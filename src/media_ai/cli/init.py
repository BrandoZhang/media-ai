"""``media-ai init`` — set up credentials, model defaults, and Agent Skills.

Skill-first: a user picks what they want to *do* ("generate images") and the wizard
derives which providers that needs. The derivation is a union rather than a product —
credentials live per provider in one flat namespace — so the credential ask is bounded
by the number of credentialed providers no matter how many skills are selected. See
:mod:`media_ai.cli._discovery`.

The machine contract still holds: every prompt is drawn on ``/dev/tty`` (see
:mod:`media_ai.cli._prompt`) and stdout carries exactly one JSON object summarising
what was written. That is also what makes the wizard usable from ``curl … | bash``,
where the pipe owns stdin.
"""

from __future__ import annotations

import argparse
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..core import registry
from ..core.errors import ErrorCategory, MediaError
from ..core.result import SCHEMA_VERSION
from ..core.types import Operation
from ..credentials import stores
from ..credentials.profile import config_path
from ..credentials.tomlwrite import atomic_write, dumps, write_private, write_public
from . import common
from ._announce import announcements
from ._discovery import (
    available_skills,
    operations_for_skill,
    providers_for_skills,
    resolve_selection,
    selectable_skills,
    skill_info,
)
from ._prompt import Cancelled, Option, get_prompter, run_steps
from ._skillstore import SKILL_DESTS, copy_skill, installed_skills, record_install, skill_is_current

# Which config keys hold a provider's default model, per skill group. These are
# genuinely separate models — Gemini's image/Veo/TTS families are disjoint, and
# ElevenLabs splits by operation — which is why one `model` per profile can't
# express them. Kept beside the adapters that read them.
MODEL_SLOTS: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("gemini", "image"): (("image_model", "image generation and editing"),),
    ("gemini", "video"): (("video_model", "video generation"),),
    ("gemini", "speech"): (("tts_model", "text-to-speech"),),
    ("volc", "image"): (("image_model", "image generation and editing"),),
    ("volc", "video"): (("video_model", "video generation"),),
    ("openai", "image"): (("image_model", "image generation and editing"),),
    ("elevenlabs", "speech"): (("model", "text-to-speech"), ("dialogue_model", "multi-voice dialogue")),
    ("elevenlabs", "music"): (("music_model", "music"),),
    ("elevenlabs", "sound"): (("sound_model", "sound effects"),),
}

CREDENTIALS_HEADER = (
    "media-ai credentials — written by `media-ai init`.\n"
    "SECRETS: keep this file chmod 600; the CLI refuses to read it otherwise.\n"
    "Each [<name>] is an account. A table named after a provider is that\n"
    "provider's default credential."
)
CONFIG_HEADER = (
    "media-ai config — written by `media-ai init`.\n"
    "NON-SECRET: safe to share. Holds routing and model defaults, never a key."
)


#: Re-exported so the wizard and everything that has to clean up after it name the
#: same file. The definition lives beside the reader in ``credentials/stores.py``.
credentials_path = stores.credentials_path


# --------------------------------------------------------------------------- io


def _load(path: Path) -> dict:
    """Parse an existing config, or return ``{}``. A broken file is an error, not
    something to silently overwrite — it may be hand-written and worth keeping."""
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MediaError(
            f"could not read existing {path}: {exc}; fix or move it, then re-run",
            category=ErrorCategory.CLI,
        ) from exc


def _backup(path: Path) -> Path | None:
    """Copy a file aside before rewriting it. Comments are not preserved by the
    writer, so the original is the only record of anything hand-written.

    Created at the *source file's* mode from the start rather than written and then
    chmod-ed: a backup of ``credentials.toml`` holds every key in it, and the second
    order leaves them in a world-readable file for as long as the write takes — and
    permanently if the process dies in between. Same reasoning as
    ``tomlwrite.atomic_write``, which is what does the work here.
    """
    if not path.is_file():
        return None
    mode = path.stat().st_mode & 0o777
    for n in range(1, 1000):
        candidate = path.with_suffix(path.suffix + f".bak{'' if n == 1 else n}")
        if not candidate.exists():
            atomic_write(candidate, path.read_text(encoding="utf-8"), mode=mode)
            return candidate
    raise MediaError(f"too many backups beside {path}", category=ErrorCategory.CLI)


