"""Declarative model catalogue — what each provider supports, and its lifecycle.

Adapters used to classify a model id with string tests scattered through their
capability methods (``"tts" in m``, ``m.startswith("veo")``, ``"lite" in m``). That
works until it doesn't: an id that matches no pattern silently inherits some default
tier's capabilities, and there is nowhere to record that a model was retired, or when
anyone last checked it against the live API. Those are data questions, so they live in
data here rather than in control flow.

A :class:`Catalog` answers three things the string tests could not:

- **Lifecycle.** ``status`` distinguishes generally-available from preview, deprecated,
  and removed. A removed model raises a specific error naming its replacement instead
  of needing a bespoke ``_imagen_removed()`` helper per provider.
- **Provenance.** ``verified`` records when a model was last exercised against the real
  API. ``None`` means *never verified* and is reported as such — the field exists so
  that gap is visible rather than assumed away.
- **Fallbacks that are deliberate.** An unknown id still resolves, but through an
  explicitly-declared fallback spec rather than by falling off the end of an if-chain.

The catalogue holds per-model *data*; adapters still assemble the
:class:`~media_ai.core.capabilities.ModelCapabilities`, whose shape is provider-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import ErrorCategory, MediaError

__all__ = ["ModelStatus", "ModelSpec", "Catalog"]


class ModelStatus(str, Enum):
    """Where a model sits in its lifecycle."""

    GA = "ga"
    """Generally available; the default."""

    PREVIEW = "preview"
    """Usable but the provider may change or withdraw it without notice."""

    DEPRECATED = "deprecated"
    """Still callable, but on its way out — ``replacement`` names what to move to."""

    REMOVED = "removed"
    """No longer callable. Resolving one raises rather than sending a doomed request."""


@dataclass(frozen=True)
class ModelSpec:
    """One model's identity, lifecycle, and per-model capability data."""

    id: str

    status: ModelStatus = ModelStatus.GA

    aliases: tuple[str, ...] = ()
    """Other ids that resolve to exactly this spec (dated snapshots, vendor synonyms)."""

    matches: tuple[str, ...] = ()
    """Id *prefixes* this spec claims when nothing matches exactly."""

    contains: tuple[str, ...] = ()
    """Id *substrings* this spec claims. Needed because a vendor family is not always
    a prefix: Google's TTS ids are ``gemini-2.5-flash-preview-tts``, where the only
    reliable marker is ``tts`` in the middle. A prefix-only scheme sends every
    unrecognised one to whichever spec claims ``gemini-``.

    Both are checked in catalogue order, so declare narrow specs before broad ones.
    """

    replacement: str | None = None
    """What to use instead. Required in spirit for ``DEPRECATED``/``REMOVED``."""

    reason: str | None = None
    """Why it is deprecated or removed; surfaced in the error and in notes."""

    verified: str | None = None
    """ISO date this model's behaviour was last checked against the live API.

    ``None`` means never verified. It is reported honestly rather than defaulted to
    something reassuring — an unverified model is exactly what a caller wants to know
    about before depending on it.
    """

    synthetic: bool = False
    """Not a real vendor model id — a declared fallback that catches ids nothing else
    claims. Kept out of every listing: it answers "what happens to an id I don't
    recognise", which is a resolution rule, not a model anyone can call."""

    discoverable: bool = True
    """Whether ``models()`` lists it. Deprecated snapshots usually still resolve via
    ``--model`` but are kept out of discovery so they aren't offered to new callers."""

    notes: tuple[str, ...] = ()
    """Human-readable notes merged into the model's capabilities."""

    caps: dict = field(default_factory=dict)
    """Per-model capability data the owning adapter interprets (sizes, durations,
    option names…). Opaque here on purpose: the *shape* is provider-specific, only the
    *data* is catalogued."""

    @property
    def is_usable(self) -> bool:
        return self.status is not ModelStatus.REMOVED

    def lifecycle_notes(self) -> tuple[str, ...]:
        """Notes describing status and provenance, for merging into capabilities."""
        out: list[str] = []
        if self.status is ModelStatus.DEPRECATED:
            msg = f"deprecated{f': {self.reason}' if self.reason else ''}"
            if self.replacement:
                msg += f" — prefer {self.replacement}"
            out.append(msg)
        elif self.status is ModelStatus.PREVIEW:
            out.append("preview — the provider may change or withdraw this without notice")
        out.append(
            f"last verified against the live API on {self.verified}"
            if self.verified
            else "not verified against the live API"
        )
        return tuple(out)


