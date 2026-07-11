"""Custom-provider extensibility: in-process registration + entry-point discovery.

Proves a third party can add a backend (and its capability schema + a
provider-specific option) without editing core, and that the registry, routing,
capability discovery, and validation all pick it up.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PNG_1x1_BYTES
from media_ai import (
    Artifact,
    GenerationResult,
    ImageCaps,
    MediaError,
    Modality,
    ModelCapabilities,
    Operation,
    Provider,
    register_provider,
    unregister_provider,
)
from media_ai.core import registry
from media_ai.core.capabilities import validate_request
from media_ai.core.types import ImageRequest


class AcmeProvider(Provider):
    """A minimal offline custom provider with a provider-specific option."""

    name = "acme"
    requires_credentials = False
    model_hints = ("acme-",)

    def models(self):
        return ["acme-fast", "acme-pro"]

    def default_model(self, modality):
        return "acme-pro"

    def capabilities(self, model=None):
        return ModelCapabilities(
            provider=self.name, model=model or "acme-pro", modalities=frozenset({Modality.IMAGE}),
            image=ImageCaps(operations=frozenset({Operation.IMAGE_GENERATE}), max_count=2,
                            supports_seed=True, options=("sticker",)),
        )

    def generate_image(self, req: ImageRequest) -> GenerationResult:
        out = Path(req.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(PNG_1x1_BYTES)
        return GenerationResult(modality="image", operation=req.operation.value, provider=self.name,
                                model=req.model, artifacts=[Artifact.from_path(out, "image", mime="image/png")],
                                usage={}, meta={"sticker": req.options.get("sticker")})


@pytest.fixture
def clean_registry():
    """Snapshot and restore the module-global registry around a test."""
    saved = dict(registry._REGISTRY)
    saved_flags = (registry._BUILTINS_LOADED, registry._ENTRYPOINTS_LOADED)
    try:
        yield
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)
        registry._BUILTINS_LOADED, registry._ENTRYPOINTS_LOADED = saved_flags


def test_in_process_registration_and_routing(clean_registry):
    register_provider("acme", lambda **kw: AcmeProvider(**kw), model_hints=("acme-",))
    assert "acme" in registry.provider_names()
    # a bare model id routes to the provider via its hint
    assert registry.provider_for_model("acme-fast") == "acme"
    prov, model = registry.build(model="acme-fast", modality=Modality.IMAGE)
    assert isinstance(prov, AcmeProvider) and model == "acme-fast"


def test_custom_provider_end_to_end(clean_registry, tmp_path):
    register_provider("acme", lambda **kw: AcmeProvider(**kw), model_hints=("acme-",))
    prov, model = registry.build(provider="acme", modality=Modality.IMAGE)
    req = ImageRequest(prompt="a fox", output=tmp_path / "fox.png", model=model, seed=3, options={"sticker": "holiday"})
    validate_request(req, prov.capabilities(model))  # provider-specific option is allowed
    res = prov.generate_image(req)
    assert Path(res.primary().path).is_file() and res.meta["sticker"] == "holiday"


def test_unsupported_option_on_custom_provider_is_rejected(clean_registry, tmp_path):
    register_provider("acme", lambda **kw: AcmeProvider(**kw))
    prov = registry.get_provider("acme")
    req = ImageRequest(prompt="x", output=tmp_path / "o.png", options={"not_a_real_option": 1})
    with pytest.raises(MediaError):
        validate_request(req, prov.capabilities("acme-pro"))


def test_unregister(clean_registry):
    register_provider("acme", lambda **kw: AcmeProvider(**kw))
    assert registry.is_registered("acme")
    unregister_provider("acme")
    assert not registry.is_registered("acme")


def test_entry_point_discovery(clean_registry, monkeypatch):
    """A plugin exposed via the ``media_ai.providers`` entry-point group is
    discovered without any code change to the host."""
    import importlib.metadata as md

    class _EP:
        name = "acme"

        def load(self):
            return AcmeProvider

    def fake_entry_points(*, group=None):
        return [_EP()] if group == "media_ai.providers" else []

    monkeypatch.setattr(md, "entry_points", fake_entry_points)
    # force re-discovery
    registry._REGISTRY.clear()
    registry._BUILTINS_LOADED = False
    registry._ENTRYPOINTS_LOADED = False

    assert "acme" in registry.provider_names()
    prov = registry.get_provider("acme")
    assert isinstance(prov, AcmeProvider)
    assert registry.provider_for_model("acme-pro") == "acme"  # hint from the class attr


def test_rpc_only_provider_is_first_class(clean_registry, tmp_path, monkeypatch):
    """A provider with NO HTTP — a fake RPC stub — plugs in via the same
    Provider interface: credential injection, error mapping, and the shared
    retry helper all work, and it flows through registry + validation + result."""
    from media_ai import ErrorCategory, retry
    from media_ai.core import retry as retry_mod

    monkeypatch.setattr(retry_mod.time, "sleep", lambda *a, **k: None)  # no real backoff

    class Transient(Exception):
        pass

    class Unauthorized(Exception):
        pass

    class FakeStub:
        def __init__(self):
            self.calls = 0
            self.seen_key = None

        def generate(self, *, prompt, api_key):
            self.calls += 1
            self.seen_key = api_key
            if self.calls == 1:
                raise Transient("try again")  # transient on first attempt
            return b"\x89PNG-rpc-bytes"

    class RpcProvider(Provider):
        name = "rpc"
        model_hints = ("rpc-",)

        def __init__(self, *, credentials=None, config=None):
            super().__init__(credentials=credentials, config=config)
            self.stub = FakeStub()

        def models(self):
            return ["rpc-pro"]

        def default_model(self, m):
            return "rpc-pro"

        def capabilities(self, model=None):
            return ModelCapabilities(provider=self.name, model=model or "rpc-pro",
                modalities=frozenset({Modality.IMAGE}),
                image=ImageCaps(operations=frozenset({Operation.IMAGE_GENERATE}), supports_seed=True))

        def generate_image(self, req):
            key = self.credential().reveal()
            try:
                data = retry(lambda: self.stub.generate(prompt=req.prompt, api_key=key),
                             retryable=lambda e: isinstance(e, Transient))
            except Unauthorized as e:
                raise MediaError(str(e), category=ErrorCategory.AUTH, provider=self.name) from e
            req.output.parent.mkdir(parents=True, exist_ok=True)
            req.output.write_bytes(data)
            return GenerationResult(modality="image", operation=req.operation.value, provider=self.name,
                                    model=req.model, artifacts=[Artifact.from_path(req.output, "image")], usage={})

    # give it a credential the same way every provider gets one
    monkeypatch.setenv("RPC_API_KEY", "rpc-secret-abcdef")
    register_provider("rpc", lambda **kw: RpcProvider(**kw), model_hints=("rpc-",))

    prov, model = registry.build(model="rpc-pro", modality=Modality.IMAGE)
    assert not isinstance(prov, __import__("media_ai").HttpProvider)  # genuinely non-HTTP
    req = ImageRequest(prompt="a fox", output=tmp_path / "rpc.png", model=model, seed=1)
    validate_request(req, prov.capabilities(model))
    res = prov.generate_image(req)
    assert Path(res.primary().path).is_file()
    assert prov.stub.calls == 2 and prov.stub.seen_key == "rpc-secret-abcdef"  # retried once, key injected


def test_broken_plugin_is_skipped_not_fatal(clean_registry, monkeypatch):
    import importlib.metadata as md

    class _BadEP:
        name = "broken"

        def load(self):
            raise RuntimeError("plugin import boom")

    monkeypatch.setattr(md, "entry_points", lambda *, group=None: [_BadEP()] if group == "media_ai.providers" else [])
    registry._REGISTRY.clear()
    registry._BUILTINS_LOADED = False
    registry._ENTRYPOINTS_LOADED = False

    # the built-ins must still be available despite the broken plugin
    names = registry.provider_names()
    assert "mock" in names and "broken" not in names
