"""``<cli> init`` — set up bindings, scene defaults, and Agent Skills.

Skill-first: a user picks what they want to *do* ("generate images") and the wizard
derives which **bindings** could serve that, from the manifests. Nothing here holds a
list of models or providers — add a binding to a manifest and it appears in the menu,
which is what keeps setup and the code that runs afterwards describing the same
system. See :mod:`media_ai.cli._discovery`.

A binding, not a provider, is what gets a credential: three Seedream models are three
questions. That is the price of a config that says outright which key each call uses.

The machine contract still holds: every prompt is drawn on ``/dev/tty`` (see
:mod:`media_ai.cli._prompt`) and stdout carries exactly one JSON object summarising
what was written. That is also what makes the wizard usable from ``curl … | bash``,
where the pipe owns stdin.
"""

from __future__ import annotations

import argparse
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..brand import cli_name, cmd
from ..core.binding import AuthKind
from ..core.config import config_path, load_config, render_config
from ..core.errors import ErrorCategory, MediaError
from ..core.registry import catalog
from ..core.result import SCHEMA_VERSION
from ..core.scene import scenes_for_group
from ..credentials import stores
from ..credentials.tomlwrite import backup as tomlwrite_backup
from ..credentials.tomlwrite import dumps, write_private, write_public
from . import common
from ._announce import announcements
from ._discovery import (
    available_skills,
    bindings_for_skills,
    group_of,
    resolve_selection,
    scenes_for_skill,
    selectable_skills,
    skill_info,
)
from ._prompt import Cancelled, GoBack, Option, get_prompter, run_steps
from ._skillstore import SKILL_DESTS, copy_skill, installed_skills, record_install, skill_is_current


def credentials_header() -> str:
    """The comment block written at the top of ``credentials.toml``."""
    cli = cli_name()
    return (
        f"{cli} credentials — written by `{cli} init`.\n"
        "SECRETS: keep this file chmod 600; the CLI refuses to read it otherwise.\n"
        "Each [<name>] is an account. The wizard names one after the binding that\n"
        "uses it, so `which key did this binding use?` has a one-line answer."
    )


