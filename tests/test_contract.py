"""Provider contract tests — one suite every adapter must satisfy.

Parametrized over every (provider, model) the registry exposes. Checks that
capability descriptors are well-formed and serializable, that declared modalities
match their caps objects, and that capability-gated validation actually rejects an
unsupported request (proving discovery and validation stay in sync per provider).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import CATALOG
from media_ai.core.capabilities import UnsupportedPolicy, validate_request
from media_ai.core.registry import load_adapter_class
from media_ai.core.errors import MediaError
from media_ai.core.types import ImageRequest, Modality, Operation, SpeechRequest, VideoRequest


def _adapter(provider: str):
    """Instantiate an adapter the way the registry does, minus a binding."""
    return load_adapter_class(CATALOG.providers[provider].adapter)()


def _all_models():
    """Every (provider, wire model id) the manifests declare.

    Driven by the manifests rather than by asking each adapter what it supports, so a
    binding whose declaration and implementation disagree fails here.
    """
    return [(b.provider, b.model_id) for b in CATALOG.all()]


ALL = _all_models()


@pytest.mark.parametrize("provider,model", ALL, ids=[f"{p}:{m}" for p, m in ALL])
def test_capabilities_wellformed_and_serializable(provider, model):
    prov = _adapter(provider)
    caps = prov.capabilities(model)
    import json

    json.dumps(caps.to_dict())  # serializable
    assert caps.provider == provider and caps.model == model
    assert caps.modalities, "a model must declare at least one modality"
    # modality <-> caps object consistency
    assert (Modality.IMAGE in caps.modalities) == (caps.image is not None)
    assert (Modality.VIDEO in caps.modalities) == (caps.video is not None)
    assert (Modality.AUDIO in caps.modalities) == (caps.audio is not None)


@pytest.mark.parametrize("provider,model", ALL, ids=[f"{p}:{m}" for p, m in ALL])
def test_declared_operations_are_known(provider, model):
    caps = _adapter(provider).capabilities(model)
    for sub in (caps.image, caps.video, caps.audio):
        if sub is not None:
            for op in sub.operations:
                assert isinstance(op, Operation)


@pytest.mark.parametrize("provider,model", ALL, ids=[f"{p}:{m}" for p, m in ALL])
def test_unsupported_option_is_rejected(provider, model):
    caps = _adapter(provider).capabilities(model)
    if caps.image is not None:
        req = ImageRequest(prompt="x", output=Path("o.png"), options={"__definitely_not_a_real_option__": 1})
        with pytest.raises(MediaError):
            validate_request(req, caps, UnsupportedPolicy.ERROR)
    elif caps.video is not None:
        req = VideoRequest(prompt="x", output=Path("o.mp4"), options={"__definitely_not_a_real_option__": 1})
        with pytest.raises(MediaError):
            validate_request(req, caps, UnsupportedPolicy.ERROR)
    elif caps.audio is not None:
        req = SpeechRequest(text="x", output=Path("o.mp3"), options={"__definitely_not_a_real_option__": 1})
        with pytest.raises(MediaError):
            validate_request(req, caps, UnsupportedPolicy.ERROR)


@pytest.mark.parametrize("provider,model", ALL, ids=[f"{p}:{m}" for p, m in ALL])
def test_video_models_are_async_with_job_semantics(provider, model):
    caps = _adapter(provider).capabilities(model)
    if caps.video is not None and caps.video.operations:
        # Every video model that *generates* is job-based (submit -> poll -> finalize).
        # A backend with no generation operations is exempt: local ffmpeg returns the
        # finished file, and there is no job to poll.
        assert caps.video.is_async is True


