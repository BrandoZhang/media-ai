"""What the packaged Agent Skills are, and which providers a selection of them needs.

``init`` is skill-first: a user picks the things they want to do ("generate
images") rather than the vendors they want to configure. Two questions follow from
that, and this module answers both: *what can be picked* — each skill's blurb, its
install tier, and what it drags in with it, read from the skill's own frontmatter —
and *what a pick costs*, i.e. the set of providers then worth asking about.

The mapping is **derived, never hardcoded** — a skill's name gives its command
group, the group gives its :class:`~media_ai.core.scene.Scene` set, and the manifests
say which bindings serve those scenes. So a new binding appears in setup without
touching this file, which is the same rule the ``capabilities`` command follows.

Two consequences fall out of deriving it, both load-bearing for the wizard's size:

- A skill maps to bindings by **union, not product**. Picking image + video + speech
  asks about every binding serving any of those scenes, once each — not once per
  combination.
- Skills driving no scene contribute nothing. The ``capabilities``/``usage`` skills are
  offline, ``shared`` is documentation, and ``job`` polls a job some *other* skill
  created — so none of them widens the credential ask. That falls
  out of the derivation rather than needing a maintained exclusion list. So does the
  free local backend: ``video.concat`` is served by a binding with
  ``auth.kind = "none"``, and nothing with nothing to ask gets asked about.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from ..brand import skill_name, skill_prefix
from ..core.config import Config
from ..core.registry import catalog
from ..core.scene import Scene, scenes_for_group
from ._frontmatter import parse as parse_frontmatter
from ._render import render

__all__ = [
    "SkillInfo",
    "available_skills",
    "bindings_for_skills",
    "core_skills",
    "group_of",
    "packaged_groups",
    "scenes_for_skill",
    "resolve_selection",
    "selectable_skills",
    "skill_info",
    "skill_root",
]

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


@cache
def packaged_groups() -> tuple[str, ...]:
    """The command groups the package ships a skill for, sorted — ``("animation", …)``.

    The packaged directories are named for the group alone (``skills/image/``), not for
    the installed skill (``media-ai-image``), so that the brand enters the tree in
    exactly one place: :func:`media_ai.brand.skill_name`. A packaged directory carrying
    the name would be a second copy of it, and one that no rename could reach.

    Cached rather than :func:`available_skills`, which is a pure function of this and of
    the brand: caching the branded names would make the cache go stale the moment a test
    patches the brand, for an `iterdir()` this already avoids repeating.
    """
    root = files("media_ai") / "skills"
    return tuple(sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()))


def available_skills() -> tuple[str, ...]:
    """Installed skill directory names shipped inside the package, sorted.

    Branded — ``media-ai-image``, or ``<brand>-image`` for a renamed build. This is the
    name a skill has on disk and the one every other function here takes.
    """
    return tuple(skill_name(g) for g in packaged_groups())


def skill_root(skill: str):
    """The packaged directory for one skill (a ``Traversable``, not necessarily a Path).

    Takes the *installed* (branded) name and maps it back to the packaged group
    directory, so callers never have to hold both forms.
    """
    return files("media_ai") / "skills" / group_of(skill)


def group_of(skill: str) -> str:
    prefix = skill_prefix()
    return skill[len(prefix):] if skill.startswith(prefix) else skill


def scenes_for_skill(skill: str) -> frozenset[Scene]:
    """Scenes a skill drives, from its ``<brand>-<group>`` name.

    Empty for a skill that drives no generation at all; callers read that as "needs no
    credential" rather than as an error, because that is exactly what it means for
    capabilities/usage/shared/job. Deriving it is what keeps the wizard honest — a new
    skill or a new binding shows up here without an edit.
    """
    return scenes_for_group(group_of(skill))


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


@cache
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
    """``metadata`` plus ``description`` from a packaged SKILL.md, flattened.

    Rendered before parsing, exactly as the installed copy will be: a ``needs:`` edge
    reads ``{{skill}}job``, and :func:`resolve_selection` matches it against the branded
    names from :func:`available_skills`. Parsing the raw template instead would leave
    every dependency unresolvable — silently, since an unknown ``need`` is skipped as a
    third-party name rather than raised on.
    """
    try:
        text = render((skill_root(skill) / "SKILL.md").read_text(encoding="utf-8"))
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


def bindings_for_skills(skills: list[str], config: Config | None = None) -> dict[str, list[str]]:
    """``{binding id: [skill, …]}`` — which bindings to offer, and what each unlocks.

    Offered per *binding* rather than per provider, because that is the unit a
    credential attaches to now: picking three Seedream models means three entries, and
    the wizard asks about each. The skill list beside each one is what a user declining
    it gives up.

    Bindings needing no credential are left out — there is nothing to ask about a
    local backend, and offering one alongside a question about API keys is what made
    the old wizard treat two very different decisions identically.
    """
    from ..core.binding import AuthKind

    cat = catalog()
    wanted: set[Scene] = set()
    per_scene: dict[Scene, set[str]] = {}
    for skill in skills:
        for scene in scenes_for_skill(skill):
            wanted.add(scene)
            per_scene.setdefault(scene, set()).add(skill)

    served: dict[str, set[str]] = {}
    for spec in cat.all():
        if cat.providers[spec.provider].auth.kind is AuthKind.NONE:
            continue
        for scene in spec.scenes & wanted:
            served.setdefault(spec.id, set()).update(per_scene[scene])
    return {bid: sorted(served[bid]) for bid in sorted(served)}