def config_header() -> str:
    """The comment block written at the top of ``config.toml`` by the wizard."""
    cli = cli_name()
    return (
        f"{cli} config — written by `{cli} init`.\n"
        "NON-SECRET: safe to share. Bindings and scene defaults, never a key."
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
    """Copy a file aside before rewriting it — see :func:`tomlwrite.backup`.

    Wrapped only to turn the "too many backups" case into the CLI's error contract;
    the mechanism lives beside ``atomic_write`` so every writer of a user-owned TOML
    file reaches the same one.
    """
    try:
        return tomlwrite_backup(path)
    except OSError as exc:
        raise MediaError(str(exc), category=ErrorCategory.CLI) from exc


# ------------------------------------------------------------------- skills


def _skill_choices(skills: list[str]) -> list[Option]:
    """One row per skill: what it costs (hint) and what it is (detail).

    A bare list of ``<brand>-*`` names is not a choice a user can make — nothing on
    it says what the ``sound`` skill does, or that ``concat`` needs no key. So
    the skill's own blurb is shown beside it.
    """
    out = []
    for skill in skills:
        scenes = scenes_for_skill(skill)
        hint = "no credentials needed" if not scenes else ", ".join(sorted(s.value for s in scenes))
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
    unable to tell whether the *directory* was there or whether this tool's skills were
    already in it — which is the difference between a first install and a refresh.
    """
    if not path.is_dir():
        return "", "Does not exist yet; it will be created."
    here = installed_skills(path)
    if here:
        return f"{len(here)} installed", f"Already holds {len(here)} {cli_name()} skill(s), which this will bring up to date."
    return "", f"The directory is there, with no {cli_name()} skills in it yet."


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


def _env_already_set(env: tuple[str, ...]) -> str | None:
    for var in env:
        if os.getenv(var):
            return var
    return None


def _configure_bindings(
    bindings: dict[str, list[str]], prompter, *, advanced: bool
) -> tuple[dict[str, dict], dict[str, str], dict[str, str], dict[str, str]]:
    """Configure one binding at a time, including its account-specific wire id.

    A binding is the unit of configuration, so its endpoint and its key must remain
    adjacent in the wizard.  Asking for all keys first and all endpoint ids later is
    particularly error-prone for Ark: after choosing several Seedream/Seedance
    bindings, there is no reliable way to know which id belongs to which key.

    Returns credentials plus generic model-id overrides, Ark endpoint ids and base
    URL overrides, all keyed by binding id.  The storage mechanism is deliberately
    asked once; then each binding is completed from its URL/id through its key.
    """
    modes = [
        Option("paste the key", hint="stored in credentials.toml, chmod 600"),
        Option("reference an environment variable", hint="writes env://VAR, the key never lands on disk"),
    ]
    mode = prompter.select("How should keys be stored?", modes)

    cat = catalog()
    notes = dict.fromkeys(
        cat.providers[cat.get(bid).provider].account_specific_note
        for bid in bindings
        if cat.providers[cat.get(bid).provider].account_specific_model_ids
    )
    for note in notes:
        prompter.note("\n" + (note or "This provider's wire IDs are account-specific — check its console."))

    # Esc inside this loop steps back one *binding*, not out of the whole step: with
    # several configured one after another, letting it unwind to the driver would
    # throw away every key already typed. Going back from the first one — or from the
    # storage question — still leaves the step, which is what the user means.
    creds: dict[str, dict] = {}
    model_ids: dict[str, str] = {}
    endpoint_ids: dict[str, str] = {}
    base_urls: dict[str, str] = {}
    order = sorted(bindings)
    i = 0
    while i < len(order):
        bid = order[i]
        try:
            entry, model_id, endpoint_id, base_url = _configure_one_binding(
                bid, bindings[bid], mode, prompter, advanced=advanced,
            )
        except GoBack:
            if i == 0:
                raise
            creds.pop(order[i - 1], None)
            model_ids.pop(order[i - 1], None)
            endpoint_ids.pop(order[i - 1], None)
            base_urls.pop(order[i - 1], None)
            i -= 1
            continue
        creds.pop(bid, None)
        model_ids.pop(bid, None)
        endpoint_ids.pop(bid, None)
        base_urls.pop(bid, None)
        if entry:
            creds[bid] = entry
            if model_id:
                model_ids[bid] = model_id
            if endpoint_id:
                endpoint_ids[bid] = endpoint_id
            if base_url:
                base_urls[bid] = base_url
        i += 1
    return (
        {bid: creds[bid] for bid in order if bid in creds},
        {bid: model_ids[bid] for bid in order if bid in model_ids},
        {bid: endpoint_ids[bid] for bid in order if bid in endpoint_ids},
        {bid: base_urls[bid] for bid in order if bid in base_urls},
    )


def _configure_one_binding(
    bid: str, skills: list[str], mode: int, prompter, *, advanced: bool
) -> tuple[dict | None, str | None, str | None, str | None]:
    """Ask all setup questions for one binding in the order they take effect."""
    cat = catalog()
    spec = cat.get(bid)
    provider = cat.providers[spec.provider]
    model_id = endpoint_id = base_url = None

    if provider.base_url.configurable:
        # Every HTTP binding owns its endpoint, so setup always makes that endpoint
        # explicit. Ark must always recommend its manifest's canonical endpoint; for
        # other providers a re-run preserves an existing custom URL as the default.
        configured = load_config().bindings.get(bid)
        default_url = (
            provider.base_url.default
            if provider.setup_base_url
            else (configured.base_url if configured and configured.base_url else provider.base_url.default)
        )
        base_url = prompter.text(
            f"{bid} — Base URL", default=default_url or "",
        ).strip()

    if provider.account_specific_model_ids:
        configured = load_config().bindings.get(bid)
        # A legacy ``model_id = \"ep-…\"`` remains usable, and makes a subsequent
        # init run migrate naturally to the accurately named ``endpoint_id`` field.
        current = (configured.endpoint_id or configured.model_id) if configured else ""
        if provider.wire_id_pattern and current and not re.fullmatch(provider.wire_id_pattern, current):
            current = ""
        hint = f" (for example {provider.wire_id_hint})" if provider.wire_id_hint else ""
        endpoint_id = prompter.text(
            f"{bid} — {provider.wire_id_label} sent as model{hint}", default=current,
        ).strip()
        if not endpoint_id:
            raise MediaError(
                f"{bid} needs an {provider.wire_id_label}", category=ErrorCategory.CLI,
                code="endpoint_id_missing", hint=f"enter an {provider.wire_id_hint or provider.wire_id_label}",
            )
        if provider.wire_id_pattern and not re.fullmatch(provider.wire_id_pattern, endpoint_id):
            raise MediaError(
                f"{bid} {provider.wire_id_label} must match {provider.wire_id_hint or provider.wire_id_pattern}",
                category=ErrorCategory.CLI, code="endpoint_id_invalid",
                hint=f"enter an {provider.wire_id_hint or provider.wire_id_label}",
            )
    elif advanced:
        configured = load_config().bindings.get(bid)
        current = (configured.model_id if configured and configured.model_id else spec.model_id)
        entered = prompter.text(f"{bid} — model ID sent on the wire", default=current).strip()
        if entered and entered != spec.model_id:
            model_id = entered

    return _ask_one_credential(bid, skills, mode, prompter), model_id, endpoint_id, base_url


def _ask_one_credential(bid: str, skills: list[str], mode: int, prompter) -> dict | None:
    cat = catalog()
    spec = cat.get(bid)
    provider = cat.providers[spec.provider]
    env = provider.auth.env or (f"{provider.name.upper().replace('-', '_')}_API_KEY",)
    label = f"{bid} — unlocks {', '.join(group_of(s) for s in skills)}"
    if provider.setup_hint:
        label += f"\n  {provider.setup_hint}"
    already = _env_already_set(env)
    if already and not prompter.confirm(f"{label}\n  ${already} is already set; configure anyway?", default=False):
        # Declining still writes the binding — pointed at the variable that is set, so
        # a config that omits nothing is what makes `bindings list` trustworthy.
        return {"api_key": f"env://{already}"}
    if mode == 1:
        chosen = prompter.text(f"{label}\n  environment variable to read", default=env[0])
        return {"api_key": f"env://{chosen}"}
    key = prompter.secret(f"{label}\n  API key")
    return {"api_key": key.strip()} if key.strip() else None


def _binding_choice(bid: str, skills: list[str]) -> Option:
    """One binding as a menu row, labelled with what it costs to trust it.

    Lifecycle and live-test provenance go in the hint for the same reason discovery
    reports them: a preview model that has never been exercised against the real API
    is a fine thing to pick, and a terrible thing to pick without being told.
    """
    spec = catalog().get(bid)
    bits = []
    if spec.lifecycle.value == "deprecated":
        bits.append(f"deprecated → {spec.replacement}" if spec.replacement else "deprecated")
    elif spec.lifecycle.value == "preview":
        bits.append("preview")
    bits.append(f"verified {spec.verified}" if spec.verified else "never live-tested")
    bits.append(", ".join(group_of(s) for s in skills))
    return Option(bid, hint=" · ".join(bits), value=bid)


def _ask_scene_defaults(bindings: list[str], skills: list[str], prompter) -> dict[str, str]:
    """Which binding each command group uses when a call names none.

    Asked per *group* and stored per *scene*. A group is the decision people actually
    make ("images go here"); storing it expanded means refining one scene later — text
    to image on one binding, editing on another — needs no schema change.

    The question is only asked about the scenes where there is genuinely something to
    choose. A scene with a single candidate is not a decision, and it used to be skipped
    entirely whenever the group's answer went to a binding that did not serve it: choose
    a model for the ``video`` group and ``video.concat`` — served by ``local/ffmpeg``
    alone, offered in the same list, and never the answer anyone gives to "which model
    generates my video" — was left with no default at all, so a fresh install refused
    ``video concat`` while naming the binding it should have used in the hint.

    **A group answer that cannot cover every scene leaves a second decision, not a
    guess.** One binding per group is the common case but not a guaranteed one: configure
    ``eleven-multilingual-v2`` (speech only), ``eleven-v3`` (dialogue only) and
    ``gemini-tts`` (both), answer the group question with either ElevenLabs model, and
    the *other* scene had two candidates, neither of them the answer — so it was dropped
    on the same floor ``video.concat`` used to land on, and ``speech dialogue``
    refused on a fresh install. Those scenes get their own question rather than an
    inferred sibling: a default is what every unflagged call silently gets, which is the
    last place to substitute something nobody chose.
    """
    cat = catalog()
    out: dict[str, str] = {}
    for group in sorted({group_of(s) for s in skills}):
        scenes = sorted(scenes_for_group(group), key=lambda s: s.value)
        serves = {scene: [b for b in bindings if scene in cat.get(b).scenes] for scene in scenes}
        contested = sorted({b for candidates in serves.values() if len(candidates) > 1 for b in candidates})
        chosen = None
        if contested:
            idx = prompter.select(f"Default for `{cli_name()} {group}` when no binding is named",
                                  [Option(b, hint=cat.get(b).title, value=b) for b in contested])
            chosen = contested[idx]
        for scene, candidates in serves.items():
            if chosen in candidates:
                out[scene.value] = chosen
            elif len(candidates) == 1:
                out[scene.value] = candidates[0]  # not a decision; nothing else serves it
            elif candidates:
                idx = prompter.select(
                    f"Default for `{scene.value}`, which {chosen} does not serve",
                    [Option(b, hint=cat.get(b).title, value=b) for b in candidates],
                )
                out[scene.value] = candidates[idx]
    return out


# ----------------------------------------------------------------- the wizard


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
    needed: dict[str, list[str]] = field(default_factory=dict)   # binding id -> skills
    bindings: list[str] = field(default_factory=list)            # the ones picked
    creds: dict = field(default_factory=dict)                    # binding id -> {"api_key": …}
    model_ids: dict[str, str] = field(default_factory=dict)      # binding id -> wire id override
    endpoint_ids: dict[str, str] = field(default_factory=dict)   # binding id -> Ark endpoint id
    base_urls: dict[str, str] = field(default_factory=dict)      # binding id -> explicit setup URL
    defaults: dict[str, str] = field(default_factory=dict)       # scene -> binding id
    verify: dict[str, bool] = field(default_factory=dict)


def _wizard(args, prompter) -> dict:
    """Ask everything, then do everything, then close the run off.

    Steps run through :func:`run_steps`, so Esc in any of them returns to the previous
    question. That is only safe because none of them write: the whole point of the
    ``_Answers`` split. Several steps also do nothing at all depending on the flags
    (``--skills-dest``, ``--skills-only``, no provider needed) — the driver skips over
    those on the way back, so "back" always lands on a real question.
    """
    prompter.intro(f"{cli_name()} setup")
    for title, message in announcements():
        prompter.box(title, message)
    summary: dict = {
        "ok": True, "schema_version": SCHEMA_VERSION, "command": "init",
        "wrote": [], "backed_up": [], "bindings": [], "skills": [], "dry_run": bool(args.dry_run),
    }
    answers = _Answers()
    run_steps(
        [
            lambda: _ask_skills(args, prompter, answers),
            lambda: _ask_dests(args, prompter, answers),
            lambda: _ask_custom_dest(args, prompter, answers),
            lambda: _ask_collisions(args, prompter, answers),
            lambda: _ask_bindings(args, prompter, answers),
            lambda: _ask_binding_configuration(args, prompter, answers),
            lambda: _ask_defaults(args, prompter, answers),
            lambda: _ask_verify(args, prompter, answers),
        ],
        prompter,
    )
    _apply(args, answers, summary)
    if answers.verify and not args.dry_run:
        # Not on a dry run: `probe` makes a real call, and for openai a *billed* one.
        # It would also be answering the wrong question — nothing was written, so it
        # would be reporting on whatever credentials happened to be there already.
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


def _ask_bindings(args, prompter, answers: _Answers) -> None:
    answers.needed, answers.bindings = {}, []
    if args.skills_only or args.non_interactive:
        # Credentials are never collected unattended: there is nothing sensible to
        # default a key to, and guessing one would be worse than stopping here.
        return
    answers.needed = bindings_for_skills(answers.skills)
    if not answers.needed:
        prompter.note("The selected skills run locally — no credentials needed.")
        return
    choices = [_binding_choice(bid, skills) for bid, skills in sorted(answers.needed.items())]
    picked = prompter.multiselect(
        "These bindings can serve the skills you picked. Configure which?",
        choices, preselected=list(range(len(choices))),
    )
    answers.bindings = [choices[i].value for i in picked]


def _ask_binding_configuration(args, prompter, answers: _Answers) -> None:
    answers.creds, answers.model_ids, answers.endpoint_ids, answers.base_urls = {}, {}, {}, {}
    if not answers.bindings:
        return
    (
        answers.creds,
        answers.model_ids,
        answers.endpoint_ids,
        answers.base_urls,
    ) = _configure_bindings(
        {b: answers.needed[b] for b in answers.bindings}, prompter, advanced=args.advanced,
    )


def _ask_defaults(args, prompter, answers: _Answers) -> None:
    """Which binding each group falls back to — the only automatic choice there is.

    Worth asking even when a group has one candidate: without a default, a call that
    names no binding fails, and "it worked in the wizard" is the wrong lesson to draw
    from a setup that configured a key and stopped short of making it reachable.

    The candidates are the bindings configured here **plus the ones that need no
    configuring**. Leaving those out was a bug with teeth: ``local/ffmpeg`` requires no
    credential, so it never appeared in ``creds``, so no default was ever written for the
    scenes only it serves — and ``video concat`` on a fresh install answered
    ``no_default_binding`` for a binding that was sitting right there, free and offline.
    Whether a binding needs a key has nothing to do with whether a caller may omit its
    name, and this step is the one that decides the latter.
    """
    answers.defaults = {}
    if args.skills_only:
        return  # "without changing credentials or bindings" — a default is config
    candidates = sorted(set(answers.creds) | set(_configuration_free_bindings()))
    if not candidates:
        return
    answers.defaults = _ask_scene_defaults(candidates, answers.skills, prompter)


def _configuration_free_bindings() -> list[str]:
    """Bindings callable with nothing configured — minus the placeholder.

    ``mock/mock`` is equally free and is deliberately excluded: proposing it as a scene
    default is exactly the recommendation ``placeholder`` exists to suppress, and a
    default is the strongest form of one, since it is what a call with no ``--binding``
    silently gets. It stays callable by name.
    """
    cat = catalog()
    return [
        spec.id for spec in cat.all()
        if cat.providers[spec.provider].auth.kind is AuthKind.NONE and not spec.placeholder
    ]


def _ask_verify(args, prompter, answers: _Answers) -> None:
    """Decide *whether* to probe each key. The probing itself happens after the apply.

    Now that every probe is a free authenticated GET (see ``_verify``), ``--verify``
    is the whole answer and there is nothing left to confirm — the flag already *is*
    the consent. This step therefore asks nothing; ``run_steps`` skips back over it, so
    it stays invisible rather than becoming an empty screen.

    It remains a step rather than a line inside the apply phase because that is the
    rule: a question after the writes have happened would make "cancelled; nothing was
    written" a lie, and an Esc there would escape the step driver entirely. Keeping the
    seam means restoring a question here later costs nothing.
    """
    answers.verify = {bid: True for bid in sorted(answers.creds)} if args.verify else {}


# -- and then the doing ----------------------------------------------------


def _apply(args, answers: _Answers, summary: dict) -> None:
    """The only part that touches the disk.

    Both files are *serialized* before anything is written. ``dumps`` supports a
    narrow subset of TOML and refuses the rest rather than mangling it, so a
    hand-written config holding a float or an array-of-tables raises — and raising
    after the skills and the credentials had been written is exactly the half-applied
    state the ask-then-do split exists to avoid.
    """
    pending: list[tuple[Path, str, object]] = []
    raw_keys = {bid: e for bid, e in answers.creds.items() if not e["api_key"].startswith("env://")}
    if raw_keys:
        # Only keys that are actually stored go in the secret file. A binding pointed
        # at env:// keeps its key out of the filesystem entirely, which is the whole
        # reason that option exists.
        path = credentials_path()
        pending.append((path, _render(path, _load(path) | raw_keys, credentials_header()), write_private))
    if answers.creds or answers.defaults:
        pending.append((config_path(), _render_config(answers), write_public))

    summary["skills"] = answers.plan
    _apply_installs(answers.plan, dry_run=args.dry_run)
    for path, text, writer in pending:
        _write_merged(path, text, writer, args, summary)
    summary["bindings"] = sorted(answers.creds)
    summary["defaults"] = dict(sorted(answers.defaults.items()))


def _render_config(answers: _Answers) -> str:
    """Merge this run's bindings and defaults into whatever is already configured.

    Merged rather than replaced: setting up video today must not silently drop the
    image binding configured last week. Re-running with the same answers produces the
    same bytes, which is what keeps the installer a no-op on a second pass.
    """
    from ..core.config import UserBinding

    existing = load_config()
    bindings = dict(existing.bindings)
    for bid, entry in answers.creds.items():
        key = entry["api_key"]
        # Merge: re-running `init` is the documented upgrade path, so it must not drop
        # the base_url, options or endpoint id a previous run (or a hand edit) put there.
        # Only what this run actually asked about changes.
        bindings[bid] = (bindings.get(bid) or UserBinding(id=bid)).merged_with(
            # ``endpoint_id`` replaces a legacy Ark ``model_id``; keeping both would
            # make the next config read deliberately reject the ambiguous wire id.
            model_id="" if bid in answers.endpoint_ids else answers.model_ids.get(bid),
            endpoint_id=answers.endpoint_ids.get(bid),
            base_url=answers.base_urls.get(bid),
            credential=key if key.startswith("env://") else f"cred://{bid}",
        )
    merged = existing.merged_with(
        bindings=bindings,
        defaults=dict(existing.defaults) | answers.defaults,
        path=config_path(), exists=True,
    )
    return render_config(merged, header=config_header())


def _render(path: Path, data: dict, header: str) -> str:
    """Serialize ``data``, turning a writer refusal into an error a user can act on."""
    from ..credentials.tomlwrite import TomlWriteError

    try:
        return dumps(data, header=header)
    except TomlWriteError as exc:
        raise MediaError(
            f"{path} holds a value this writer cannot round-trip ({exc}); move it aside, "
            "then re-run — nothing has been changed",
            category=ErrorCategory.CLI,
        ) from exc


def _write_merged(path: Path, text: str, writer, args, summary: dict) -> None:
    """Write ``text`` to ``path``, backing the old file up only if it really changes.

    The "only if" matters for re-runs: without it, answering the same questions a
    second time leaves a second ``credentials.toml.bak`` — a copy of the same keys,
    under a name nobody will remember to delete.

    The *write* still happens either way, because it is what sets the mode: the
    resolver refuses a group- or world-readable ``credentials.toml``, and re-running
    the wizard is the obvious way to fix one. Skipping the write for identical content
    would leave that broken with no way back short of a manual ``chmod``. The content
    is unchanged, so the file is not — only its permissions can be.
    """
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
    dry = summary["dry_run"]
    verb = "would write" if dry else "wrote"
    for path in summary["wrote"]:
        prompter.note(f"{verb} {path}")
    for path in summary["backed_up"]:
        prompter.note(f"backed up {path}")
    if summary["defaults"]:
        lines = "\n".join(f"  {scene:<24} {bid}" for scene, bid in summary["defaults"].items())
        prompter.note(f"\nCalls that name no binding will use:\n{lines}")
    elif summary["bindings"]:
        prompter.note(
            "\nNo default was set, so every call has to name a binding:\n"
            f"  {cmd('config', 'set-default')} <scene> {summary['bindings'][0]}"
        )
    # Named in full, and only here. `mock/mock` draws a picture of the prompt, so the
    # one safe way to mention it is as something the reader is deliberately asking for.
    prompter.note("\nTry it offline (draws a placeholder — no key, no network):\n"
                  f"  {cmd('image', 'generate')} --binding mock/mock --prompt hello --output /tmp/x.png")
    prompter.outro(
        "Dry run — nothing was changed."
        if dry
        else f"Done. `{cmd('doctor')}` checks this install; `{cmd('uninstall')}` undoes it."
    )


# -------------------------------------------------------------------- entry


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} init",
                                 description="Configure credentials, bindings, and Agent Skills.")
    ap.add_argument("--verify", action="store_true", help="probe each key after writing (off by default)")
    ap.add_argument("--advanced", action="store_true",
                    help="also configure each binding's wire identifier (Ark endpoint ID where applicable)")
    ap.add_argument("--skills-only", action="store_true",
                    help="install or refresh Agent Skills without changing credentials or bindings")
    ap.add_argument("--skills-dest", default=None, help="Agent Skills destination directory; skip the destination prompt")
    ap.add_argument("--dry-run", action="store_true", help="report what would be written without writing it")
    ap.add_argument("--non-interactive", action="store_true", help="never open a terminal UI")
    ap.add_argument("--pretty", action="store_true", help="pretty-print the JSON result")
    ap.add_argument("--log-level", default=None, help="stderr log level: debug, info, warning, or error")
    ap.add_argument("--verbose", action="store_true", help="print redacted HTTP diagnostics to stderr")
    ap.add_argument("--metadata-out", default=None, help="also write the secret-free result JSON to this path")
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
