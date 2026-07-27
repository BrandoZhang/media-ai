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
from pathlib import Path

from ..core import registry
from ..core.errors import ErrorCategory, MediaError
from ..core.result import SCHEMA_VERSION
from ..core.types import Operation
from ..credentials import stores
from ..credentials.profile import config_path
from ..credentials.tomlwrite import dumps, write_private, write_public
from . import common
from ._discovery import available_skills, operations_for_skill, providers_for_skills, skill_root
from ._prompt import Cancelled, Option, get_prompter

# Agent conventions that read SKILL.md directories. Adding one is a single row; the
# layouts are assumed identical (<root>/skills/<name>/SKILL.md) until shown otherwise.
SKILL_DESTS = (
    ("claude", ".claude/skills"),
    ("agents", ".agents/skills"),
    ("codex", ".codex/skills"),
    ("trae", ".trae/skills"),
    ("openclaw", ".openclaw/skills"),
)

# The shared skill is the common contract the others build on, so it always ships.
ALWAYS_INSTALL = "media-ai-shared"

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


def credentials_path() -> Path:
    return Path(os.getenv("MEDIA_CREDENTIALS_FILE", "~/.config/media-ai/credentials.toml")).expanduser()


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
    writer, so the original is the only record of anything hand-written."""
    if not path.is_file():
        return None
    for n in range(1, 1000):
        candidate = path.with_suffix(path.suffix + f".bak{'' if n == 1 else n}")
        if not candidate.exists():
            candidate.write_bytes(path.read_bytes())
            os.chmod(candidate, path.stat().st_mode & 0o777)
            return candidate
    raise MediaError(f"too many backups beside {path}", category=ErrorCategory.CLI)


# ------------------------------------------------------------------- skills


def _skill_choices(skills: list[str]) -> list[Option]:
    out = []
    for skill in skills:
        ops = operations_for_skill(skill)
        hint = "no credentials needed" if not ops else ", ".join(sorted(o.value for o in ops))
        if skill == ALWAYS_INSTALL:
            hint = "required by the others"
        out.append(Option(label=skill, hint=hint, value=skill))
    return out


def _dest_choices() -> list[Option]:
    """Every agent convention at user and project level, existing ones first."""
    out = []
    for _key, segment in SKILL_DESTS:
        for base, label in ((Path.home(), "~"), (Path.cwd(), "./")):
            path = base / segment
            out.append(Option(label=f"{label}{segment}", hint="exists" if path.is_dir() else "", value=path))
    out.sort(key=lambda o: (not o.hint, o.label))
    return out


def _copy_skill(name: str, dest_root: Path) -> list[Path]:
    """Copy one packaged skill directory to ``dest_root/<name>``, preserving
    ``references/`` subdirectories. Returns the files written."""
    written: list[Path] = []

    def walk(src, out: Path):
        out.mkdir(parents=True, exist_ok=True)
        for entry in src.iterdir():
            target = out / entry.name
            if entry.is_dir():
                walk(entry, target)
            else:
                target.write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
                written.append(target)

    walk(skill_root(name), dest_root / name)
    return written


def _install_skills(skills: list[str], dests: list[Path], prompter, *, dry_run: bool) -> list[dict]:
    """Copy each selected skill into each destination, asking before overwriting."""
    overwrite_all = skip_all = False
    report = []
    for dest in dests:
        installed, skipped = [], []
        for skill in skills:
            target = dest / skill
            if target.exists() and not overwrite_all:
                if skip_all:
                    skipped.append(skill)
                    continue
                choice = prompter.select(
                    f"{target} already exists",
                    [Option("overwrite"), Option("skip"), Option("overwrite all"), Option("skip all")],
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
            if not dry_run:
                _copy_skill(skill, dest)
            installed.append(skill)
        report.append({"dest": str(dest), "installed": installed, "skipped": skipped})
    return report


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


def _models_for(provider: str, group: str) -> list[Option]:
    """Candidate models for a skill group, labelled with lifecycle and provenance.

    Returns Options rather than bare ids so the caller cannot show one without its
    status — the catalogue knowing a model is deprecated is no use if the wizard
    that sets it as a default does not say so.
    """
    try:
        prov = registry.get_provider(provider)
        wanted = {op for op in Operation if op.value.split(".", 1)[0] == group}
        found = []
        for model in prov.models():
            caps = prov.capabilities(model)
            for block in (caps.image, caps.video, caps.audio):
                if block is not None and block.operations & wanted:
                    found.append(caps)
                    break
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


def _wizard(args, prompter) -> dict:
    prompter.note("media-ai setup\n")
    summary: dict = {
        "ok": True, "schema_version": SCHEMA_VERSION, "operation": "init",
        "wrote": [], "backed_up": [], "providers": [], "skills": [], "dry_run": bool(args.dry_run),
    }

    # -- skills ----------------------------------------------------------
    # --non-interactive means "take the defaults, ask nothing" — it has to hold for
    # every question below, or an unattended run blocks on a prompt it cannot answer.
    all_skills = available_skills()
    if args.non_interactive:
        skills = all_skills
    else:
        picked = prompter.multiselect(
            "Which skills should be installed?", _skill_choices(all_skills),
            preselected=list(range(len(all_skills))),
        )
        skills = sorted({all_skills[i] for i in picked} | {ALWAYS_INSTALL})

    if args.skills_dest:
        dests = [Path(args.skills_dest).expanduser().resolve()]
    elif args.non_interactive:
        raise MediaError(
            "--non-interactive needs --skills-dest: there is no safe default for which "
            "agent directory to install into",
            category=ErrorCategory.CLI,
        )
    else:
        choices = _dest_choices()
        chosen = prompter.multiselect(
            "Where should they be installed? (space to toggle, several are fine)", choices,
        )
        dests = [choices[i].value for i in chosen]
        if prompter.confirm("Add a custom path as well?", default=False):
            raw = prompter.text("Path")
            if raw.strip():
                dests.append(Path(raw).expanduser().resolve())

    if dests:
        summary["skills"] = _install_skills(skills, dests, prompter, dry_run=args.dry_run)

    if args.skills_only or args.non_interactive:
        # Credentials are never collected unattended: there is nothing sensible to
        # default a key to, and guessing one would be worse than stopping here.
        return summary

    # -- providers derived from the skills -------------------------------
    needed = providers_for_skills(skills)
    if not needed:
        prompter.note("\nThe selected skills run locally — no credentials needed.")
        return summary

    choices = [
        Option(p, hint=", ".join(s.removeprefix("media-ai-") for s in sk), value=p)
        for p, sk in sorted(needed.items())
    ]
    picked = prompter.multiselect(
        "These providers can serve the skills you picked. Configure which?",
        choices, preselected=list(range(len(choices))),
    )
    providers = [choices[i].value for i in picked]
    if not providers:
        return summary

    # -- credentials + model defaults ------------------------------------
    creds = _collect_credentials({p: needed[p] for p in providers}, prompter)
    models = _configure_models(providers, _groups_for(skills), prompter, advanced=args.advanced)

    # -- write -----------------------------------------------------------
    if creds:
        path = credentials_path()
        merged = _load(path) | creds
        if not args.dry_run:
            backup = _backup(path)
            if backup:
                summary["backed_up"].append(str(backup))
            write_private(path, dumps(merged, header=CREDENTIALS_HEADER))
        summary["wrote"].append(str(path))
    if models:
        path = config_path()
        existing = _load(path)
        existing["providers"] = (existing.get("providers") or {}) | models
        if not args.dry_run:
            backup = _backup(path)
            if backup:
                summary["backed_up"].append(str(backup))
            write_public(path, dumps(existing, header=CONFIG_HEADER))
        summary["wrote"].append(str(path))

    summary["providers"] = sorted(creds)
    if args.verify:
        summary["verified"] = _verify(sorted(creds), prompter)

    _report(summary, providers, prompter)
    return summary


def _verify(providers: list[str], prompter) -> dict:
    """Probe each configured key. Off by default — one provider has no free probe."""
    from ._verify import probe

    out = {}
    for provider in providers:
        if provider == "openai" and not prompter.confirm(
            "openai has no free probe — verifying costs one small image generation. Verify it?",
            default=False,
        ):
            out[provider] = "skipped"
            continue
        out[provider] = probe(provider)
        prompter.note(f"  {provider}: {out[provider]}")
    return out


def _report(summary: dict, providers: list[str], prompter) -> None:
    prompter.note("\nDone.")
    for path in summary["wrote"]:
        prompter.note(f"  wrote {path}")
    for path in summary["backed_up"]:
        prompter.note(f"  backed up {path}")
    if providers:
        prompter.note(
            f"\nTo make {providers[0]} the default, set:\n  export MEDIA_PROVIDER={providers[0]}"
        )
    prompter.note("\nTry it offline:\n  media-ai image generate --provider mock --prompt hello --output /tmp/x.png")


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