# ------------------------------------------------------------------- skills


def _skill_choices(skills: list[str]) -> list[Option]:
    """One row per skill: what it costs (hint) and what it is (detail).

    A bare list of ``media-ai-*`` names is not a choice a user can make — nothing on
    it says what ``media-ai-sound`` does, or that ``media-ai-concat`` needs no key. So
    the skill's own blurb is shown beside it.
    """
    out = []
    for skill in skills:
        ops = operations_for_skill(skill)
        hint = "no credentials needed" if not ops else ", ".join(sorted(o.value for o in ops))
        out.append(Option(label=skill, hint=hint, value=skill, detail=skill_info(skill).summary))
    return out


def _report_additions(reasons: dict[str, str], prompter) -> None:
    """Say what is being installed beyond what was ticked, and why.

    Adding directories a user did not select is defensible; doing it silently is not —
    they are the ones who will find the extra folders later.
    """
    if not reasons:
        return
    width = max(len(name) for name in reasons)
    lines = "\n".join(f"  {name:<{width}}  {why}" for name, why in sorted(reasons.items()))
    prompter.note(f"Also installing:\n{lines}")


def _dest_state(path: Path) -> tuple[str, str]:
    """``(hint, sentence)`` describing what is already at ``path``.

    "exists" on its own was the wrong answer to the wrong question: it left the user
    unable to tell whether the *directory* was there or whether media-ai's skills were
    already in it — which is the difference between a first install and a refresh.
    """
    if not path.is_dir():
        return "", "Does not exist yet; it will be created."
    here = installed_skills(path)
    if here:
        return f"{len(here)} installed", f"Already holds {len(here)} media-ai skill(s), which this will bring up to date."
    return "", "The directory is there, with no media-ai skills in it yet."


#: Sentinel for the "somewhere else" row. A row rather than a follow-up question:
#: a custom path is one more place to install to, so it belongs in the same list as
#: the rest — and left unticked (the default) it costs no question at all.
CUSTOM_DEST = "custom"


def _dest_choices() -> list[Option]:
    """Every agent convention at user and project level, best guess first.

    Ordered by how likely it is to be the one wanted: directories that already exist
    (the agent is installed and in use), then the declared order of the conventions,
    then user level before project level — installing to ``~`` makes the skills work
    in every checkout, which is what someone running the wizard for the first time
    almost always means.

    Each row says which agent reads it and how far the install reaches, because the
    choice between ``~/.claude/skills`` and ``./.claude/skills`` is a real one and the
    paths alone do not make that obvious.
    """
    ranked = []
    seen: set[str] = set()
    for rank, agent in enumerate(SKILL_DESTS):
        for depth, (base, label) in enumerate(((Path.home(), "~/"), (Path.cwd(), "./"))):
            path = base / agent.segment
            # Run the wizard from your home directory and "~/…" and "./…" are the same
            # directory; offering it twice means installing to it twice, and being
            # asked to confirm an overwrite of the copy just made.
            if str(path.resolve()) in seen:
                continue
            seen.add(str(path.resolve()))
            # "current folder", not "this project": `./` is wherever the shell happens
            # to be, which is a project directory only if you started the wizard in one.
            short, long = (
                ("all projects", "every project on this machine")
                if depth == 0
                else ("current folder", f"the current folder only — {Path.cwd()}")
            )
            found, sentence = _dest_state(path)
            option = Option(
                label=f"{label}{agent.segment}",
                hint=" · ".join(x for x in (agent.name, short, found) if x),
                value=path,
                detail=f"Read by {agent.who}, for {long}. {sentence}",
            )
            ranked.append(((not path.is_dir(), rank, depth), option))
    ranked.sort(key=lambda row: row[0])
    out = [option for _key, option in ranked]
    out.append(
        Option(
            label="somewhere else…",
            hint="type a path",
            value=CUSTOM_DEST,
            detail="Tick this to be asked for a directory — any other place your agent reads skills from.",
        )
    )
    return out