class Catalog:
    """An ordered set of :class:`ModelSpec` for one provider."""

    def __init__(self, provider: str, specs: tuple[ModelSpec, ...], *, fallback: str | None = None):
        self.provider = provider
        self.specs = specs
        self._by_id: dict[str, ModelSpec] = {}
        for spec in specs:
            for key in (spec.id, *spec.aliases):
                if key.lower() in self._by_id:
                    raise ValueError(f"{provider}: duplicate model id {key!r} in catalogue")
                self._by_id[key.lower()] = spec
        self._fallback = fallback
        if fallback is not None and fallback.lower() not in self._by_id:
            raise ValueError(f"{provider}: fallback {fallback!r} is not in the catalogue")

    # -- lookup ------------------------------------------------------------

    def get(self, model: str) -> ModelSpec | None:
        """Resolve exactly, then by declared prefix or substring, then to the fallback.

        ``None`` when nothing claims it and no fallback is declared.
        """
        if not model:
            return self.default_spec()
        key = model.lower()
        exact = self._by_id.get(key)
        if exact is not None:
            return exact
        for spec in self.specs:
            if any(key.startswith(p.lower()) for p in spec.matches):
                return spec
            if any(c.lower() in key for c in spec.contains):
                return spec
        return self.default_spec()

    def default_spec(self) -> ModelSpec | None:
        return self._by_id.get(self._fallback.lower()) if self._fallback else None

    def require(self, model: str) -> ModelSpec:
        """Resolve, refusing a removed model with an error naming its replacement.

        Raising here is the point: a removed id would otherwise produce a confusing
        error from the provider — or worse, be silently treated as something else.
        """
        spec = self.get(model)
        if spec is None:
            raise MediaError(
                f"unknown model {model!r} for provider {self.provider!r}",
                category=ErrorCategory.NOT_FOUND, provider=self.provider, model=model,
            )
        if spec.status is ModelStatus.REMOVED:
            detail = f" ({spec.reason})" if spec.reason else ""
            hint = f"; use {spec.replacement}" if spec.replacement else ""
            raise MediaError(
                f"model {model!r} is no longer supported{detail}{hint}",
                category=ErrorCategory.UNSUPPORTED, provider=self.provider, model=model,
            )
        return spec

    # -- discovery ---------------------------------------------------------

    def discoverable_ids(self) -> list[str]:
        """Ids ``models()`` should advertise: real, usable, and not withheld."""
        return [s.id for s in self.specs if s.discoverable and s.is_usable and not s.synthetic]

    def real_ids(self) -> list[str]:
        """Every catalogued vendor model, including deprecated and removed ones."""
        return [s.id for s in self.specs if not s.synthetic]

    def ids_with_status(self, status: ModelStatus) -> list[str]:
        return [s.id for s in self.specs if s.status is status and not s.synthetic]

    def unverified_ids(self) -> list[str]:
        """Usable models with no recorded live-API verification."""
        return [s.id for s in self.specs if s.is_usable and not s.verified and not s.synthetic]


def apply_spec(caps, spec: ModelSpec | None):
    """Stamp lifecycle and provenance from ``spec`` onto assembled capabilities.

    Adapters build the capability object — its shape is theirs — then hand it here so
    status, replacement, verification, and notes land identically everywhere instead
    of each adapter remembering to do it.
    """
    if spec is None:
        return caps
    caps.status = spec.status.value
    caps.replacement = spec.replacement
    caps.verified = spec.verified
    caps.experimental = spec.status is ModelStatus.PREVIEW
    caps.notes = tuple(caps.notes) + spec.notes + spec.lifecycle_notes()
    if spec.aliases:
        caps.aliases = tuple(caps.aliases) + spec.aliases
    return caps
