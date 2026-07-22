"""Normalized results + the machine-readable JSON contract.

Every command emits exactly one JSON object on stdout. Success is a
:class:`GenerationResult`; an async submit is a :class:`JobHandle`; a poll is a
:class:`JobStatus`. Legacy keys (``path``, ``extra_paths``, ``bytes``) are kept
alongside the new ``artifacts[]`` for one release so existing Skills keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1


@dataclass
class Artifact:
    path: str
    kind: str  # image | video | frame
    mime: str | None = None
    bytes: int = 0
    role: str | None = None

    @classmethod
    def from_path(cls, path: Path | str, kind: str, *, mime: str | None = None, role: str | None = None) -> "Artifact":
        p = Path(path)
        return cls(str(p), kind, mime=mime, bytes=(p.stat().st_size if p.is_file() else 0), role=role)

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "mime": self.mime, "bytes": self.bytes, "role": self.role}


@dataclass
class GenerationResult:
    modality: str
    operation: str
    provider: str
    model: str | None
    artifacts: list[Artifact]
    usage: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def primary(self) -> Artifact:
        return self.artifacts[0]

    def to_dict(self) -> dict:
        primary = self.artifacts[0] if self.artifacts else None
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "modality": self.modality,
            "operation": self.operation,
            "provider": self.provider,
            "model": self.model,
            "artifacts": [a.to_dict() for a in self.artifacts],
            # --- compatibility aliases (one release) ---
            "kind": self.modality,
            "path": primary.path if primary else None,
            "bytes": primary.bytes if primary else 0,
            "extra_paths": [a.path for a in self.artifacts[1:]],
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

    def to_dict(self) -> dict:
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "kind": self.modality,
            "modality": self.modality,
            "provider": self.provider,
            "model": self.model,
            "job": {"provider": self.provider, "model": self.model, "id": self.id},
            "task_id": self.id,  # compat alias
            "output": self.output,
            "poll": f"media-ai job query --provider {self.provider} --id {self.id} --output {self.output}",
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
            out.update({"kind": rd["kind"], "path": rd["path"], "bytes": rd["bytes"], "artifacts": rd["artifacts"],
                        "extra_paths": rd["extra_paths"], "usage": rd["usage"], "meta": rd["meta"]})
        if self.raw:
            out["raw"] = self.raw
        return out