def _preselected_dests(choices: list[Option]) -> list[int]:
    """Tick the directories that already exist, or the leading guess if none do.

    Pre-ticking nothing would make "press enter through the wizard" install nothing at
    all — and then report success, which is the one outcome a first run must not have.
    "Somewhere else…" is never pre-ticked: the default has to be *not* adding one, or
    entering through the wizard stops to ask for a path nobody wanted.
    """
    real = [(i, opt) for i, opt in enumerate(choices) if opt.value != CUSTOM_DEST]
    existing = [i for i, opt in real if Path(str(opt.value)).is_dir()]
    return existing or ([real[0][0]] if real else [])


def _plan_installs(skills: list[str], dests: list[Path], prompter, *, unattended: bool = False) -> list[dict]:
    """Decide what will be written where. Asks about collisions; **writes nothing**.

    Split from the writing so that every question in the wizard is answered before
    anything lands on disk — which is what makes Ctrl-C safe and "go back" safe, since
    a step that has already written cannot be re-run.

    A copy that already matches the packaged skill is neither written nor asked about.
    That is what makes a second ``install.sh`` quiet: nothing has changed, so there is
    nothing to decide.

    ``unattended`` takes the default for the one question that survives that — an
    edited copy is updated. Without it a CI upgrade that changed a packaged SKILL.md
    would stop on a prompt nothing can answer, which is the rule ``--non-interactive``
    exists to prevent.
    """
    overwrite_all = unattended
    skip_all = False
    plan = []
    for dest in dests:
        install, write, skipped = [], [], []
        for skill in skills:
            target = dest / skill
            if target.exists():
                if skill_is_current(dest, skill):
                    install.append(skill)  # already the current version: leave it be
                    continue
                if skip_all:
                    skipped.append(skill)
                    continue
                if not overwrite_all:
                    choice = prompter.select(
                        f"{target} differs from the packaged skill",
                        [Option("update it"), Option("keep mine"), Option("update all"), Option("keep all mine")],
                    )
                    if choice == 2:
                        overwrite_all = True
                    elif choice == 3:
                        skip_all = True
                        skipped.append(skill)
                        continue
                    elif choice == 1:
                        skipped.append(skill)
                        continue
            install.append(skill)
            write.append(skill)
        plan.append({"dest": str(dest), "installed": install, "written": write, "skipped": skipped})
    return plan


def _apply_installs(plan: list[dict], *, dry_run: bool) -> None:
    if dry_run:
        return
    for entry in plan:
        for skill in entry["written"]:
            copy_skill(skill, Path(entry["dest"]))
    # Record where they landed so `media-ai uninstall` can find them again —
    # including a custom path no amount of scanning would guess.
    record_install([Path(entry["dest"]) for entry in plan])


# ---------------------------------------------------------------- credentials


def _env_var_names(provider: str) -> tuple[str, ...]:
    return stores.ENV_VARS.get(provider, (f"{provider.upper()}_API_KEY",))


def _env_already_set(provider: str) -> str | None:
    for var in _env_var_names(provider):
        if os.getenv(var):
            return var
    return None


def _collect_credentials(providers: dict[str, list[str]], prompter) -> dict[str, dict]:
    """Ask how to store keys once, then collect one per provider.

    The storage *mechanism* is uniform across providers (the resolver chain is the
    same for all of them); only the environment variable name differs. So this asks
    the how-question once rather than per provider.
    """
    modes = [
        Option("paste the key", hint="stored in credentials.toml, chmod 600"),
        Option("reference an environment variable", hint='writes env://VAR, the key never lands on disk'),
    ]
    mode = prompter.select("How should keys be stored?", modes)

    creds: dict[str, dict] = {}
    for provider, skills in sorted(providers.items()):
        already = _env_already_set(provider)
        label = f"{provider} — unlocks {', '.join(s.removeprefix('media-ai-') for s in skills)}"
        if already and not prompter.confirm(f"{label}\n  ${already} is already set; configure anyway?", default=False):
            continue
        if mode == 1:
            var = _env_var_names(provider)[0]
            chosen = prompter.text(f"{label}\n  environment variable to read", default=var)
            creds[provider] = {"api_key": f"env://{chosen}"}
        else:
            key = prompter.secret(f"{label}\n  API key (input hidden)")
            if key.strip():
                creds[provider] = {"api_key": key.strip()}
    return creds


