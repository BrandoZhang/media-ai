"""Custom-backend extensibility: a manifest plus an adapter, added without touching core.

A backend is two things — a **manifest** declaring what it can do, and an **adapter**
implementing how to call it. These prove both halves can come from outside the
package: in-process, and through the ``media_ai.bindings`` entry point.

The RPC case is the one that shapes the design. An internal platform reached over
Thrift or gRPC has no base URL, no header, and no HTTP status codes to map, so it
cannot be expressed by declaring request bodies. Declaring *capabilities* while the
adapter stays code is what lets it register on exactly the same terms as Ark or Veo —
scene checks, validation, credential injection, retry and result shape all apply.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PNG_1x1_BYTES

from media_ai import Adapter, Artifact, GenerationResult, MediaError, register_manifest
from media_ai.core import registry
from media_ai.core.config import Config, UserBinding
from media_ai.core.resolve import available_bindings, resolve
from media_ai.core.scene import Scene
from media_ai.core.types import ImageRequest
from media_ai.core.validate import validate_request

ACME_MANIFEST = """
[provider]
name       = "acme"
title      = "Acme Imaging"
transport  = "http"
adapter    = "test_extensions:AcmeAdapter"
setup_hint = "Get a key at acme.test"

[provider.auth]
kind   = "api_key"
header = "Authorization"
scheme = "Bearer"
env    = ["ACME_API_KEY"]

[provider.base_url]
default = "https://api.acme.test/v1"

[[binding]]
id       = "acme/acme-pro"
model    = "acme-pro"
model_id = "acme-pro-2026"
scenes   = ["image.text_to_image"]

[binding.constraints]
options = ["sticker"]

[binding.constraints.supports]
seed = true
"""


class AcmeAdapter(Adapter):
    """A minimal offline custom adapter with a provider-specific option.

    Note how little it declares: the scenes it implements, and the wire. What the
    model can do lives in the manifest, so there is no second copy here to drift.
    """

    def supported_scenes(self):
        return frozenset({Scene.IMAGE_TEXT_TO_IMAGE})

    def generate_image(self, req: ImageRequest) -> GenerationResult:
        out = Path(req.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(PNG_1x1_BYTES)
        return GenerationResult(modality="image", provider=self.name,
                                model=req.model, artifacts=[Artifact.from_path(out, "image", mime="image/png")],
                                usage={}, meta={"sticker": req.options.get("sticker")})


def _configured(binding_id="acme/acme-pro", credential="env://ACME_API_KEY") -> Config:
    return Config(bindings={binding_id: UserBinding(id=binding_id, credential=credential)})


# ----------------------------------------------------------------- in-process


def test_a_registered_manifest_becomes_resolvable(clean_registry):
    register_manifest(ACME_MANIFEST, source="acme")
    spec = registry.catalog().get("acme/acme-pro")
    assert spec is not None
    assert spec.model_id == "acme-pro-2026"
    assert Scene.IMAGE_TEXT_TO_IMAGE in spec.scenes

    rb = resolve(binding="acme/acme-pro", config=_configured())
    assert rb.provider.adapter == "test_extensions:AcmeAdapter"
    assert rb.model_id == "acme-pro-2026"


def test_a_custom_backend_runs_end_to_end(clean_registry, tmp_path, monkeypatch):
    monkeypatch.setenv("ACME_API_KEY", "acme-secret-123456")
    register_manifest(ACME_MANIFEST, source="acme")
    rb = resolve(binding="acme/acme-pro", config=_configured())
    adapter = registry.build_adapter(rb)

    req = ImageRequest(prompt="a fox", output=tmp_path / "fox.png", model=rb.model_id,
                       seed=3, options={"sticker": "holiday"})
    validate_request(req, rb.spec.constraints)  # the provider-specific option is allowed
    res = adapter.generate_image(req)
    assert Path(res.primary().path).is_file()
    assert res.meta["sticker"] == "holiday"


def test_an_undeclared_option_is_still_rejected(clean_registry, tmp_path):
    register_manifest(ACME_MANIFEST, source="acme")
    rb = resolve(binding="acme/acme-pro", config=_configured())
    req = ImageRequest(prompt="x", output=tmp_path / "o.png", options={"not_a_real_option": 1})
    with pytest.raises(MediaError):
        validate_request(req, rb.spec.constraints)


def test_a_scene_the_binding_does_not_declare_is_refused(clean_registry, tmp_path):
    """The manifest says text-to-image only, so an edit is refused before any call."""
    register_manifest(ACME_MANIFEST, source="acme")
    config = _configured()
    rb = resolve(binding="acme/acme-pro", config=config)
    with pytest.raises(MediaError) as ei:
        rb.check_scene(Scene.IMAGE_IMAGE_TO_IMAGE, available_bindings(registry.catalog(), config))
    assert ei.value.code == "scene_not_supported"
    assert "image.text_to_image" in ei.value.details["supported_scenes"]


def test_unregistering_removes_it(clean_registry):
    register_manifest(ACME_MANIFEST, source="acme")
    assert registry.catalog().get("acme/acme-pro") is not None
    registry.unregister_manifest("acme")
    assert registry.catalog().get("acme/acme-pro") is None


# --------------------------------------------------------------- entry points


def test_entry_point_discovery(clean_registry, monkeypatch):
    """A plugin exposed via ``media_ai.bindings`` is found with no change to the host."""
    import importlib.metadata as md

    class _EP:
        name = "acme"

        def load(self):
            return ACME_MANIFEST

    monkeypatch.setattr(md, "entry_points", lambda *, group=None: [_EP()] if group == "media_ai.bindings" else [])
    registry.reset_catalog()

    assert registry.catalog().get("acme/acme-pro") is not None


def test_a_broken_plugin_is_skipped_not_fatal(clean_registry, monkeypatch):
    import importlib.metadata as md

    class _BadEP:
        name = "broken"

        def load(self):
            raise RuntimeError("plugin import boom")

    monkeypatch.setattr(md, "entry_points", lambda *, group=None: [_BadEP()] if group == "media_ai.bindings" else [])
    registry.reset_catalog()

    cat = registry.catalog()
    assert cat.get("mock/mock") is not None, "one broken plugin took the whole catalog down"


def test_a_manifest_that_does_not_parse_fails_at_registration(clean_registry):
    """Loudly, and where the mistake is — not at some later lookup."""
    from media_ai.core.binding import ManifestError

    with pytest.raises(ManifestError):
        register_manifest(ACME_MANIFEST.replace('scenes   = ["image.text_to_image"]', 'scenes = ["image.telepathy"]'))


# ------------------------------------------------------------------ non-HTTP


RPC_MANIFEST = """
[provider]
name      = "rpc"
title     = "An internal RPC platform"
transport = "rpc"
adapter   = "test_extensions:RpcAdapter"

