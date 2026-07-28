"""Binding manifests — what each ``(provider, model)`` pair is, declared as data.

A **binding** is the unit of integration: one callable ``(provider, model)`` pair.
``volc-ark/seedance-2.0`` and ``heygen/seedance-2.0`` are two bindings, not one
model with a switch, because everything that matters about them differs — the wire
format, the scenes they serve, the parameter limits, the credential.

This module holds the *declaration*: scenes, constraints, lifecycle, how to
authenticate, and which adapter implements the wire. It holds no wire logic at all.
That split is deliberate. A manifest that also described the request mapping would
have to express Ark's create→poll→cancel with billed-task cancellation, Veo's
long-running operation plus an authenticated file download, OpenAI's multipart
edits, ElevenLabs' sidecar endpoints — and an internal Thrift client, which is not
HTTP at all. A format that could express those is a programming language, and a bad
one. So: **declare the capabilities, code the wire.**

What the declaration buys is that four consumers stop drifting apart, because they
read the same file:

* ``media-ai capabilities`` reports it,
* ``validate_request`` gates on it before any network call,
* ``media-ai init`` builds its questions from it — no hand-maintained model table,
* the packaged skills generate their per-binding parameter tables from it.

Manifests ship as ``src/media_ai/bindings/<provider>.toml``, one file per provider.
A third party adds bindings with a ``media_ai.bindings`` entry point resolving to a
manifest path; a broken one is logged and skipped rather than breaking the CLI,
while a broken built-in raises (the test suite is what catches it).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from importlib.resources import files
from typing import Any

from .errors import ErrorCategory, MediaError
from .scene import Scene

__all__ = [
    "AuthKind",
    "AuthSpec",
    "BaseUrlSpec",
    "BindingSpec",
    "Constraints",
    "Lifecycle",
    "ManifestError",
    "ProviderSpec",
    "Transport",
    "BindingCatalog",
    "load_manifest",
    "builtin_catalog",
]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ADAPTER_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


class ManifestError(MediaError):
    """A manifest is malformed. Always a bug in the manifest, never in a request."""

    def __init__(self, message: str, *, source: str | None = None) -> None:
        super().__init__(f"{source}: {message}" if source else message, category=ErrorCategory.CLI)


class Transport(str, Enum):
    HTTP = "http"
    RPC = "rpc"
    """Anything that is not HTTP — gRPC, Thrift, a vendor SDK. The framework assumes
    nothing: no base URL, no HTTP client, no status-code error mapping. The adapter
    builds its own connection from the resolved binding."""
    LOCAL = "local"
    """Runs on this machine. No network, no credential, no cost — bundled ffmpeg
    (``local/ffmpeg``) and the offline ``mock``. Modelling these as bindings rather
    than as special cases is what lets "does this need a key?" stay derived
    (``auth.kind == "none"``) instead of declared per skill."""


class AuthKind(str, Enum):
    API_KEY = "api_key"
    NONE = "none"
    CUSTOM = "custom"
    """The adapter interprets the credential itself. For RPC backends whose handshake
    is not a header."""


class Lifecycle(str, Enum):
    GA = "ga"
    PREVIEW = "preview"
    """Usable, but the provider may change or withdraw it without notice."""
    DEPRECATED = "deprecated"
    """Still callable; ``replacement`` names what to move to."""


# There is deliberately no ``REMOVED``. A model that cannot be called is deleted from
# the manifest outright — keeping a tombstone means keeping the code that explains it,
# and the honest answer to "why doesn't this id work?" is that nothing declares it.


@dataclass(frozen=True)
class AuthSpec:
    kind: AuthKind = AuthKind.API_KEY
    header: str | None = None
    scheme: str | None = None
    """Prefix inside the header value, e.g. ``Bearer``. Absent means the raw key."""
    env: tuple[str, ...] = ()
    """Conventional environment variables, offered by the wizard as one credential
    source. Nothing reads these implicitly — a binding's ``credential`` reference
    always says where its key comes from."""

    @property
    def needs_credential(self) -> bool:
        return self.kind is not AuthKind.NONE


@dataclass(frozen=True)
class BaseUrlSpec:
    default: str | None = None
    configurable: bool = False
    """Whether a binding may override it — regional residency endpoints, a gateway,
    a private deployment."""


@dataclass(frozen=True)
class ProviderSpec:
    """One API surface: how to reach it and who implements it."""

    name: str
    title: str = ""
    transport: Transport = Transport.HTTP
    adapter: str = ""
    """``module:Class`` implementing the wire. Imported lazily, so a manifest for a
    provider whose adapter lives in another package costs nothing until it is used."""
    auth: AuthSpec = field(default_factory=AuthSpec)
    base_url: BaseUrlSpec = field(default_factory=BaseUrlSpec)
    docs: str | None = None
    setup_hint: str | None = None
    """One line the wizard shows before asking for a credential — where to get a key,
    what has to be enabled first."""


@dataclass(frozen=True)
class Geometry:
    mode: str = "none"  # pixels | aspect_ratio | both | none
    named_sizes: tuple[str, ...] = ()  # image tiers: 1K, 2K, 4K
    resolutions: tuple[str, ...] = ()  # video tiers: 720p, 1080p
    aspect_ratios: tuple[str, ...] = ()
    pixel_sizes: tuple[str, ...] = ()  # exact allowed WxH when fixed-enum
    pixel_multiple: int | None = None
    pixel_max_edge: int | None = None
    pixel_total: tuple[int, int] | None = None
    """``(min, max)`` on width×height. ``0`` for either end means unbounded — used
    where a provider documents one side and leaves the other to the API."""
    ratio_range: tuple[float, float] | None = None
    max_edge_ratio: float | None = None


@dataclass(frozen=True)
class Output:
    formats: tuple[str, ...] = ()
    max_count: int = 1
    max_total_images: int | None = None
    """Ceiling on *references plus outputs* together, where a provider budgets them
    jointly (Seedream 4.5 / 5.0 lite: ≤ 15). Neither a count nor a reference limit
    alone can express it."""


@dataclass(frozen=True)
class References:
    """Limits on what may be sent *in*. Output constraints have always been declared;
    input constraints were not, so an oversized reference image cost a round trip and
    a 400 to discover."""

    max: int = 0
    formats: tuple[str, ...] = ()
    max_bytes: int | None = None
    max_pixels: int | None = None
    min_edge: int | None = None
    ratio_range: tuple[float, float] | None = None


@dataclass(frozen=True)
class Video:
    durations: tuple[int, ...] = ()
    is_async: bool = True


@dataclass(frozen=True)
class Audio:
    voices: tuple[str, ...] = ()
    default_voice: str | None = None
    formats: tuple[str, ...] = ()
    max_dialogue_voices: int = 0
    max_characters: int | None = None
    duration_ms: tuple[int, int] | None = None  # music
    duration_s: tuple[float, float] | None = None  # sound effects


@dataclass(frozen=True)
class Constraints:
    supports: dict[str, bool] = field(default_factory=dict)
    """Capability flags that do **not** change the input roles, so they are not
    scenes: ``seed``, ``audio``, ``negative_prompt``, ``interactive_edit``. Read by
    validators and reported by ``capabilities``."""
    options: tuple[str, ...] = ()
    """Provider-specific ``--option`` keys this binding accepts. Anything not listed
    is rejected before the call."""
    geometry: Geometry = field(default_factory=Geometry)
    output: Output = field(default_factory=Output)
    references: References = field(default_factory=References)
    video: Video = field(default_factory=Video)
    audio: Audio = field(default_factory=Audio)

    def supports_flag(self, name: str) -> bool:
        return bool(self.supports.get(name, False))


@dataclass(frozen=True)
class BindingSpec:
    """One ``(provider, model)`` integration, as declared."""

    id: str  # "<provider>/<model>"
    provider: str
    model: str
    model_id: str
    """What goes on the wire. Distinct from ``model`` because the id a vendor accepts
    is dated and account-specific (``doubao-seedream-5-0-pro-260628``) while the name
    people use is not."""
    title: str = ""
    aliases: tuple[str, ...] = ()
    scenes: frozenset[Scene] = frozenset()
    lifecycle: Lifecycle = Lifecycle.GA
    replacement: str | None = None
    reason: str | None = None
    verified: str | None = None
    """ISO date this binding was last exercised against the live API. ``None`` means
    never, and is reported as such. Filling it in with a plausible date would convert
    "unknown" into "confirmed", which is the one thing this field must never do."""
    constraints: Constraints = field(default_factory=Constraints)
    usage_unit: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def groups(self) -> frozenset[str]:
        return frozenset(s.group for s in self.scenes)

    def serves(self, scene: Scene) -> bool:
        return scene in self.scenes


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def _table(data: Any, key: str, source: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ManifestError(f"[{key}] must be a table", source=source)
    return value


def _str_tuple(data: dict, key: str, source: str) -> tuple[str, ...]:
    value = data.get(key, ())
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ManifestError(f"{key} must be a list of strings", source=source)
    return tuple(str(v) for v in value)


def _pair(data: dict, key: str, source: str, cast=int) -> tuple | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ManifestError(f"{key} must be a two-element list [min, max]", source=source)
    return (cast(value[0]), cast(value[1]))


def _enum(cls, raw: str, key: str, source: str):
    try:
        return cls(raw)
    except ValueError:
        allowed = ", ".join(m.value for m in cls)
        raise ManifestError(f"{key} = {raw!r} is not one of: {allowed}", source=source) from None


def _parse_provider(data: dict, source: str) -> ProviderSpec:
    raw = _table(data, "provider", source)
    name = str(raw.get("name") or "")
    if not name:
        raise ManifestError("[provider] needs a name", source=source)
    transport = _enum(Transport, str(raw.get("transport", "http")), "provider.transport", source)

    auth_raw = _table(raw, "auth", source)
    kind = _enum(AuthKind, str(auth_raw.get("kind", "api_key")), "provider.auth.kind", source)
    auth = AuthSpec(
        kind=kind,
        header=auth_raw.get("header"),
        scheme=auth_raw.get("scheme"),
        env=_str_tuple(auth_raw, "env", source),
    )

    url_raw = _table(raw, "base_url", source)
    base_url = BaseUrlSpec(default=url_raw.get("default"), configurable=bool(url_raw.get("configurable", False)))

    adapter = str(raw.get("adapter") or "")
    if not _ADAPTER_RE.match(adapter):
        raise ManifestError(f"provider.adapter must be 'module:Class', got {adapter!r}", source=source)
    if transport is Transport.HTTP and not base_url.default:
        raise ManifestError("an http provider needs provider.base_url.default", source=source)
    if transport is Transport.LOCAL and auth.needs_credential:
        raise ManifestError("a local provider cannot require a credential", source=source)

    return ProviderSpec(
        name=name, title=str(raw.get("title") or name), transport=transport, adapter=adapter,
        auth=auth, base_url=base_url, docs=raw.get("docs"), setup_hint=raw.get("setup_hint"),
    )


def _parse_constraints(raw: dict, source: str) -> Constraints:
    supports = _table(raw, "supports", source)
    for key, value in supports.items():
        if not isinstance(value, bool):
            raise ManifestError(f"constraints.supports.{key} must be a boolean", source=source)

    geo = _table(raw, "geometry", source)
    out = _table(raw, "output", source)
    refs = _table(raw, "references", source)
    vid = _table(raw, "video", source)
    aud = _table(raw, "audio", source)

    return Constraints(
        supports=dict(supports),
        options=_str_tuple(raw, "options", source),
        geometry=Geometry(
            mode=str(geo.get("mode", "none")),
            named_sizes=_str_tuple(geo, "named_sizes", source),
            resolutions=_str_tuple(geo, "resolutions", source),
            aspect_ratios=_str_tuple(geo, "aspect_ratios", source),
            pixel_sizes=_str_tuple(geo, "pixel_sizes", source),
            pixel_multiple=geo.get("pixel_multiple"),
            pixel_max_edge=geo.get("pixel_max_edge"),
            pixel_total=_pair(geo, "pixel_total", source),
            ratio_range=_pair(geo, "ratio_range", source, cast=float),
            max_edge_ratio=geo.get("max_edge_ratio"),
        ),
        output=Output(
            formats=_str_tuple(out, "formats", source),
            max_count=int(out.get("max_count", 1)),
            max_total_images=out.get("max_total_images"),
        ),
        references=References(
            max=int(refs.get("max", 0)),
            formats=_str_tuple(refs, "formats", source),
            max_bytes=refs.get("max_bytes"),
            max_pixels=refs.get("max_pixels"),
            min_edge=refs.get("min_edge"),
            ratio_range=_pair(refs, "ratio_range", source, cast=float),
        ),
        video=Video(
            durations=tuple(int(d) for d in vid.get("durations", ())),
            is_async=bool(vid.get("async", True)),
        ),
        audio=Audio(
            voices=_str_tuple(aud, "voices", source),
            default_voice=aud.get("default_voice"),
            formats=_str_tuple(aud, "formats", source),
            max_dialogue_voices=int(aud.get("max_dialogue_voices", 0)),
            max_characters=aud.get("max_characters"),
            duration_ms=_pair(aud, "duration_ms", source),
            duration_s=_pair(aud, "duration_s", source, cast=float),
        ),
    )


def _parse_binding(raw: dict, provider: ProviderSpec, source: str) -> BindingSpec:
    bid = str(raw.get("id") or "")
    if not _ID_RE.match(bid):
        raise ManifestError(f"binding id {bid!r} must look like '<provider>/<model>' (lowercase)", source=source)
    prov, _, model = bid.partition("/")
    if prov != provider.name:
        raise ManifestError(f"binding {bid!r} does not start with its provider {provider.name!r}", source=source)

    declared_model = str(raw.get("model") or model)
    if declared_model != model:
        raise ManifestError(f"binding {bid!r} declares model {declared_model!r}, which its id does not match", source=source)

    scenes = set()
    for name in _str_tuple(raw, "scenes", source):
        try:
            scenes.add(Scene(name))
        except ValueError:
            raise ManifestError(f"binding {bid!r}: unknown scene {name!r}", source=source) from None
    if not scenes:
        raise ManifestError(f"binding {bid!r} declares no scenes", source=source)

    lifecycle = _enum(Lifecycle, str(raw.get("lifecycle", "ga")), f"binding {bid!r} lifecycle", source)
    replacement = raw.get("replacement") or None
    if lifecycle is Lifecycle.DEPRECATED and not replacement:
        raise ManifestError(f"binding {bid!r} is deprecated and must name a replacement", source=source)

    verified = str(raw.get("verified") or "").strip() or None
    if verified is not None and not _DATE_RE.match(verified):
        raise ManifestError(f"binding {bid!r}: verified must be YYYY-MM-DD or empty, got {verified!r}", source=source)

    model_id = str(raw.get("model_id") or "")
    if not model_id and provider.transport is not Transport.LOCAL:
        raise ManifestError(f"binding {bid!r} needs a model_id (the id sent on the wire)", source=source)

    return BindingSpec(
        id=bid, provider=prov, model=model, model_id=model_id or model,
        title=str(raw.get("title") or model),
        aliases=_str_tuple(raw, "aliases", source),
        scenes=frozenset(scenes),
        lifecycle=lifecycle, replacement=replacement, reason=raw.get("reason") or None,
        verified=verified,
        constraints=_parse_constraints(_table(raw, "constraints", source), source),
        usage_unit=raw.get("usage_unit") or None,
        notes=_str_tuple(raw, "notes", source),
    )


def load_manifest(text: str, *, source: str = "<manifest>") -> tuple[ProviderSpec, list[BindingSpec]]:
    """Parse one manifest. Raises :class:`ManifestError` with ``source`` in the message."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"could not parse: {exc}", source=source) from exc

    provider = _parse_provider(data, source)
    raw_bindings = data.get("binding", [])
    if not isinstance(raw_bindings, list) or not raw_bindings:
        raise ManifestError("needs at least one [[binding]]", source=source)

    bindings = [_parse_binding(b, provider, source) for b in raw_bindings]
    seen: set[str] = set()
    for b in bindings:
        for key in (b.id, *b.aliases):
            if key in seen:
                raise ManifestError(f"duplicate binding id or alias {key!r}", source=source)
            seen.add(key)
    return provider, bindings