# --------------------------------------------------------------------- models


def _model_hint(caps) -> str:
    """What a user needs to know before making this model their default.

    Discovery lists deprecated and preview models — they are still callable, and
    withholding them would be worse. But offering one unlabelled means someone picks
    a superseded model on setup day and finds out months later.
    """
    bits = []
    if caps.status == "deprecated":
        bits.append(f"deprecated → {caps.replacement}" if caps.replacement else "deprecated")
    elif caps.status == "preview":
        bits.append("preview")
    bits.append(f"verified {caps.verified}" if caps.verified else "never live-tested")
    return " · ".join(bits)


def _rank(caps) -> tuple:
    """Sort key: current before preview before deprecated, verified before not."""
    order = {"ga": 0, "preview": 1, "deprecated": 2, "removed": 3}
    return (order.get(caps.status, 9), 0 if caps.verified else 1)


# Groups whose models are *not* the ids `models()` enumerates. ElevenLabs runs music
# and sound effects on their own models and declares them in dedicated capability
# fields, while lumping every audio operation into one `AudioCaps.operations` — so
# matching on the operation set alone would offer TTS ids as the music default, and
# the wizard would write one into config for `music generate` to fail on.
_MODEL_FIELDS = {"music": "music_models", "sound": "sound_models"}


def _models_for(provider: str, group: str) -> list[Option]:
    """Candidate models for a skill group, labelled with lifecycle and provenance.

    Returns Options rather than bare ids so the caller cannot show one without its
    status — the catalogue knowing a model is deprecated is no use if the wizard
    that sets it as a default does not say so.
    """
    try:
        prov = registry.get_provider(provider)
        wanted = {op for op in Operation if op.value.split(".", 1)[0] == group}
        field_name = _MODEL_FIELDS.get(group)
        found, declared = [], []
        for model in prov.models():
            caps = prov.capabilities(model)
            for block in (caps.image, caps.video, caps.audio):
                if block is None or not block.operations & wanted:
                    continue
                declared += list(getattr(block, field_name, ()) or ()) if field_name else []
                found.append(caps)
                break
        if field_name and declared:
            # These ids are not in `models()`, so there is no ModelCapabilities to
            # label them with — say where they came from instead of nothing.
            seen = dict.fromkeys(declared)
            return [Option(label=m, hint=f"{provider} {group} model", value=m) for m in seen]
        found.sort(key=_rank)
        return [Option(label=c.model, hint=_model_hint(c), value=c.model) for c in found]
    except Exception:  # noqa: BLE001 - discovery is best-effort; free text still works
        return []


def _configure_volc_endpoints(groups: set[str], prompter) -> dict:
    """Ark addresses models by account-specific endpoint ids.

    An ``ep-…`` id names a deployment, not a model, so on its own it tells the CLI
    nothing — it cannot answer "does this support image editing?". Asking which model
    sits behind it is what makes the endpoint's capabilities knowable.
    """
    prompter.note(
        "\nVolcengine Ark model ids are account-specific: a custom endpoint (ep-…) only\n"
        "exists on the account that created it, so the built-in defaults may not be\n"
        "enabled on yours. Check the Ark console for your ids."
    )
    table: dict = {}
    endpoints: dict = {}
    for group in sorted(groups):
        for key, label in MODEL_SLOTS.get(("volc", group), ()):
            model = prompter.text(f"volc — model id for {label}", default="")
            if not model:
                continue
            table[key] = model
            if model.lower().startswith("ep-"):
                candidates = _models_for("volc", group)
                if candidates:
                    idx = prompter.select(
                        f"  which model does {model} serve?\n"
                        "  (this is what lets the CLI know its capabilities)",
                        candidates,
                    )
                    endpoints[model] = candidates[idx].value
    if endpoints:
        table["endpoints"] = endpoints
    return table


