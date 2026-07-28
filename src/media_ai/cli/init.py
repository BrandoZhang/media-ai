"""``media-ai init`` — set up bindings, scene defaults, and Agent Skills.

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
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import Config, config_path, load_config, render_config
from ..core.errors import ErrorCategory, MediaError
from ..core.registry import catalog
from ..core.scene import scenes_for_group
from ..core.result import SCHEMA_VERSION
from ..credentials import stores
from ..credentials.tomlwrite import atomic_write, dumps, write_private, write_public
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

CREDENTIALS_HEADER = (
    "media-ai credentials — written by `media-ai init`.\n"
    "SECRETS: keep this file chmod 600; the CLI refuses to read it otherwise.\n"
    "Each [<name>] is an account. The wizard names one after the binding that\n"
    "uses it, so `which key did this binding use?` has a one-line answer."
)
CONFIG_HEADER = (
    "media-ai config — written by `media-ai init`.\n"
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


def _env_already_set(env: tuple[str, ...]) -> str | None:
    for var in env:
        if os.getenv(var):
            return var
    return None


def _collect_credentials(bindings: dict[str, list[str]], prompter) -> dict[str, dict]:
    """Ask how to store keys once, then collect one **per binding**.

    Per binding, not per provider: a binding carries its own credential reference, so
    picking three Seedream models is three questions. That is the cost of the config
    saying outright which key each call uses, instead of a shared entry a reader has
    to go and look up.

    The storage *mechanism* is the same for all of them, so the how-question is asked
    once. Returns ``{binding id: {"api_key": …}}`` — a raw key to store, or an
    ``env://VAR`` reference to write instead.
    """
    modes = [
        Option("paste the key", hint="stored in credentials.toml, chmod 600"),
        Option("reference an environment variable", hint="writes env://VAR, the key never lands on disk"),
    ]
    mode = prompter.select("How should keys be stored?", modes)

    # Esc inside this loop steps back one *binding*, not out of the whole step: with
    # several configured one after another, letting it unwind to the driver would
    # throw away every key already typed. Going back from the first one — or from the
    # storage question — still leaves the step, which is what the user means.
    creds: dict[str, dict] = {}
    order = sorted(bindings)
    i = 0
    while i < len(order):
        bid = order[i]
        try:
            entry = _ask_one_credential(bid, bindings[bid], mode, prompter)
        except GoBack:
            if i == 0:
                raise
            creds.pop(order[i - 1], None)
            i -= 1
            continue
        creds.pop(bid, None)
        if entry:
            creds[bid] = entry
        i += 1
    return {bid: creds[bid] for bid in order if bid in creds}


def _ask_one_credential(bid: str, skills: list[str], mode: int, prompter) -> dict | None:
    cat = catalog()
    spec = cat.get(bid)
    provider = cat.providers[spec.provider]
    env = provider.auth.env or (f"{provider.name.upper().replace('-', '_')}_API_KEY",)
    label = f"{bid} — unlocks {', '.join(s.removeprefix('media-ai-') for s in skills)}"
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


# ------------------------------------------------------------------ model ids


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
    bits.append(", ".join(s.removeprefix("media-ai-") for s in skills))
    return Option(bid, hint=" · ".join(bits), value=bid)


def _ask_model_ids(bindings: list[str], prompter, *, advanced: bool) -> dict[str, str]:
    """Per-binding overrides for the id that goes on the wire.

    Only asked where the manifest's default cannot be trusted: Ark model ids are
    account-specific, so the shipped one may simply not exist on the account being
    configured. Everywhere else the manifest is right and the question is noise —
    behind ``--advanced`` for anyone who wants it anyway.
    """
    cat = catalog()
    out: dict[str, str] = {}
    account_specific = [b for b in bindings if cat.providers[cat.get(b).provider].name == "volc-ark"]
    if account_specific:
        prompter.note(
            "\nVolcengine Ark model ids are account-specific: a custom endpoint (ep-…) only\n"
            "exists on the account that created it, and the shipped defaults may not be\n"
            "enabled on yours. Check the Ark console for your ids."
        )
    for bid in bindings:
        spec = cat.get(bid)
        if bid not in account_specific and not advanced:
            continue
        entered = prompter.text(f"{bid} — model id sent on the wire", default=spec.model_id)
        if entered and entered != spec.model_id:
            out[bid] = entered
    return out


def _ask_scene_defaults(bindings: list[str], skills: list[str], prompter) -> dict[str, str]:
    """Which binding each command group uses when a call names none.

    Asked per *group* and stored per *scene*. A group is the decision people actually
    make ("images go here"); storing it expanded means refining one scene later — text
    to image on one binding, editing on another — needs no schema change.
    """
    cat = catalog()
    out: dict[str, str] = {}
    for group in sorted({group_of(s) for s in skills}):
        scenes = scenes_for_group(group)
        if not scenes:
            continue
        candidates = [b for b in bindings if cat.get(b).scenes & scenes]
        if not candidates:
            continue
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            idx = prompter.select(f"Default for `media-ai {group}` when no binding is named", 
                                  [Option(b, hint=cat.get(b).title, value=b) for b in candidates])
            chosen = candidates[idx]
        for scene in sorted(scenes & cat.get(chosen).scenes, key=lambda s: s.value):
            out[scene.value] = chosen
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
    prompter.intro("media-ai setup")
    for title, message in announcements():
        prompter.box(title, message)
    summary: dict = {
        "ok": True, "schema_version": SCHEMA_VERSION, "operation": "init",
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
            lambda: _ask_credentials(args, prompter, answers),
            lambda: _ask_model_id_overrides(args, prompter, answers),
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


def _ask_credentials(args, prompter, answers: _Answers) -> None:
    answers.creds = {}
    if not answers.bindings:
        return
    answers.creds = _collect_credentials({b: answers.needed[b] for b in answers.bindings}, prompter)


def _ask_model_id_overrides(args, prompter, answers: _Answers) -> None:
    answers.model_ids = {}
    if not answers.creds:
        return
    answers.model_ids = _ask_model_ids(sorted(answers.creds), prompter, advanced=args.advanced)


def _ask_defaults(args, prompter, answers: _Answers) -> None:
    """Which binding each group falls back to — the only automatic choice there is.

    Worth asking even when a group has one candidate: without a default, a call that
    names no binding fails, and "it worked in the wizard" is the wrong lesson to draw
    from a setup that configured a key and stopped short of making it reachable.
    """
    answers.defaults = {}
    if not answers.creds:
        return
    answers.defaults = _ask_scene_defaults(sorted(answers.creds), answers.skills, prompter)


def _ask_verify(args, prompter, answers: _Answers) -> None:
    """Decide *whether* to probe each key. The probing itself happens after the apply.

    Asked here rather than beside the probe so the rule holds without an exception:
    a question after the writes have happened would make "cancelled; nothing was
    written" a lie, and an Esc there would escape the step driver entirely.
    """
    answers.verify = {}
    if not args.verify:
        return
    for bid in sorted(answers.creds):
        provider = bid.partition("/")[0]
        answers.verify[bid] = provider != "openai" or prompter.confirm(
            "openai has no free probe — verifying costs one small image generation. Verify it?",
            default=False,
        )


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
        pending.append((path, _render(path, _load(path) | raw_keys, CREDENTIALS_HEADER), write_private))
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
        bindings[bid] = UserBinding(
            id=bid,
            model_id=answers.model_ids.get(bid),
            credential=key if key.startswith("env://") else f"cred://{bid}",
        )
    merged = Config(
        bindings=bindings,
        defaults=dict(existing.defaults) | answers.defaults,
        path=config_path(), exists=True,
    )
    return render_config(merged, header=CONFIG_HEADER)


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
            f"  media-ai config set-default <scene> {summary['bindings'][0]}"
        )
    prompter.note("\nTry it offline:\n  media-ai image generate --provider mock --prompt hello --output /tmp/x.png")
    prompter.outro(
        "Dry run — nothing was changed."
        if dry
        else "Done. `media-ai doctor` checks this install; `media-ai uninstall` undoes it."
    )


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
