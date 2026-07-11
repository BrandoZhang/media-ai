"""Provider contract tests — one suite every adapter must satisfy.

Parametrized over every (provider, model) the registry exposes. Checks that
capability descriptors are well-formed and serializable, that declared modalities
match their caps objects, and that capability-gated validation actually rejects an
unsupported request (proving discovery and validation stay in sync per provider).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from media_ai.core import registry
from media_ai.core.capabilities import UnsupportedPolicy, validate_request
from media_ai.core.errors import MediaError
from media_ai.core.types import ImageRequest, Modality, Operation, VideoRequest


def _all_models():
    out = []
    for name in registry.provider_names():
        prov = registry.get_provider(name)
        for model in prov.models():
            out.append((name, model))
    return out


ALL = _all_models()


@pytest.mark.parametrize("provider,model", ALL, ids=[f"{p}:{m}" for p, m in ALL])
def test_capabilities_wellformed_and_serializable(provider, model):
    prov = registry.get_provider(provider)
    caps = prov.capabilities(model)
    import json

    json.dumps(caps.to_dict())  # serializable
    assert caps.provider == provider and caps.model == model
    assert caps.modalities, "a model must declare at least one modality"
    # modality <-> caps object consistency
    assert (Modality.IMAGE in caps.modalities) == (caps.image is not None)
    assert (Modality.VIDEO in caps.modalities) == (caps.video is not None)


@pytest.mark.parametrize("provider,model", ALL, ids=[f"{p}:{m}" for p, m in ALL])
def test_declared_operations_are_known(provider, model):
    caps = registry.get_provider(provider).capabilities(model)
    for sub in (caps.image, caps.video):
        if sub is not None:
            for op in sub.operations:
                assert isinstance(op, Operation)


@pytest.mark.parametrize("provider,model", ALL, ids=[f"{p}:{m}" for p, m in ALL])
def test_unsupported_option_is_rejected(provider, model):
    caps = registry.get_provider(provider).capabilities(model)
    if caps.image is not None:
        req = ImageRequest(prompt="x", output=Path("o.png"), options={"__definitely_not_a_real_option__": 1})
        with pytest.raises(MediaError):
            validate_request(req, caps, UnsupportedPolicy.ERROR)
    elif caps.video is not None:
        req = VideoRequest(prompt="x", output=Path("o.mp4"), options={"__definitely_not_a_real_option__": 1})
        with pytest.raises(MediaError):
            validate_request(req, caps, UnsupportedPolicy.ERROR)


@pytest.mark.parametrize("provider,model", ALL, ids=[f"{p}:{m}" for p, m in ALL])
def test_video_models_are_async_with_job_semantics(provider, model):
    caps = registry.get_provider(provider).capabilities(model)
    if caps.video is not None:
        # every video model in this system is job-based (submit -> poll -> finalize)
        assert caps.video.is_async is True


def test_registry_infers_provider_from_model_id():
    assert registry.provider_for_model("gpt-image-2") == "openai"
    assert registry.provider_for_model("veo-3.0-generate-001") == "gemini"
    assert registry.provider_for_model("gemini-3.1-flash-image") == "gemini"
    assert registry.provider_for_model("doubao-seedance-2-0-260128") == "volc"
    assert registry.provider_for_model("totally-unknown") is None