def _configure_models(providers: list[str], groups: set[str], prompter, *, advanced: bool) -> dict:
    """Per-provider model defaults, keyed by skill group rather than by provider.

    Only Volc is asked unconditionally: its ids cannot be enumerated. For the rest the
    built-in defaults are sensible, so the questions are behind ``advanced``.
    """
    out: dict = {}
    for provider in providers:
        if provider == "volc":
            table = _configure_volc_endpoints(groups, prompter)
            if table:
                out["volc"] = table
            continue
        if not advanced:
            continue
        table = {}
        for group in sorted(groups):
            for key, label in MODEL_SLOTS.get((provider, group), ()):
                candidates = _models_for(provider, group)
                if not candidates:
                    continue
                idx = prompter.select(f"{provider} — model for {label}", candidates)
                table[key] = candidates[idx].value
        if table:
            out[provider] = table
    return out


# ----------------------------------------------------------------- the wizard


def _groups_for(skills: list[str]) -> set[str]:
    return {op.value.split(".", 1)[0] for s in skills for op in operations_for_skill(s)}


@dataclass
class _Answers:
    """Everything the questions establish, before any of it is acted on.

    The wizard is two halves: fill this in, then apply it. Keeping them apart is what
    lets a step be re-run — which is what "go back" is — and what keeps Ctrl-C from
    leaving half a configuration behind.

    **Every step clears the fields it owns before it does anything else**, including
    on the paths where it decides it has nothing to ask. Re-running a step otherwise
    leaves the previous run's answer behind: go back and deselect a provider, and the
    key typed for it would still be written — or, worse, ``providers`` would name a
    provider that ``needed`` no longer has, and the next step would die on a KeyError
    holding every answer the user had just given.
    """

    skills: list[str] = field(default_factory=list)
    dests: list[Path] = field(default_factory=list)
    want_custom: bool = False
    custom_dest: Path | None = None
    plan: list[dict] = field(default_factory=list)
    needed: dict[str, list[str]] = field(default_factory=dict)
    providers: list[str] = field(default_factory=list)
    creds: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)
    verify: dict[str, bool] = field(default_factory=dict)


def _wizard(args, prompter) -> dict:
    """Ask everything, then do everything, then close the run off.

    Steps run through :func:`run_steps`, so Esc in any of them returns to the previous
    question. That is only safe because none of them write: the whole point of the
    ``_Answers`` split. Several steps also do nothing at all depending on the flags
    (``--skills-dest``, ``--skills-only``, no provider needed) — the driver skips over
    those on the way back, so "back" always lands on a real question.
    """
    prompter.intro("media-ai setup")
    for title, message in announcements():
        prompter.box(title, message)
    summary: dict = {
        "ok": True, "schema_version": SCHEMA_VERSION, "operation": "init",
        "wrote": [], "backed_up": [], "providers": [], "skills": [], "dry_run": bool(args.dry_run),
    }
    answers = _Answers()
    run_steps(
        [
            lambda: _ask_skills(args, prompter, answers),
            lambda: _ask_dests(args, prompter, answers),
            lambda: _ask_custom_dest(args, prompter, answers),
            lambda: _ask_collisions(args, prompter, answers),
            lambda: _ask_providers(args, prompter, answers),
            lambda: _ask_credentials(args, prompter, answers),
            lambda: _ask_models(args, prompter, answers),
            lambda: _ask_verify(args, prompter, answers),
        ],
        prompter,
    )
    _apply(args, answers, summary)
    if answers.verify:
        summary["verified"] = _probe_keys(answers.verify, prompter)
    _report(summary, prompter)
    return summary


# -- the questions ---------------------------------------------------------


