"""Picking a binding: ``--binding``, ``--provider``/``--model``, or the scene default.

Three ways to say which binding to use, in decreasing precision::

    --binding volc-ark/seedance-2.0        # exact
    --provider volc-ark --model seedance-2.0
    --model seedance-2.0                   # if only one configured binding serves it
    (nothing)                              # the [defaults] entry for this scene

The last form is why this module exists. ``--provider`` and ``--model`` are the kind
of flags a model calling the CLI may or may not have learned, so a bare invocation
has to work — that is a convenience, and it is the *only* automatic choice made
here.

**Nothing falls back.** A binding that is missing, ambiguous, or does not serve the
scene raises; it never resolves to a second choice. Choosing again after a failure is
the caller's decision, not this CLI's — the agent driving it knows what the run is
for. What this module owes such a caller is a refusal it can act on, so every error
carries a stable ``code``, the candidates it was choosing between, and a command
that fixes it.

Bindings that need no credential (``local/ffmpeg``, ``mock``) are usable without
appearing in the config: there is nothing to configure. Everything else must be
configured, because "configured" and "has a key" are the same statement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..credentials.reference import BindingCredentials
from .binding import AuthKind, BindingCatalog, BindingSpec, ProviderSpec, Transport
from .config import Config, UserBinding, load_config
from .errors import ErrorCategory, MediaError
from .scene import Scene

__all__ = ["ResolvedBinding", "available_bindings", "resolve"]


@dataclass(frozen=True)
class ResolvedBinding:
    """A binding ready to call: what it can do, where it lives, and how to sign in."""

    id: str
    spec: BindingSpec
    """The declaration — scenes and constraints. Comes from the manifest, possibly
    reached through ``extends``, which is why it is not always ``catalog.get(id)``."""
    provider: ProviderSpec
    model_id: str
    base_url: str | None = None
    credential: str | None = None
    options: dict = field(default_factory=dict)
    configured: bool = False
    """False for a binding usable without configuration (no credential required)."""

    @property
    def scenes(self):
        return self.spec.scenes

    def credentials(self) -> BindingCredentials:
        return BindingCredentials(self.credential, provider=self.provider.name)

    def check_scene(self, scene: Scene, available: list["ResolvedBinding"]) -> None:
        """Refuse a scene this binding does not serve, naming ones that do.

        ``alternatives`` lists only bindings the caller can actually reach right now,
        so it is a next step rather than a catalogue excerpt.
        """
        if scene in self.spec.scenes:
            return
        alternatives = [b.id for b in available if scene in b.spec.scenes and b.id != self.id]
        raise MediaError(
            f"binding {self.id!r} does not support {scene.value}",
            category=ErrorCategory.UNSUPPORTED,
            code="scene_not_supported",
            provider=self.provider.name,
            model=self.spec.model,
            details={
                "binding": self.id,
                "scene": scene.value,
                "supported_scenes": sorted(s.value for s in self.spec.scenes),
                "alternatives": alternatives,
                "hint": _hint_for_scene(scene, alternatives),
            },
        )


def _hint_for_scene(scene: Scene, alternatives: list[str]) -> str:
    if alternatives:
        return f"re-run with --binding {alternatives[0]}"
    return f"no configured binding serves {scene.value}; run `media-ai bindings available` to see what could"


# --------------------------------------------------------------------------
# what is reachable
# --------------------------------------------------------------------------


def _spec_for(user: UserBinding, catalog: BindingCatalog) -> BindingSpec | None:
    """The declaration behind a configured binding: its own, or the one it extends."""
    if user.extends:
        return catalog.get(user.extends)
    return catalog.get(user.id)


def _resolved(
    bid: str, spec: BindingSpec, provider: ProviderSpec, user: UserBinding | None
) -> ResolvedBinding:
    return ResolvedBinding(
        id=bid,
        spec=spec,
        provider=provider,
        model_id=(user.model_id if user and user.model_id else spec.model_id),
        base_url=(user.base_url if user and user.base_url else provider.base_url.default),
        credential=(user.credential if user else None),
        options=dict(user.options) if user else {},
        configured=user is not None,
    )


def available_bindings(catalog: BindingCatalog, config: Config) -> list[ResolvedBinding]:
    """Every binding this machine can call right now, sorted by id.

    Two sources, and the difference is the credential: a declared binding whose
    provider needs no key is always available, while a credentialed one appears only
    once the config names its key. A user-defined binding (one with ``extends``)
    appears because it was configured.
    """
    out: dict[str, ResolvedBinding] = {}

    for spec in catalog.all():
        provider = catalog.providers[spec.provider]
        if provider.auth.kind is AuthKind.NONE:
            out[spec.id] = _resolved(spec.id, spec, provider, config.bindings.get(spec.id))

    for bid, user in config.bindings.items():
        spec = _spec_for(user, catalog)
        if spec is None:
            raise MediaError(
                f'config declares [bindings."{bid}"] but nothing declares what it is; '
                f"add `extends = \"<known binding>\"` or remove it",
                category=ErrorCategory.CLI, code="binding_undeclared",
                details={"binding": bid, "known": catalog.ids()},
            )
        provider_name = bid.partition("/")[0]
        provider = catalog.providers.get(provider_name) or catalog.providers[spec.provider]
        out[bid] = _resolved(bid, spec, provider, user)

    return [out[k] for k in sorted(out)]


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


def resolve(
    *,
    binding: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    scene: Scene | None = None,
    catalog: BindingCatalog | None = None,
    config: Config | None = None,
) -> ResolvedBinding:
    """Return the binding to call, or raise an error that says how to fix it."""
    from .registry import catalog as default_catalog

    catalog = catalog or default_catalog()
    config = config if config is not None else load_config()
    available = available_bindings(catalog, config)

    if binding:
        return _by_id(binding, available, catalog)

    if provider or model:
        # One filter for all three shapes (--provider, --model, both). A model matches
        # by its short name, by the tail of a binding id, or by the id that goes on the
        # wire — an agent reading a vendor's docs knows `doubao-seedream-4-5-251128`,
        # not the name this project shortened it to.
        candidates = [
            b for b in available
            if (not provider or b.provider.name == provider or b.id.startswith(f"{provider}/"))
            and (not model or model in {b.spec.model, b.id.partition("/")[2], b.model_id})
        ]
        if scene and len(candidates) > 1:
            # Narrow by scene only to break a tie: `--provider gemini` for a video is
            # unambiguous even though Gemini also serves images. Never used to *widen*.
            candidates = [b for b in candidates if scene in b.spec.scenes] or candidates
        if not candidates and provider and model:
            # Nothing available matched, but the pair may name something declared and
            # unconfigured — a more useful answer than "no matching binding".
            return _by_id(f"{provider}/{model}", available, catalog)
        return _unique(
            candidates,
            what=_describe(provider, model),
            code="ambiguous_model" if model else "ambiguous_provider",
            available=available, scene=scene,
        )

    if scene is None:
        raise MediaError(
            "no binding requested and no scene to default for",
            category=ErrorCategory.CLI, code="no_binding_requested",
        )
    return _default_for(scene, available, config)


def _describe(provider: str | None, model: str | None) -> str:
    if provider and model:
        return f"provider {provider!r} + model {model!r}"
    return f"model {model!r}" if model else f"provider {provider!r}"


def _by_id(bid: str, available: list[ResolvedBinding], catalog: BindingCatalog) -> ResolvedBinding:
    for b in available:
        if b.id == bid:
            return b

    declared = catalog.get(bid)
    if declared is not None:
        provider = catalog.providers[declared.provider]
        env = provider.auth.env[0] if provider.auth.env else f"{provider.name.upper()}_API_KEY"
        raise MediaError(
            f"binding {bid!r} is not configured on this machine",
            category=ErrorCategory.AUTH, code="binding_not_configured",
            provider=declared.provider, model=declared.model,
            details={
                "binding": bid,
                "setup_hint": provider.setup_hint,
                "configured": [b.id for b in available],
                "hint": f"media-ai bindings add {bid} (or set credential = \"env://{env}\" in the config)",
            },
        )
    raise MediaError(
        f"unknown binding {bid!r}",
        category=ErrorCategory.NOT_FOUND, code="unknown_binding",
        details={
            "binding": bid,
            "declared": catalog.ids(),
            "hint": "media-ai bindings available",
        },
    )


def _unique(
    candidates: list[ResolvedBinding], *, what: str, code: str,
    available: list[ResolvedBinding], scene: Scene | None,
) -> ResolvedBinding:
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise MediaError(
            f"no configured binding for {what}",
            category=ErrorCategory.NOT_FOUND, code="no_matching_binding",
            details={
                "configured": [b.id for b in available],
                "hint": "media-ai bindings available",
            },
        )
    ids = [b.id for b in candidates]
    raise MediaError(
        f"{what} is served by {len(ids)} configured bindings; say which one",
        category=ErrorCategory.CLI, code=code,
        details={
            "candidates": ids,
            "scene": scene.value if scene else None,
            "hint": f"re-run with --binding {ids[0]}",
        },
    )


def _default_for(scene: Scene, available: list[ResolvedBinding], config: Config) -> ResolvedBinding:
    wanted = config.default_for(scene)
    if wanted:
        for b in available:
            if b.id == wanted:
                return b
        raise MediaError(
            f"the default binding for {scene.value} is {wanted!r}, which is not configured",
            category=ErrorCategory.CLI, code="default_binding_missing",
            details={
                "scene": scene.value, "binding": wanted,
                "configured": [b.id for b in available],
                "hint": f"media-ai config set-default {scene.value} <binding>",
            },
        )

    serving = [b.id for b in available if scene in b.spec.scenes]
    raise MediaError(
        f"no binding configured for {scene.value}",
        category=ErrorCategory.CLI, code="no_default_binding",
        details={
            "scene": scene.value,
            "available": serving,
            "hint": (
                f"media-ai config set-default {scene.value} {serving[0]}"
                if serving
                else "media-ai init"
            ),
        },
    )


def transport_of(rb: ResolvedBinding) -> Transport:
    return rb.provider.transport
