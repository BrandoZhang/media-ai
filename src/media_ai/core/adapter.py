"""The :class:`Adapter` interface — one class per provider, implementing its wire.

An adapter is constructed from a :class:`~media_ai.core.resolve.ResolvedBinding` and
nothing else. Everything it may know about *where* it is calling comes from there: the
endpoint, the id that goes on the wire, per-binding options, and a credential source
scoped to that binding alone.

That is the whole point of the constructor. Adapters used to reach for the environment
themselves — Ark read five variables, Gemini seven — so a binding's behaviour was
assembled from its config *plus* whatever the shell happened to hold, and "why is this
one timing out at 900 seconds?" could not be answered from the config that named it.
One binding, one place.

The class is named for what it is. ``Provider`` now means the API surface a manifest
declares; the code that speaks to it is the adapter. Keeping the old name would leave
one word meaning two things an import apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..credentials.secret import Credential
from .errors import ErrorCategory, MediaError
from .scene import Scene
from .types import (
    DialogueRequest,
    ImageRequest,
    JobRef,
    MusicPlanRequest,
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)

if TYPE_CHECKING:  # a runtime import would close the loop resolve -> registry -> adapter
    from .binding import Constraints
    from .resolve import ResolvedBinding


class Adapter:
    """Base class for a generation backend.

    Subclasses override the operations they support and declare which scenes those
    cover. An operation nothing implements raises a deterministic ``UNSUPPORTED``
    error — though in practice the pre-flight scene check gets there first, because
    the manifest already said what this binding serves.
    """

    def __init__(self, binding: ResolvedBinding) -> None:
        self.binding = binding

    # ---- what the binding says -------------------------------------------

    @property
    def name(self) -> str:
        """The provider name, taken from the manifest rather than restated here.

        A hardcoded copy would be a second source of truth for something that already
        has one, and it lands in every result and error — so drift would surface as a
        lie in the output rather than as a failing import.
        """
        return self.binding.provider.name

    @property
    def model_id(self) -> str:
        """What goes on the wire — the id the API accepts, not the model's short name."""
        return self.binding.model_id

    @property
    def base_url(self) -> str:
        return (self.binding.base_url or "").rstrip("/")

    @property
    def constraints(self) -> Constraints:
        return self.binding.spec.constraints

    def option(self, key: str, default=None):
        """A per-binding knob from the config (poll intervals, org ids, …).

        Distinct from ``req.options``, which is what *this call* asked for. These
        belong to the binding and are the same on every call through it.
        """
        return self.binding.options.get(key, default)

    def float_option(self, key: str, default: float) -> float:
        try:
            return float(self.option(key, default))
        except (TypeError, ValueError):
            return default

    def int_option(self, key: str, default: int) -> int:
        try:
            return int(self.option(key, default))
        except (TypeError, ValueError):
            return default

    # ---- credentials -----------------------------------------------------

    def credential(self) -> Credential:
        """Resolve this binding's credential, per call, so rotation needs no restart."""
        return self.binding.credentials().resolve()

    # ---- accounting ------------------------------------------------------

    def record(self, scene: Scene | str | None, **fields) -> None:
        """Append one usage line for a call through this binding.

        The binding id, provider and wire model are filled in here rather than at each
        call site, so no adapter can write a ledger line that fails to say which
        configured binding was billed — the question the ledger exists to answer.

        ``scene`` is ``None`` on the one path that cannot know it: finalizing a job
        submitted by an *earlier* process (``media-ai job query``), where the inputs
        that implied the scene are long gone. The key is then absent rather than
        guessed — a wrong scene in a cost report is worse than a missing one.
        """
        from .usage import record_usage

        entry = {"binding": self.binding.id, "provider": self.name,
                 "model": fields.pop("model", None) or self.model_id}
        if scene is not None:
            entry["scene"] = scene.value if isinstance(scene, Scene) else scene
        record_usage({**entry, **fields})

    # ---- declaration -----------------------------------------------------

    def supported_scenes(self) -> frozenset[Scene]:  # pragma: no cover - interface
        """Scenes this adapter actually implements.

        Checked against every manifest naming it (``tests/test_manifests.py``), so a
        binding cannot advertise a scene whose code was never written — the failure a
        declarative manifest invites, caught where it is cheap.
        """
        raise NotImplementedError

    def honoured_flags(self) -> frozenset[str]:
        """``constraints.supports.*`` names whose ``true`` this adapter acts on.

        The same guard as :meth:`supported_scenes`, one level down. A manifest can
        declare any flag it likes; only a flag some code path reads is a capability.
        Ark's Seedream 5.0 declared ``grounding = true`` — a flag only the Gemini
        adapter implements — so discovery advertised web search on a binding whose
        request builder has never heard of it, and the caller's `--option` was rejected.

        Flags the *core* gates on every binding (``seed``, ``negative_prompt``,
        geometry and reference limits, …) are validated before an adapter is reached and
        need no entry here; this covers the ones an adapter has to opt into. The empty
        default means "declares nothing provider-specific", which is the safe answer.
        """
        return frozenset()

    # ---- operations (override the supported ones) ------------------------

    def generate_image(self, req: ImageRequest):
        raise self._unsupported("image generation")

    def generate_video(self, req: VideoRequest):
        raise self._unsupported("video generation")

    def generate_speech(self, req: SpeechRequest):
        raise self._unsupported("speech generation")

    def generate_dialogue(self, req: DialogueRequest):
        raise self._unsupported("dialogue generation")

    def generate_music(self, req: MusicRequest):
        raise self._unsupported("music generation")

    def generate_music_plan(self, req: MusicPlanRequest):
        raise self._unsupported("composition-plan generation")

    def generate_sound(self, req: SoundEffectRequest):
        raise self._unsupported("sound-effect generation")

    def get_job(self, ref: JobRef, *, output: Path | None = None):
        raise self._unsupported("async jobs")

    def cancel_job(self, ref: JobRef):
        raise self._unsupported("job cancellation")

    def _unsupported(self, what: str) -> MediaError:
        return MediaError(
            f"binding {self.binding.id!r} does not support {what}",
            category=ErrorCategory.UNSUPPORTED,
            provider=self.name,
            model=self.model_id,
        )