def _ask_skills(args, prompter, answers: _Answers) -> None:
    answers.skills = []
    # --non-interactive means "take the defaults, ask nothing" — it has to hold for
    # every question below, or an unattended run blocks on a prompt it cannot answer.
    if args.non_interactive:
        answers.skills = available_skills()
        return
    # Only the *optional* skills are a real choice. The shared contract, discovery
    # and cost accounting are assumed by everything else, and the job skill is
    # half of what async video generation means — offering those four produces
    # selections that half-work, not informed consent. See `_discovery.TIERS`.
    offered = selectable_skills()
    picked = prompter.multiselect(
        "Which skills should be installed?", _skill_choices(offered),
        preselected=list(range(len(offered))),
    )
    answers.skills, reasons = resolve_selection([offered[i] for i in picked])
    _report_additions(reasons, prompter)


def _ask_dests(args, prompter, answers: _Answers) -> None:
    answers.dests, answers.want_custom = [], False
    if args.skills_dest:
        answers.dests = [Path(args.skills_dest).expanduser().resolve()]
        return
    if args.non_interactive:
        raise MediaError(
            "--non-interactive needs --skills-dest: there is no safe default for which "
            "agent directory to install into",
            category=ErrorCategory.CLI,
        )
    choices = _dest_choices()
    chosen = [choices[i].value for i in prompter.multiselect(
        "Where should they be installed?", choices, preselected=_preselected_dests(choices),
    )]
    answers.want_custom = CUSTOM_DEST in chosen
    answers.dests = [dest for dest in chosen if dest != CUSTOM_DEST]


def _ask_custom_dest(args, prompter, answers: _Answers) -> None:
    """The path behind the "somewhere else…" row.

    Its own step so that going back from it returns to the destination list rather
    than past it — and so that not ticking the row asks nothing at all, which is what
    the driver then steps over on the way back.
    """
    answers.custom_dest = None
    if not answers.want_custom:
        return
    raw = prompter.text("Path to install to")
    if raw.strip():
        answers.custom_dest = Path(raw).expanduser().resolve()


def _all_dests(answers: _Answers) -> list[Path]:
    return [*answers.dests, *([answers.custom_dest] if answers.custom_dest else [])]


def _ask_collisions(args, prompter, answers: _Answers) -> None:
    answers.plan = []
    if dests := _all_dests(answers):
        answers.plan = _plan_installs(answers.skills, dests, prompter, unattended=args.non_interactive)


def _ask_providers(args, prompter, answers: _Answers) -> None:
    answers.needed, answers.providers = {}, []
    if args.skills_only or args.non_interactive:
        # Credentials are never collected unattended: there is nothing sensible to
        # default a key to, and guessing one would be worse than stopping here.
        return
    answers.needed = providers_for_skills(answers.skills)
    if not answers.needed:
        prompter.note("The selected skills run locally — no credentials needed.")
        return
    choices = [
        Option(p, hint=", ".join(s.removeprefix("media-ai-") for s in sk), value=p)
        for p, sk in sorted(answers.needed.items())
    ]
    picked = prompter.multiselect(
        "These providers can serve the skills you picked. Configure which?",
        choices, preselected=list(range(len(choices))),
    )
    answers.providers = [choices[i].value for i in picked]


def _ask_credentials(args, prompter, answers: _Answers) -> None:
    answers.creds = {}
    if not answers.providers:
        return
    answers.creds = _collect_credentials({p: answers.needed[p] for p in answers.providers}, prompter)


def _ask_models(args, prompter, answers: _Answers) -> None:
    answers.models = {}
    if not answers.providers:
        return
    answers.models = _configure_models(
        answers.providers, _groups_for(answers.skills), prompter, advanced=args.advanced
    )


def _ask_verify(args, prompter, answers: _Answers) -> None:
    """Decide *whether* to probe each key. The probing itself happens after the apply.

    Asked here rather than beside the probe so the rule holds without an exception:
    a question after the writes have happened would make "cancelled; nothing was
    written" a lie, and an Esc there would escape the step driver entirely.
    """
    answers.verify = {}
    if not args.verify:
        return
    for provider in sorted(answers.creds):
        answers.verify[provider] = provider != "openai" or prompter.confirm(
            "openai has no free probe — verifying costs one small image generation. Verify it?",
            default=False,
        )


