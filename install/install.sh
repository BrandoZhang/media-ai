#!/usr/bin/env bash
# Installer for the CLI named in CLI_NAME below.
#
#   curl -fsSL https://raw.githubusercontent.com/BrandoZhang/media-ai/main/install/install.sh | bash
#
# Installs uv if missing, installs the package from git, self-tests offline, then hands
# over to `<cli> init` for configuration.
#
# Everything lives inside main() and main is called on the last line: a curl that dies
# mid-transfer leaves a truncated function definition that is never invoked, rather
# than half a script that runs.

set -euo pipefail

# Where the code comes from. Not the brand: a renamed build still installs from the
# repository it was forked from unless that is changed too.
REPO="${MEDIA_AI_REPO:-BrandoZhang/media-ai}"
# Fallback when the releases API is unreachable or rate-limited. Bump on release.
DEFAULT_VERSION="${MEDIA_AI_DEFAULT_VERSION:-v0.7.1}"
# The CLI's name, and the distribution `uv tool` keys the install by. Both are pinned
# to `media_ai.brand.CLI_NAME` by tests/test_brand.py — this script is outside the
# package and cannot import it, which is exactly why a test holds them together
# (the same arrangement DEFAULT_VERSION has with `media_ai.__version__`).
CLI_NAME="media-ai"
DIST_NAME="$CLI_NAME"

main() {
  local version="" skills_dest="" do_init=1 dry_run=0 do_uninstall=0 assume_yes=0
  local keep_flags=()

  while [ $# -gt 0 ]; do
    case "$1" in
      # `shift 2` on a flag given as the last argument returns non-zero, which under
      # `set -e` kills the installer with no output at all. Check for the value first
      # and say what is missing.
      --version)      need_value "$@"; version="$2"; shift 2 ;;
      --version=*)    version="${1#*=}"; shift ;;
      --skills-dest)  need_value "$@"; skills_dest="$2"; shift 2 ;;
      --skills-dest=*) skills_dest="${1#*=}"; shift ;;
      --no-init)      do_init=0; shift ;;
      --uninstall)    do_uninstall=1; shift ;;
      --keep-config)  keep_flags+=(--keep-config); shift ;;
      --keep-credentials) keep_flags+=(--keep-credentials); shift ;;
      --keep-skills)  keep_flags+=(--keep-skills); shift ;;
      -y|--yes)       assume_yes=1; shift ;;
      --dry-run)      dry_run=1; shift ;;
      -h|--help)      usage; return 0 ;;
      *) err "unknown option: $1"; usage; return 2 ;;
    esac
  done

  # Before ensure_uv: installing uv in order to uninstall would be absurd, and the
  # CLI may well have been installed some other way.
  if [ "$do_uninstall" -eq 1 ]; then
    run_uninstall "$assume_yes" "$dry_run" "${keep_flags[@]+"${keep_flags[@]}"}"
    return $?
  fi

  check_platform
  ensure_uv
  [ -n "$version" ] || version="$(resolve_version)"

  local spec="git+https://github.com/${REPO}@${version}"
  if [ "$dry_run" -eq 1 ]; then
    say "would install: uv tool install --force $spec"
    return 0
  fi

  say "installing ${DIST_NAME} (${version})…"
  uv tool install --force "$spec" >&2

  check_path
  self_test
  if [ "$do_init" -eq 1 ]; then run_init "$skills_dest"; fi
  return 0
}

usage() {
  cat >&2 <<'USAGE'
usage: install.sh [options]

  --version REF      install this git ref (tag, branch, or sha; default: latest tag)
  --skills-dest PATH install Agent Skills here without asking
  --no-init          skip the configuration wizard
  --dry-run          print what would be done and exit

uninstalling:

  --uninstall        remove the skills, the configuration, then the CLI itself
  --keep-config      with --uninstall: leave config.toml (model defaults, profiles)
  --keep-credentials with --uninstall: leave credentials.toml (API keys)
  --keep-skills      with --uninstall: leave the installed Agent Skills
  -y, --yes          with --uninstall: don't ask, take the defaults
USAGE
}

say() { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }
err() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; }

# Refuse a flag whose value is missing, with a message. Called as `need_value "$@"`,
# so $1 is the flag and $2 is the value that has to be there. A value starting with
# `-` counts as missing: `--version --dry-run` would otherwise install a git ref
# literally named "--dry-run" and silently drop the flag it swallowed.
need_value() {
  if [ $# -lt 2 ] || case "$2" in -*) true ;; *) false ;; esac; then
    err "$1 needs a value (e.g. $1 v0.2.0)"
    usage
    exit 2
  fi
}

check_platform() {
  case "$(uname -s)" in
    Linux|Darwin) ;;
    *) err "unsupported platform: $(uname -s). Windows needs WSL."; exit 1 ;;
  esac
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  say "uv not found; installing it…"
  if ! command -v curl >/dev/null 2>&1; then
    err "curl is required to install uv. Install curl, or install uv yourself:"
    err "  https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh >&2
  # The uv installer drops it in ~/.local/bin, which this shell may not have yet.
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || { err "uv install did not put uv on PATH"; exit 1; }
}