# --------------------------------------------------------------------------
# catalog
# --------------------------------------------------------------------------


class BindingCatalog:
    """Every declared binding, indexed the three ways callers ask for one."""

    def __init__(self) -> None:
        self.providers: dict[str, ProviderSpec] = {}
        self._bindings: dict[str, BindingSpec] = {}
        self._aliases: dict[str, str] = {}

    def add(self, provider: ProviderSpec, bindings: list[BindingSpec], *, source: str = "<manifest>") -> None:
        if provider.name in self.providers:
            raise ManifestError(f"provider {provider.name!r} is already declared", source=source)
        self.providers[provider.name] = provider
        for b in bindings:
            if b.id in self._bindings or b.id in self._aliases:
                raise ManifestError(f"duplicate binding id {b.id!r}", source=source)
            self._bindings[b.id] = b
            for alias in b.aliases:
                self._aliases[alias] = b.id

    # -- lookup ------------------------------------------------------------

    def get(self, binding_id: str) -> BindingSpec | None:
        return self._bindings.get(self._aliases.get(binding_id, binding_id))

    def ids(self) -> list[str]:
        return sorted(self._bindings)

    def all(self) -> list[BindingSpec]:
        return [self._bindings[i] for i in self.ids()]

    def provider_of(self, binding_id: str) -> ProviderSpec | None:
        spec = self.get(binding_id)
        return self.providers.get(spec.provider) if spec else None

    def for_model(self, model: str) -> list[BindingSpec]:
        """Every binding serving ``model``, across providers.

        More than one is the normal case once a model has two providers, and it is
        why a bare ``--model`` resolves only when the answer is unambiguous.
        """
        return [b for b in self.all() if b.model == model]

    def for_scene(self, scene: Scene) -> list[BindingSpec]:
        return [b for b in self.all() if b.serves(scene)]

    def for_group(self, group: str) -> list[BindingSpec]:
        return [b for b in self.all() if group in b.groups]


def _builtin_sources():
    root = files("media_ai") / "bindings"
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".toml"):
            yield entry.name, entry.read_text(encoding="utf-8")


def builtin_catalog() -> BindingCatalog:
    """The catalog of manifests shipped inside the package.

    Built-ins raise on a malformed manifest: it is a packaging bug, and the tests are
    what catch it. Third-party manifests discovered through entry points get the
    opposite treatment (logged and skipped) — that wiring lands with the registry.
    """
    catalog = BindingCatalog()
    for name, text in _builtin_sources():
        catalog.add(*load_manifest(text, source=name), source=name)
    return catalog