# -- and then the doing ----------------------------------------------------


def _apply(args, answers: _Answers, summary: dict) -> None:
    """The only part that touches the disk."""
    summary["skills"] = answers.plan
    _apply_installs(answers.plan, dry_run=args.dry_run)
    if answers.creds:
        path = credentials_path()
        _merge_write(path, _load(path) | answers.creds, write_private, CREDENTIALS_HEADER, args, summary)
    if answers.models:
        path = config_path()
        existing = _load(path)
        existing["providers"] = (existing.get("providers") or {}) | answers.models
        _merge_write(path, existing, write_public, CONFIG_HEADER, args, summary)
    summary["providers"] = sorted(answers.creds)


def _merge_write(path: Path, data: dict, writer, header: str, args, summary: dict) -> None:
    """Write ``data`` to ``path``, backing the old file up only if it really changes.

    The "only if" matters for re-runs: without it, answering the same questions a
    second time leaves a second ``credentials.toml.bak`` — a copy of the same keys,
    under a name nobody will remember to delete.

    The *write* still happens either way, because it is what sets the mode: the
    resolver refuses a group- or world-readable ``credentials.toml``, and re-running
    the wizard is the obvious way to fix one. Skipping the write for identical content
    would leave that broken with no way back short of a manual ``chmod``. The content
    is unchanged, so the file is not — only its permissions can be.
    """
    text = dumps(data, header=header)
    if args.dry_run:
        summary["wrote"].append(str(path))  # nothing happened; `dry_run: true` says so
        return
    if not _unchanged(path, text):
        backup = _backup(path)
        if backup:
            summary["backed_up"].append(str(backup))
    writer(path, text)
    summary["wrote"].append(str(path))


def _unchanged(path: Path, text: str) -> bool:
    try:
        return path.is_file() and path.read_text(encoding="utf-8") == text
    except OSError:
        return False


def _probe_keys(wanted: dict[str, bool], prompter) -> dict:
    """Probe each key the user agreed to check. Asks nothing — that already happened."""
    from ._verify import probe

    out = {}
    for provider, agreed in sorted(wanted.items()):
        out[provider] = probe(provider) if agreed else "skipped"
        prompter.note(f"{provider}: {out[provider]}")
    return out


def _report(summary: dict, prompter) -> None:
    for path in summary["wrote"]:
        prompter.note(f"wrote {path}")
    for path in summary["backed_up"]:
        prompter.note(f"backed up {path}")
    providers = summary["providers"]
    if providers:
        prompter.note(f"\nTo make {providers[0]} the default, set:\n  export MEDIA_PROVIDER={providers[0]}")
    prompter.note("\nTry it offline:\n  media-ai image generate --provider mock --prompt hello --output /tmp/x.png")
    prompter.outro("Done. `media-ai doctor` checks this install; `media-ai uninstall` undoes it.")


# -------------------------------------------------------------------- entry


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai init", description="Configure credentials, models, and Agent Skills.")
    ap.add_argument("--verify", action="store_true", help="probe each key after writing (off by default)")
    ap.add_argument("--advanced", action="store_true", help="also choose a model for each operation")
    ap.add_argument("--skills-only", action="store_true", help="only install Agent Skills")
    ap.add_argument("--skills-dest", default=None, help="install skills here without asking")
    ap.add_argument("--dry-run", action="store_true", help="report what would be written without writing it")
    ap.add_argument("--non-interactive", action="store_true", help="never open a terminal UI")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--log-level", default=None)
    ap.add_argument("--metadata-out", default=None)
    return ap


def _do(args) -> dict:
    prompter = get_prompter(force_fallback=args.non_interactive)
    try:
        return _wizard(args, prompter)
    except Cancelled:
        # A cancel is a CLI outcome (exit 2), not a timeout — and nothing partial is
        # left behind, because writes happen only after every question is answered.
        raise MediaError("setup cancelled; nothing was written", category=ErrorCategory.CLI) from None


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