[provider.auth]
kind = "custom"
env  = ["RPC_API_KEY"]

[[binding]]
id       = "rpc/rpc-pro"
model    = "rpc-pro"
model_id = "rpc-pro-v1"
scenes   = ["image.text_to_image"]

[binding.constraints.supports]
seed = true
"""


class Transient(Exception):
    pass


class FakeStub:
    def __init__(self):
        self.calls = 0
        self.seen_key = None

    def generate(self, *, prompt, api_key):
        self.calls += 1
        self.seen_key = api_key
        if self.calls == 1:
            raise Transient("try again")
        return b"\x89PNG-rpc-bytes"


class RpcAdapter(Adapter):
    def __init__(self, binding):
        super().__init__(binding)
        self.stub = FakeStub()

    def supported_scenes(self):
        return frozenset({Scene.IMAGE_TEXT_TO_IMAGE})

    def generate_image(self, req):
        from media_ai import retry

        key = self.credential().reveal()
        data = retry(lambda: self.stub.generate(prompt=req.prompt, api_key=key),
                     retryable=lambda e: isinstance(e, Transient))
        req.output.parent.mkdir(parents=True, exist_ok=True)
        req.output.write_bytes(data)
        return GenerationResult(modality="image", provider=self.name,
                                model=req.model, artifacts=[Artifact.from_path(req.output, "image")], usage={})


def test_an_rpc_backend_is_first_class(clean_registry, tmp_path, monkeypatch):
    """No HTTP anywhere: no base URL, no header, no status codes — and it still binds.

    This is the case a manifest describing request bodies could not have served, and
    the reason wire mapping stayed in code.
    """
    from media_ai import HttpAdapter
    from media_ai.core import retry as retry_mod

    monkeypatch.setattr(retry_mod.time, "sleep", lambda *a, **k: None)  # no real backoff
    monkeypatch.setenv("RPC_API_KEY", "rpc-secret-abcdef")
    register_manifest(RPC_MANIFEST, source="rpc")

    rb = resolve(binding="rpc/rpc-pro", config=_configured("rpc/rpc-pro", "env://RPC_API_KEY"))
    assert rb.provider.transport.value == "rpc"
    assert rb.base_url is None, "an RPC provider is not given an HTTP endpoint"

    adapter = registry.build_adapter(rb)
    assert not isinstance(adapter, HttpAdapter)

    req = ImageRequest(prompt="a fox", output=tmp_path / "rpc.png", model=rb.model_id, seed=1)
    validate_request(req, rb.spec.constraints)
    res = adapter.generate_image(req)

    assert Path(res.primary().path).is_file()
    # retried once, and the credential reached the stub without the framework seeing it
    assert adapter.stub.calls == 2
    assert adapter.stub.seen_key == "rpc-secret-abcdef"
