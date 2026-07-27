"""Work out which providers a set of Agent Skills actually needs credentials for.

``media-ai init`` is skill-first: a user picks the things they want to do ("generate
images") rather than the vendors they want to configure. This turns that selection
into the set of providers worth asking about.

The mapping is **derived, never hardcoded** — :data:`~media_ai.core.types.Operation`
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

from importlib.resources import files

from ..core import registry
from ..core.logging import get_logger
from ..core.types import Operation

__all__ = [
    "SKILL_PREFIX",
    "available_skills",
    "operations_for_skill",
    "provider_matrix",
    "providers_for_skills",
]

SKILL_PREFIX = "media-ai-"


def available_skills() -> list[str]:
    """Skill directory names shipped inside the package, sorted."""
    root = files("media_ai") / "skills"
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name.startswith(SKILL_PREFIX))


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
