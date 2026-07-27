"""What the packaged Agent Skills are, and which providers a selection of them needs.

``media-ai init`` is skill-first: a user picks the things they want to do ("generate
images") rather than the vendors they want to configure. Two questions follow from
that, and this module answers both: *what can be picked* — each skill's blurb, its
install tier, and what it drags in with it, read from the skill's own frontmatter —
and *what a pick costs*, i.e. the set of providers then worth asking about.

The provider mapping is **derived, never hardcoded** — :data:`~media_ai.core.types.Operation`
and each model's ``ModelCapabilities`` are the single source of truth (the same rule
``media-ai capabilities`` follows), so a new provider or a new model shows up here
without touching this file.

Two consequences fall out of deriving it, both load-bearing for the wizard's size:

- A skill maps to providers by **union, not product**. Credentials live per provider
  in one flat namespace, so picking image + video + speech asks for four keys, not
  3x2x2 combinations. Four is also the ceiling: it is how many credentialed
  providers exist.
- Skills with no matching operation contribute nothing. ``media-ai-concat`` runs on
  local ffmpeg, ``media-ai-capabilities``/``-usage`` are offline, ``media-ai-shared``
  is documentation, and ``media-ai-job`` polls a job some *other* skill created — so
  none of them widens the credential ask. That falls out of the derivation rather
  than needing a maintained exclusion list.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

from ..core import registry
from ..core.logging import get_logger
from ..core.types import Operation
from ._frontmatter import parse as parse_frontmatter

__all__ = [
    "SKILL_PREFIX",
    "SkillInfo",
    "available_skills",
    "core_skills",
    "operations_for_skill",
    "provider_matrix",
    "providers_for_skills",
    "resolve_selection",
    "selectable_skills",
    "skill_info",
    "skill_root",
]

SKILL_PREFIX = "media-ai-"

#: Install tiers, declared per skill in ``metadata.install.tier``.
#:
#: ``core``
#:     Always installed. The shared contract, discovery, and cost accounting are not
#:     a preference — every other skill assumes them — so offering them as a choice
#:     only invites a selection that half-works.
#: ``optional``
#:     A genuine choice: a thing the user may or may not want to do.
#: ``dependency``
#:     Never offered on its own; pulled in by whatever declares it in
#:     ``metadata.install.needs``.
TIERS = ("core", "optional", "dependency")
DEFAULT_TIER = "optional"


@lru_cache(maxsize=None)
def available_skills() -> tuple[str, ...]:
    """Skill directory names shipped inside the package, sorted.

    Cached: the answer cannot change within a process, and it is asked once per
    `needs` edge during resolution and once per scanned root in `doctor` — each time
    an `iterdir()` of a resource directory that may live inside a zip.
    """
    root = files("media_ai") / "skills"
    return tuple(sorted(p.name for p in root.iterdir() if p.is_dir() and p.name.startswith(SKILL_PREFIX)))


def skill_root(skill: str):
    """The packaged directory for one skill (a ``Traversable``, not necessarily a Path)."""
    return files("media_ai") / "skills" / skill


def operations_for_skill(skill: str) -> frozenset[Operation]:
    """Operations a skill drives, from its ``media-ai-<group>`` name.

    Returns an empty set for skills that drive no provider operation at all; callers
    treat that as "needs no credentials" rather than as an error, because that is
    exactly what it means for concat/capabilities/usage/shared/job.
    """
    group = skill[len(SKILL_PREFIX) :] if skill.startswith(SKILL_PREFIX) else skill
    return frozenset(op for op in Operation if op.value.split(".", 1)[0] == group)


# ------------------------------------------------------------------ self-description


@dataclass(frozen=True)
class SkillInfo:
    """What a skill says about itself, for the installer's benefit.

    Read from the skill's own ``SKILL.md`` frontmatter rather than a table kept here,
    so a skill added to ``skills/`` arrives complete — its blurb, its tier, and what
    it drags in — without an edit anywhere else.
    """

    name: str
    summary: str  #: one human-readable paragraph; the agent-facing `description` is not it
    tier: str  #: one of :data:`TIERS`
    needs: tuple[str, ...]  #: other skills that must be installed alongside it


@lru_cache(maxsize=None)
def skill_info(skill: str) -> SkillInfo:
    """Parse one skill's ``metadata.install`` block.

    Every field degrades: a skill with no ``install`` block is a plain optional one
    described by the first sentence of the ``description`` an agent reads. That keeps
    a hand-dropped third-party skill directory installable instead of invisible.
    """
    meta = _frontmatter(skill)
    install = meta.get("install") if isinstance(meta.get("install"), dict) else {}
    tier = str(install.get("tier") or DEFAULT_TIER)
    needs = install.get("needs") or ()
    return SkillInfo(
        name=skill,
        summary=str(install.get("summary") or "").strip() or _first_sentence(meta.get("description")),
        tier=tier if tier in TIERS else DEFAULT_TIER,
        needs=tuple(str(n) for n in needs if isinstance(n, str)),
    )


def _frontmatter(skill: str) -> dict:
    """``metadata`` plus ``description`` from a packaged SKILL.md, flattened."""
    try:
        text = (skill_root(skill) / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return {}
    front = parse_frontmatter(text)
    meta = front.get("metadata")
    out = dict(meta) if isinstance(meta, dict) else {}
    out["description"] = front.get("description")
    return out


def _first_sentence(description: object) -> str:
    """The lead sentence of an agent-facing description, as a fallback blurb.

    Those descriptions are written for skill *matching* — they trail off into "use
    when asked to …" keyword lists — so only the opening claim is worth showing to a
    human choosing from a menu.
    """
    if not isinstance(description, str) or not description.strip():
        return ""
    head = description.strip().split(". ", 1)[0].rstrip(".")
    return f"{head}." if head else ""


def core_skills() -> list[str]:
    """Skills installed unconditionally, in package order."""
    return [s for s in available_skills() if skill_info(s).tier == "core"]


def selectable_skills() -> list[str]:
    """Skills worth putting in front of a user as a choice.

    Everything else is either assumed by the rest (``core``) or dragged in by
    something that was chosen (``dependency``) — asking about those produces
    selections that only half-work, not informed consent.
    """
    return [s for s in available_skills() if skill_info(s).tier == "optional"]


def resolve_selection(picked: list[str]) -> tuple[list[str], dict[str, str]]:
    """Expand a user's choice into the set that actually gets installed.

    Returns the full sorted list plus ``{skill: why}`` for everything added on the
    user's behalf, so the wizard can say what it did rather than quietly writing more
    directories than were asked for.
    """
    chosen = set(picked)
    reasons: dict[str, str] = {}
    for skill in core_skills():
        if skill not in chosen:
            chosen.add(skill)
            reasons[skill] = "always installed"

    # Breadth-first over `needs`, and only ever *adding*: a cycle or a self-reference
    # terminates because a skill already in `chosen` is never queued again.
    shipped = set(available_skills())
    queue = list(chosen)
    while queue:
        skill = queue.pop()
        for need in skill_info(skill).needs:
            if need in chosen or need not in shipped:
                continue
            chosen.add(need)
            reasons[need] = f"needed by {skill}"
            queue.append(need)
    return sorted(chosen), reasons


def provider_matrix() -> dict[Operation, dict[str, tuple[str, ...]]]:
    """``{operation: {provider: (model, …)}}`` over every credentialed provider.

    ``mock`` and anything else declaring ``requires_credentials = False`` is left out:
    it is a real provider but never something to ask a user for a key for.

    A provider that fails to introspect is logged and skipped rather than aborting
    discovery — one broken adapter should not stop the wizard from configuring the
    rest, which is the same tradeoff ``cli/capabilities.py`` makes.
    """
    matrix: dict[Operation, dict[str, list[str]]] = {}
    for name in registry.provider_names():
        try:
            prov = registry.get_provider(name)
            if not getattr(prov, "requires_credentials", True):
                continue
            for model in prov.models():
                caps = prov.capabilities(model)
                for block in (caps.image, caps.video, caps.audio):
                    if block is None:
                        continue
                    for op in block.operations:
                        matrix.setdefault(op, {}).setdefault(name, []).append(model)
        except Exception as exc:  # noqa: BLE001 - a misconfigured provider shouldn't hide the rest
            get_logger().warning("could not introspect provider %s: %s", name, exc)
    return {op: {prov: tuple(models) for prov, models in provs.items()} for op, provs in matrix.items()}


def providers_for_skills(skills: list[str]) -> dict[str, list[str]]:
    """``{provider: [skill, …]}`` — which providers to ask about, and what each unlocks.

    The skill list is what the wizard shows beside each provider so a user declining
    one can see what they give up. Providers are returned in a stable sorted order.
    """
    matrix = provider_matrix()
    served: dict[str, set[str]] = {}
    for skill in skills:
        for op in operations_for_skill(skill):
            for provider in matrix.get(op, {}):
                served.setdefault(provider, set()).add(skill)
    return {prov: sorted(served[prov]) for prov in sorted(served)}
