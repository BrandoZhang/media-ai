#!/usr/bin/env bash
# media-ai installer.
#
#   curl -fsSL https://raw.githubusercontent.com/BrandoZhang/media-ai/main/install/install.sh | bash
#
# Installs uv if missing, installs media-ai from git, self-tests offline, then hands
# over to `media-ai init` for configuration.
#
# Everything lives inside main() and main is called on the last line: a curl that dies
# mid-transfer leaves a truncated function definition that is never invoked, rather
# than half a script that runs.

set -euo pipefail

REPO="${MEDIA_AI_REPO:-BrandoZhang/media-ai}"
# Fallback when the releases API is unreachable or rate-limited. Bump on release.
DEFAULT_VERSION="${MEDIA_AI_DEFAULT_VERSION:-v0.2.0}"

main() {
  local version="" skills_dest="" do_init=1 dry_run=0

  while [ $# -gt 0 ]; do
    case "$1" in
      --version)      version="${2:-}"; shift 2 ;;
      --version=*)    version="${1#*=}"; shift ;;
      --skills-dest)  skills_dest="${2:-}"; shift 2 ;;
      --skills-dest=*) skills_dest="${1#*=}"; shift ;;
      --no-init)      do_init=0; shift ;;
      --dry-run)      dry_run=1; shift ;;
      -h|--help)      usage; return 0 ;;
      *) err "unknown option: $1"; usage; return 2 ;;
    esac
  done

  check_platform
  ensure_uv
  [ -n "$version" ] || version="$(resolve_version)"

  local spec="git+https://github.com/${REPO}@${version}"
  if [ "$dry_run" -eq 1 ]; then
    say "would install: uv tool install --force $spec"
    return 0
  fi

  say "installing media-ai (${version})…"
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
  --dry-run          print what would be installed and exit
USAGE
}

say() { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }
err() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; }

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
  # Unauthenticated GitHub API allows ~60 requests/hour/IP, which CI hits easily, so a
  # failure here degrades to DEFAULT_VERSION rather than aborting the install.
  local tag=""
  if command -v curl >/dev/null 2>&1; then
    tag="$(curl -fsSL --max-time 10 "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null \
           | sed -n 's/.*"tag_name" *: *"\([^"]*\)".*/\1/p' | head -n1 || true)"
  fi
  printf '%s' "${tag:-$DEFAULT_VERSION}"
}

check_path() {
  command -v media-ai >/dev/null 2>&1 && return 0
  local bin="$HOME/.local/bin"
  err "media-ai installed but is not on PATH."
  case "${SHELL:-}" in
    */zsh) err "  echo 'export PATH=\"$bin:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
    */fish) err "  fish_add_path $bin" ;;
    *)     err "  echo 'export PATH=\"$bin:\$PATH\"' >> ~/.bashrc && exec bash" ;;
  esac
  exit 1
}

self_test() {
  # The mock provider runs entirely locally, so this exercises the CLI, Pillow and
  # ffmpeg without a key or a network call.
  local tmp status=0
  tmp="$(mktemp -d)"
  media-ai image generate --provider mock --prompt "install check" \
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
    printf '      media-ai init\n' >&2
    return 0
  fi
  if [ -n "$skills_dest" ]; then
    media-ai init --skills-dest "$skills_dest" < /dev/tty || true
  else
    media-ai init < /dev/tty || true
  fi
}

main "$@"