resolve_version() {
  # Lists releases rather than asking for /releases/latest, because that endpoint
  # skips pre-releases — and a 0.x line published as pre-release would leave it
  # answering 404 forever, silently pinning every user to DEFAULT_VERSION while
  # looking like it resolved something. The list is newest-first and includes them.
  #
  # Unauthenticated GitHub API allows ~60 requests/hour/IP, which CI hits easily, so a
  # failure here degrades to DEFAULT_VERSION rather than aborting the install.
  #
  # grep -o then head, rather than one sed: the API answers with compact single-line
  # JSON, and a greedy `.*"tag_name"` would match the *last* occurrence on that line —
  # i.e. the oldest release in the page — instead of the newest.
  local tag=""
  if command -v curl >/dev/null 2>&1; then
    tag="$(curl -fsSL --max-time 10 "https://api.github.com/repos/${REPO}/releases?per_page=1" 2>/dev/null \
           | parse_tag || true)"
  fi
  printf '%s' "${tag:-$DEFAULT_VERSION}"
}

# Split out so install/test_parse.sh can exercise it against real payload shapes.
parse_tag() {
  grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | head -n1 \
    | sed 's/.*"\([^"]*\)"$/\1/'
}

check_path() {
  command -v "$CLI_NAME" >/dev/null 2>&1 && return 0
  local bin="$HOME/.local/bin"
  err "${CLI_NAME} installed but is not on PATH."
  case "${SHELL:-}" in
    */zsh) err "  echo 'export PATH=\"$bin:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
    */fish) err "  fish_add_path $bin" ;;
    *)     err "  echo 'export PATH=\"$bin:\$PATH\"' >> ~/.bashrc && exec bash" ;;
  esac
  exit 1
}

self_test() {
  # The mock binding runs entirely locally, so this exercises the CLI, Pillow and
  # ffmpeg without a key or a network call. It is named explicitly: mock is a normal
  # binding, never a fallback, and a fresh install has no scene default yet.
  #
  # MEDIA_USAGE_LOG is redirected into the scratch directory along with the output:
  # every generation appends a line to the ledger, which defaults to
  # ./media_usage.jsonl — so without this the installer litters the directory it was
  # run from, and adds a line to it every time it is re-run.
  local tmp status=0
  tmp="$(mktemp -d)"
  MEDIA_USAGE_LOG="$tmp/usage.jsonl" "$CLI_NAME" image generate --binding mock/mock --prompt "install check" \
    --output "$tmp/probe.png" >/dev/null 2>"$tmp/err" || status=$?
  if [ "$status" -eq 0 ]; then
    say "self-test passed (offline, no key needed)"
    rm -rf "$tmp"
  else
    err "self-test failed:"
    sed 's/^/    /' "$tmp/err" >&2 || true
    rm -rf "$tmp"
    exit 1
  fi
}

run_init() {
  local skills_dest="${1:-}"
  # When this script is itself being piped from curl, the pipe owns stdin, so the
  # wizard has to read the terminal directly. With no terminal at all (CI, a
  # non-interactive shell) it must not block waiting for input that will never come.
  #
  # Test by actually opening it: with no controlling terminal, /dev/tty still exists
  # and still passes -e and -r, but opening it fails with ENXIO. Only the open tells
  # the truth.
  # stderr is redirected *before* the /dev/tty open is attempted — redirections apply
  # left to right, so the other order lets bash's own ENXIO message escape.
  if ! : 2>/dev/null < /dev/tty; then
    say "no terminal available; skipping setup. Configure later with:"
    printf '      %s init\n' "$CLI_NAME" >&2
    return 0
  fi
  # stdout is discarded for the same reason run_uninstall discards it: `init` ends by
  # printing its machine-contract JSON object, which after a wizard the user just
  # finished reading is noise landing under the closing line.
  if [ -n "$skills_dest" ]; then
    "$CLI_NAME" init --skills-dest "$skills_dest" < /dev/tty >/dev/null || true
  else
    "$CLI_NAME" init < /dev/tty >/dev/null || true
  fi
}

run_uninstall() {
  # Two halves, in this order: the CLI removes what it wrote (skills, and — only if
  # asked — the config files), then this removes the CLI. It cannot be done the other
  # way round, because the first half runs the CLI.
  local assume_yes="$1" dry_run="$2"
  shift 2
  local flags=("$@") have_tty=1

  if [ "$dry_run" -eq 1 ]; then flags+=(--dry-run); fi
  # Same /dev/tty test as run_init: under `curl … | bash` the pipe owns stdin, and
  # with no terminal at all the wizard must not wait for an answer that cannot come.
  # Without one, --yes is implied: everything goes except what a --keep-* flag holds back.
  : 2>/dev/null < /dev/tty || have_tty=0
  if [ "$assume_yes" -eq 1 ] || [ "$have_tty" -eq 0 ]; then flags+=(--yes); fi

  if command -v "$CLI_NAME" >/dev/null 2>&1; then
    say "removing installed Agent Skills and configuration…"
    if [ "$have_tty" -eq 1 ]; then
      "$CLI_NAME" uninstall "${flags[@]+"${flags[@]}"}" < /dev/tty >/dev/null || true
    else
      "$CLI_NAME" uninstall "${flags[@]+"${flags[@]}"}" >/dev/null || true
    fi
  else
    err "${CLI_NAME} is not on PATH; skipping skill removal (run '${CLI_NAME} uninstall' yourself if it is installed elsewhere)"
  fi

  if [ "$dry_run" -eq 1 ]; then
    say "would run: uv tool uninstall ${DIST_NAME}"
    return 0
  fi
  if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q "^${DIST_NAME}"; then
    say "removing the ${CLI_NAME} CLI…"
    uv tool uninstall "$DIST_NAME" >&2
  else
    err "${DIST_NAME} was not installed as a uv tool; remove it however you installed it:"
    err "  pip uninstall ${DIST_NAME}"
  fi
  return 0
}

main "$@"
