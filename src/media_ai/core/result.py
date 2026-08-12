"""Normalized results + the machine-readable JSON contract.

Every command emits exactly one JSON object on stdout. Success is a
:class:`GenerationResult`; an async submit is a :class:`JobHandle`; a poll is a
:class:`JobStatus`.

**Every produced file is an entry in ``artifacts[]``** — there is no second, flatter
way to say the same thing. The pre-release shapes carried ``path``/``bytes``/
``extra_paths``/``kind`` alongside it, which meant a consumer could read the primary
artifact two ways and the extras only one, and a caller that took the short path
silently ignored every artifact after the first (``--count 3``, a timestamps sidecar,
a returned last frame). One list, always.

What produced a result is recorded in ``meta`` — ``meta.binding`` and ``meta.scene``,
stamped by :func:`media_ai.cli.common.stamp`. The scene is not restated as a
top-level field: only the CLI derives it, and a value an adapter has to repeat is a
value an adapter can get wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 2


def error_payload(err) -> dict:
    """The failure half of the machine contract: ``{"ok": false, "schema_version", "error"}``.

    Every stdout object carries ``schema_version`` — a consumer that validates against
    one schema should not need a second branch for the failure shape, and a failure is
    exactly when a caller is least able to guess what it is looking at. Both writers of
    a failure object (``cli.common`` and the group dispatcher in ``__main__``) build it
    here so the two cannot drift apart.
    """
    return {"ok": False, "schema_version": SCHEMA_VERSION, "error": err.to_dict()}


@dataclass
class Artifact:
    path: str
    kind: str  # image | video | frame
    mime: str | None = None
    bytes: int = 0
    role: str | None = None

    @classmethod
    def from_path(cls, path: Path | str, kind: str, *, mime: str | None = None, role: str | None = None) -> Artifact:
        p = Path(path)
        return cls(str(p), kind, mime=mime, bytes=(p.stat().st_size if p.is_file() else 0), role=role)

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "mime": self.mime, "bytes": self.bytes, "role": self.role}


@dataclass
class GenerationResult:
    modality: str
    provider: str
    model: str | None
    artifacts: list[Artifact]
    usage: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def primary(self) -> Artifact:
        return self.artifacts[0]

    def to_dict(self) -> dict:
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "modality": self.modality,
            "provider": self.provider,
            "model": self.model,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "usage": self.usage,
            "meta": self.meta,
        }


@dataclass
class JobHandle:
    """Returned by an async ``video generate --wait false`` submit."""

    provider: str
    model: str | None
    id: str
    output: str
    modality: str = "video"
    status: str = "queued"
    meta: dict = field(default_factory=dict)
    binding: str | None = None
    """Which binding submitted this. The poll command has to name it: a provider may
    serve several bindings, and `--provider gemini` alone would be ambiguous between
    Veo, Nano Banana and Gemini TTS."""

    def to_dict(self) -> dict:
        target = f"--binding {self.binding}" if self.binding else f"--provider {self.provider}"
        job = {"provider": self.provider, "model": self.model, "id": self.id}
        if self.binding:
            job["binding"] = self.binding
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "modality": self.modality,
            "provider": self.provider,
            "model": self.model,
            "job": job,
            "output": self.output,
            "poll": f"media-ai job query {target} --id {self.id} --output {self.output}",
            "meta": self.meta,
        }


@dataclass
class JobStatus:
    """Returned by ``job query`` / ``job cancel``. If the job finished and an
    output path was given, ``result`` carries the finalized artifacts."""

    provider: str
    model: str | None
    id: str
    status: str  # queued | running | succeeded | failed | cancelled | expired
    op: str = "query"
    result: GenerationResult | None = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "op": self.op,
            "provider": self.provider,
            "model": self.model,
            "id": self.id,
            "status": self.status,
        }
        if self.result is not None:
            rd = self.result.to_dict()
            out.update({"modality": rd["modality"], "artifacts": rd["artifacts"],
                        "usage": rd["usage"], "meta": rd["meta"]})
        if self.raw:
            out["raw"] = self.raw
        return out
