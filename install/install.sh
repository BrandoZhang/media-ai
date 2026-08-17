#!/usr/bin/env bash
# Installer for the CLI named in CLI_NAME below.
#
#   curl -fsSL https://raw.githubusercontent.com/BrandoZhang/media-ai/main/install/install.sh | bash
#
# Downloads the standalone bundle for this machine — the CLI, its dependencies and an
# ffmpeg, with a Python interpreter inside it — verifies its checksum, unpacks it, puts
# a symlink on PATH, self-tests offline, then hands over to `<cli> init`.
#
# **No Python is required, and none is installed.** That is the point of the default
# path: `uv tool install` needs an interpreter, a dependency resolution and an index it
# can reach, and each of those turns into a failed install on a machine whose owner does
# not think of themselves as a Python user. A bundle has one step — fetch a tarball —
# so the only thing that can go wrong is the download, which says so.
#
# `--from-source` keeps the old behaviour (install uv, install the package from git) for
# the cases a bundle cannot serve: a platform with no published asset, musl libc, a
# checkout, third-party binding plugins, or an extra the bundle was not frozen with —
# it carries OpenTelemetry but not the OS keychain. See docs/LIMITATIONS.md.
#
# Layout, and why it is not simply a directory that gets overwritten:
#
#     ~/.local/share/<cli>/versions/<version>/   the bundle, one directory per version
#     ~/.local/share/<cli>/current -> versions/<version>
#     ~/.local/bin/<cli> -> ~/.local/share/<cli>/current/<cli>
#
# An upgrade writes a *new* directory and moves the symlink, so it never overwrites the
# files of a running process — which matters because `<cli> upgrade` runs this script
# from inside the very bundle being replaced.
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

# This script's own path, when it has one. Under the documented `curl … | bash` it does
# not: `$0` is the string "bash", and printing "$0 --from-source" in a hint gives the
# reader `bash --from-source`, which starts an interactive shell. Hints in this project
# are contractual — usually runnable — so the two cases are distinguished here once, and
# `from_source_hint` below picks the right sentence.
SELF=""
case "$0" in
  */*|*.sh) [ -f "$0" ] && SELF="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/$(basename "$0")" ;;
esac

# The installation this script was *shipped inside*, if any. A bundled copy lives at
# `<home>/versions/<version>/_internal/install.sh`, which is how `<cli> upgrade` runs it —
# so it can work out which installation it belongs to instead of assuming the default
# one. Without this an upgrade of an install made with `--bin-dir` or `MEDIA_AI_HOME`
# quietly installs a *second* copy in the default place and leaves a stray symlink.
BUNDLE_HOME=""
RUNNING_BUNDLE=""
if [ -n "$SELF" ]; then
  _maybe_home="$(cd "$(dirname "$SELF")/../../.." 2>/dev/null && pwd || true)"
  if [ -n "$_maybe_home" ] && [ -d "$_maybe_home/versions" ]; then
    BUNDLE_HOME="$_maybe_home"
    RUNNING_BUNDLE="$(cd "$(dirname "$SELF")/.." && pwd)"
  fi
  unset _maybe_home
fi

# Where the bundle is unpacked and what goes on PATH. Branded, so two builds coexist
# rather than replace each other — the same reason `brand.config_dir()` is. The
# override variables are not branded, matching MEDIA_AI_REPO above: they are knobs for
# one invocation of this script, not a namespace anything else reads.
#
# Filled in by `resolve_layout`, which needs the flags parsed first. Declared here
# because every function below reads them, and because `install/test_installer.sh` sets
# them directly to drive an install into a scratch directory.
INSTALL_HOME=""
BIN_DIR=""

#: Written beside the versions so a later run — an upgrade especially — puts the command
#: back where the first install put it, rather than in the default place.
BIN_DIR_RECEIPT="bin-dir"

main() {
  local version="" skills_dest="" do_init=1 dry_run=0 do_uninstall=0 assume_yes=0 from_source=0 from_file=""
  local bin_dir_flag=""
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
      --bin-dir)      need_value "$@"; bin_dir_flag="$2"; shift 2 ;;
      --bin-dir=*)    bin_dir_flag="${1#*=}"; shift ;;
      --from-source)  from_source=1; shift ;;
      --from-file)    need_value "$@"; from_file="$2"; shift 2 ;;
      --from-file=*)  from_file="${1#*=}"; shift ;;
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

  resolve_layout "$bin_dir_flag"

  # Before anything that installs: fetching a package manager in order to uninstall
  # would be absurd, and the CLI may well have been installed some other way.
  if [ "$do_uninstall" -eq 1 ]; then
    run_uninstall "$assume_yes" "$dry_run" "${keep_flags[@]+"${keep_flags[@]}"}"
    return $?
  fi

  check_platform

  if [ -n "$from_file" ]; then
    # A named file settles both questions this script otherwise asks the network —
    # which version, and where to get it — so neither is asked.
    [ "$from_source" -eq 0 ] || { err "--from-file and --from-source ask for different things"; return 2; }
    install_file "$from_file" "$dry_run" || return $?
  else
    [ -n "$version" ] || version="$(resolve_version)"
    # A git ref that is not a release has no published bundle, and never will — a branch
    # is not a thing that gets built and uploaded. Source is not a fallback here, it is
    # the only reading of the request, so it is taken and said out loud.
    if [ "$from_source" -eq 0 ] && ! is_release_ref "$version"; then
      say "'$version' is a git ref, not a release; installing from source."
      from_source=1
    fi
    if [ "$from_source" -eq 1 ]; then
      install_from_source "$version" "$dry_run" || return $?
    else
      install_release "$version" "$dry_run" || return $?
    fi
  fi
  [ "$dry_run" -eq 0 ] || return 0

  # Self-test before the PATH check, and against the path it was installed to rather
  # than through PATH. Both halves matter when `--bin-dir` names somewhere the shell
  # does not look: the install genuinely worked, and being told so — before being told
  # to edit a shell rc — is the difference between "it failed" and "one more step".
  # `uv tool` puts its shim in the same directory, so this covers the source path too.
  local exe="$CLI_NAME"
  [ -x "$BIN_DIR/$CLI_NAME" ] && exe="$BIN_DIR/$CLI_NAME"
  self_test "$exe"
  check_path
  if [ "$do_init" -eq 1 ]; then run_init "$exe" "$skills_dest"; fi
  return 0
}

usage() {
  cat >&2 <<'USAGE'
usage: install.sh [options]

  --version REF      install this release (default: the latest published one).
                     A branch or sha implies --from-source.
  --bin-dir PATH     put the command here (default: ~/.local/bin)
  --skills-dest PATH install Agent Skills here without asking
  --no-init          skip the configuration wizard
  --dry-run          print what would be done and exit

  --from-source      build from git with uv instead of downloading a bundle.
                     Needs a network path to an index; gives you an installation
                     that extras and binding plugins can be added to.
  --from-file PATH   install a bundle already on disk (one you built with
                     packaging/build.sh, or downloaded yourself). Nothing is
                     fetched and nothing is checked against a published checksum.

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

# Where this install lives and what goes on PATH. Three sources each, most explicit
# first, and the interesting one is the middle: a *bundled* copy of this script belongs
# to the installation it shipped inside and upgrades that one, whatever the defaults
# say. The recorded bin directory does the same job for the symlink — `--bin-dir /opt/bin`
# on the first install has to still mean `/opt/bin` when the upgrade runs a year later,
# or the upgrade succeeds and leaves the command the user actually types pointing at the
# old version.
resolve_layout() {
  local bin_dir_flag="${1:-}"
  if [ -n "${MEDIA_AI_HOME:-}" ]; then
    INSTALL_HOME="$MEDIA_AI_HOME"
  elif [ -n "$BUNDLE_HOME" ]; then
    INSTALL_HOME="$BUNDLE_HOME"
  else
    INSTALL_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/$CLI_NAME"
  fi

  if [ -n "$bin_dir_flag" ]; then
    BIN_DIR="$bin_dir_flag"
  elif [ -n "${MEDIA_AI_BIN_DIR:-}" ]; then
    BIN_DIR="$MEDIA_AI_BIN_DIR"
  elif [ -s "$INSTALL_HOME/$BIN_DIR_RECEIPT" ]; then
    BIN_DIR="$(head -n1 "$INSTALL_HOME/$BIN_DIR_RECEIPT")"
  else
    BIN_DIR="$HOME/.local/bin"
  fi
}

# How to reach the source path from wherever this script is being read from. Not
# "$0 --from-source": under `curl … | bash` that renders as `bash --from-source`, and
# every refusal in this file ends by naming it — including the one every install hits
# today, since the newest release predates bundles and has no asset to download.
from_source_hint() {
  if [ -n "$SELF" ]; then
    printf 'bash %s --from-source' "$SELF"
  else
    printf 'curl -fsSL https://raw.githubusercontent.com/%s/main/install/install.sh | bash -s -- --from-source' "$REPO"
  fi
}

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

# >>> asset naming >>>
# What a release asset is called, and the OS/architecture pair it is named after.
#
# This block is duplicated **verbatim** in `install/install.sh` and `packaging/build.sh`:
# the builder uses it to name the tarball and the installer uses it to ask for one, so
# the two agreeing *is* the contract between them — and they cannot share a file,
# because the installer is fetched on its own by curl and the builder runs from a
# checkout. `tests/test_packaging.py` compares the text of the two copies byte for byte,
# so a change made in one is a test failure rather than a release whose assets no
# installer can find.
#
# `platform_triple` is deliberately just `uname`: whether a bundle will actually *run*
# is a separate question (see `refuse_musl` in the installer), and folding it in here
# would make the two copies answer different questions.
#
# `asset_name` takes the CLI's name rather than reading a global, because the two
# scripts learn it differently — the installer has it pinned at the top, the builder
# reads it out of the package it just installed.
platform_triple() {
  local os arch
  case "$(uname -s)" in
    Linux)  os="linux" ;;
    Darwin) os="macos" ;;
    *) return 1 ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64)  arch="x86_64" ;;
    arm64|aarch64) arch="arm64" ;;
    *) return 1 ;;
  esac
  printf '%s-%s' "$os" "$arch"
}

# asset_name <cli> <version-or-tag> <triple>. The leading `v` of a tag is stripped: the
# tag is `v1.2.3` and the file inside the release is `<cli>-1.2.3-<triple>.tar.gz`.
asset_name() { printf '%s-%s-%s.tar.gz' "$1" "${2#v}" "$3"; }
# <<< asset naming <<<

# A glibc bundle does not run on musl, and what it prints when it tries is the dynamic
# loader's bare "not found" — a message that names the file it just found and offers no
# cause at all. Catch it here, where there is still something useful to say.
refuse_musl() {
  [ "$(uname -s)" = "Linux" ] || return 0
  if ldd --version 2>&1 | head -n1 | grep -qi musl; then
    err "this looks like a musl system (Alpine); the published bundles are built against glibc."
    err "  install from source instead: $(from_source_hint)"
    exit 1
  fi
}

# True for something that could be a published release tag. Nothing else can have a
# bundle: assets are attached to releases, and a branch never becomes one.
is_release_ref() {
  case "${1#v}" in
    [0-9]*.[0-9]*.[0-9]*) return 0 ;;
    *) return 1 ;;
  esac
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

# Where that asset lives. Only the installer needs this half — the builder writes a
# file, it does not publish one — so it stays outside the shared block above.
asset_url() {
  printf 'https://github.com/%s/releases/download/v%s/%s' "$REPO" "${1#v}" "$(asset_name "$CLI_NAME" "$1" "$2")"
}

# --------------------------------------------------------------- the bundle path

install_release() {
  local version="$1" dry_run="$2" triple url
  refuse_musl
  triple="$(platform_triple)" || {
    err "no published bundle for $(uname -s)/$(uname -m)."
    err "  install from source instead: $(from_source_hint)"
    return 1
  }
  url="$(asset_url "$version" "$triple")"

  if [ "$dry_run" -eq 1 ]; then
    say "would download: $url"
    say "would unpack to: $INSTALL_HOME/versions/${version#v}"
    say "would link:     $BIN_DIR/$CLI_NAME -> $INSTALL_HOME/current/$CLI_NAME"
    return 0
  fi

  # One scratch directory per install, cleaned by one trap, and the trap lives *here*
  # rather than in `unpack_and_link`. Bash RETURN traps are not stacked per frame: a
  # trap set in the callee replaces the caller's, fires on the callee's return, and
  # leaves the caller's directory behind — which for this function is the ~47 MB tarball
  # it just downloaded, left in /tmp on every single install.
  local tmp
  tmp="$(mktemp -d)"
  # Expanded now, deliberately: $tmp is a local and is out of scope by the time the
  # trap fires.
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN

  local tarball
  tarball="$tmp/$(asset_name "$CLI_NAME" "$version" "$triple")"
  say "downloading ${DIST_NAME} ${version} for ${triple}…"
  if ! fetch "$url" "$tarball"; then
    err "could not download $url"
    err "  the release may not publish a bundle for this platform; try: $(from_source_hint)"
    return 1
  fi
  verify_checksum "$tarball" "$url.sha256" || return 1
  unpack_and_link "$tarball" "${version#v}" "$tmp/unpack"
}

# Install a tarball already on disk — one built by `packaging/build.sh`, or downloaded
# by hand onto a machine with no route to GitHub. Nothing is verified against a
# published checksum here, and that is the honest description of what was asked for:
# the caller named a specific file, so the file *is* the decision. The version comes out
# of its name, which is why the naming block above is a contract rather than a
# convention — a tarball this cannot parse is refused rather than installed under a
# guess.
install_file() {
  local path="$1" dry_run="$2" version
  [ -f "$path" ] || { err "no such bundle: $path"; return 1; }
  version="$(version_from_asset "$(basename "$path")")" || {
    err "cannot tell which version $(basename "$path") is."
    err "  expected a name like $(asset_name "$CLI_NAME" 1.2.3 "$(platform_triple || echo linux-x86_64)")"
    return 1
  }
  if [ "$dry_run" -eq 1 ]; then
    say "would unpack: $path"
    say "          to: $INSTALL_HOME/versions/$version"
    say "would link:   $BIN_DIR/$CLI_NAME -> $INSTALL_HOME/current/$CLI_NAME"
    return 0
  fi
  local tmp
  tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN
  unpack_and_link "$path" "$version" "$tmp/unpack"
}

# `<cli>-1.2.3-linux-x86_64.tar.gz` -> `1.2.3`. Refuses anything else.
version_from_asset() {
  local rest="$1"
  rest="${rest%.tar.gz}"
  rest="${rest#"$CLI_NAME"-}"
  # Two trailing segments are the platform triple; what is left has to be a version.
  rest="${rest%-*}"
  rest="${rest%-*}"
  is_release_ref "$rest" || return 1
  printf '%s' "$rest"
}

# Unpack a tarball into `versions/<version>` and point `current` and the command at it.
# `work` is a scratch directory owned by the **caller**, which also owns the trap that
# removes it — see the note in `install_release`.
unpack_and_link() {
  local tarball="$1" version="$2" work="$3" dest="$INSTALL_HOME/versions/$2"

  # Never the directory this script is being read out of. `<cli> upgrade --version
  # <the version already installed>` lands here — an explicit `--version` bypasses the
  # "already current" short-circuit in `cli/upgrade.py` — and the `rm -rf` below would
  # delete the running executable, its `_internal/`, and this file, which bash reads
  # incrementally as it executes. The versioned layout makes replacing a running build
  # safe precisely because the new one goes somewhere else; asking for the same version
  # is the one request that defeats that, so it is refused rather than made to work.
  if [ -n "$RUNNING_BUNDLE" ] && [ "$dest" = "$RUNNING_BUNDLE" ]; then
    err "refusing to replace $dest: this installer is running from inside it."
    err "  that installation is already $version. Name a different version, or run"
    err "  the installer from outside the bundle."
    return 1
  fi

  # Unpack beside the destination and move into place, rather than into it. A tar that
  # dies halfway otherwise leaves a directory that looks installed, and the next run
  # finds a version already present and does nothing about it.
  say "unpacking…"
  mkdir -p "$work" "$INSTALL_HOME/versions"
  tar -xzf "$tarball" -C "$work"
  [ -x "$work/$CLI_NAME/$CLI_NAME" ] || { err "that bundle has no $CLI_NAME executable in it"; return 1; }
  rm -rf "$dest"
  mv "$work/$CLI_NAME" "$dest"

  # What `current` pointed at before the flip. Kept — see prune_versions.
  local previous=""
  [ -L "$INSTALL_HOME/current" ] && previous="$(basename "$(readlink "$INSTALL_HOME/current")")"

  # `-n` is load-bearing, and it is the reason this is not a `mv`. Both `ln -sf` and
  # `mv -f` *follow* a destination that is a symlink to a directory, so replacing
  # `current` without it writes the new link **inside the old version's directory** and
  # leaves `current` pointing where it did — an upgrade that reports success and
  # changes nothing. `-n` (`-h` on BSD, accepted as `-n` there too) says "the
  # destination is a link, not a place to put things". `install/test_installer.sh`
  # upgrades a fixture bundle twice and reads the link back, because this failure is
  # invisible from anything short of that.
  # A `current` that is a real directory can only come from a layout older than this
  # one; `-n` would refuse it, so it goes first.
  [ -L "$INSTALL_HOME/current" ] || rm -rf "$INSTALL_HOME/current"
  ln -sfn "versions/$version" "$INSTALL_HOME/current"

  mkdir -p "$BIN_DIR"
  ln -sfn "$INSTALL_HOME/current/$CLI_NAME" "$BIN_DIR/$CLI_NAME"
  # So the next run — an upgrade a year later, run by the CLI itself — puts the command
  # back here rather than in the default place. See `resolve_layout`.
  printf '%s\n' "$BIN_DIR" > "$INSTALL_HOME/$BIN_DIR_RECEIPT"

  prune_versions "$version" "$previous"
  say "installed to $dest"
}

# The two ways this script reaches the network, and the only two. They are functions
# rather than inline `curl` lines so `install/test_installer.sh` can replace them with a
# local fixture and drive the whole install — download, checksum, unpack, symlink,
# prune — offline. The unpack-and-link half is where the interesting mistakes live
# (a `ln -sf` that lands *inside* the directory it should replace, a prune that deletes
# the bundle the running process is executing from), and none of them are visible from
# a dry run.
fetch() {
  need_curl
  curl -fsSL --retry 3 --retry-delay 1 -o "$2" "$1"
}

fetch_text() {
  need_curl
  curl -fsSL --max-time 20 "$1" 2>/dev/null
}

need_curl() {
  command -v curl >/dev/null 2>&1 && return 0
  err "curl is required to download the release bundle. Install curl, or use --from-source."
  exit 1
}

verify_checksum() {
  # The published `.sha256` is fetched and compared by hand rather than piped into
  # `sha256sum -c`, because `-c` reads a *filename* out of the file and would look for
  # it relative to the current directory — which is not where the download is. The
  # comparison is on the hash alone.
  local tarball="$1" url="$2" published actual
  published="$(fetch_text "$url" | awk 'NR==1{print $1}')" || published=""
  if [ -z "$published" ]; then
    err "no checksum published at $url; refusing to install an unverified bundle."
    err "  a release that predates checksums can still be installed with --from-source."
    return 1
  fi
  actual="$(sha256_of "$tarball")" || return 1
  if [ "$actual" != "$published" ]; then
    err "checksum mismatch for $(basename "$tarball")"
    err "  published: $published"
    err "  got:       $actual"
    return 1
  fi
  say "checksum ok"
}

sha256_of() {
  # macOS ships `shasum`, Linux `sha256sum`, and anything with OpenSSL has the third.
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 -r "$1" | awk '{print $1}'
  else
    err "no sha256 tool (sha256sum, shasum or openssl) to verify the download with"
    return 1
  fi
}

prune_versions() {
  # Keep the new bundle and the one it replaced; drop everything older. Two, not one,
  # because `<cli> upgrade` runs this script *from inside* the bundle being replaced —
  # deleting it at this point would pull the files out from under the process still
  # executing. Keeping every version instead would leave ~100 MB behind per upgrade,
  # which is the kind of accumulation the re-runnable-installer rule exists to prevent.
  local keep_new="$1" keep_old="$2" name dir
  for dir in "$INSTALL_HOME"/versions/*; do
    [ -d "$dir" ] || continue
    name="$(basename "$dir")"
    [ "$name" = "$keep_new" ] && continue
    [ -n "$keep_old" ] && [ "$name" = "$keep_old" ] && continue
    rm -rf "$dir"
  done
}

# --------------------------------------------------------------- the source path

install_from_source() {
  local version="$1" dry_run="$2"
  local spec="git+https://github.com/${REPO}@${version}"
  if [ "$dry_run" -eq 1 ]; then
    say "would install: uv tool install --force $spec"
    return 0
  fi
  ensure_uv
  say "installing ${DIST_NAME} (${version}) from source…"
  uv tool install --force "$spec" >&2
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

# --------------------------------------------------------------- after either path

# Loud, and deliberately **not** fatal.
#
# The install succeeded — the self-test above ran the installed executable and it
# worked. What is left is the user's shell configuration, which is not this script's to
# get wrong, and exiting non-zero here made a successful install report failure. That
# mattered in one place in particular: `<cli> upgrade` runs this script as a child whose
# PATH need not contain the bin directory, turning every such upgrade into
# `upgrade_failed` over a working installation.
#
# The message stays exactly as loud as it was; only the exit code changed.
check_path() {
  command -v "$CLI_NAME" >/dev/null 2>&1 && return 0
  err "${CLI_NAME} is installed at $BIN_DIR/$CLI_NAME but that is not on your PATH."
  case "${SHELL:-}" in
    */zsh) err "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
    */fish) err "  fish_add_path $BIN_DIR" ;;
    *)     err "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc && exec bash" ;;
  esac
  return 0
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
  local exe="${1:-$CLI_NAME}" tmp status=0
  tmp="$(mktemp -d)"
  MEDIA_USAGE_LOG="$tmp/usage.jsonl" "$exe" image generate --binding mock/mock --prompt "install check" \
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
  # By path, not through PATH: `check_path` is a warning now, so the wizard has to be
  # reachable even on the install that just triggered it.
  local exe="$1" skills_dest="${2:-}"
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
    "$exe" init --skills-dest "$skills_dest" < /dev/tty >/dev/null || true
  else
    "$exe" init < /dev/tty >/dev/null || true
  fi
}

# --------------------------------------------------------------------- removal

run_uninstall() {
  # Two halves, in this order: the CLI removes what it wrote (skills, and — only if
  # asked — the config files), then this removes the CLI. It cannot be done the other
  # way round, because the first half runs the CLI.
  #
  # The second half tries both install shapes rather than detecting one. A machine can
  # genuinely have both — somebody who installed from source and later re-ran the
  # installer — and leaving whichever was not detected behind would leave a `<cli>` on
  # PATH after an uninstall that reported success.
  local assume_yes="$1" dry_run="$2"
  shift 2
  local flags=("$@") have_tty=1

  if [ "$dry_run" -eq 1 ]; then flags+=(--dry-run); fi
  # Same /dev/tty test as run_init: under `curl … | bash` the pipe owns stdin, and
  # with no terminal at all the wizard must not wait for an answer that cannot come.
  # Without one, --yes is implied: everything goes except what a --keep-* flag holds back.
  : 2>/dev/null < /dev/tty || have_tty=0
  if [ "$assume_yes" -eq 1 ] || [ "$have_tty" -eq 0 ]; then flags+=(--yes); fi

  # The bin directory first, since `resolve_layout` recovered the one this installation
  # actually used — an install made with `--bin-dir` need not be on PATH at all, and
  # skipping the skill removal for that reason would leave files behind while reporting
  # a clean uninstall.
  local exe=""
  if [ -x "$BIN_DIR/$CLI_NAME" ]; then exe="$BIN_DIR/$CLI_NAME"
  elif command -v "$CLI_NAME" >/dev/null 2>&1; then exe="$CLI_NAME"
  fi
  if [ -n "$exe" ]; then
    say "removing installed Agent Skills and configuration…"
    if [ "$have_tty" -eq 1 ]; then
      "$exe" uninstall "${flags[@]+"${flags[@]}"}" < /dev/tty >/dev/null || true
    else
      "$exe" uninstall "${flags[@]+"${flags[@]}"}" >/dev/null || true
    fi
  else
    err "${CLI_NAME} is not on PATH; skipping skill removal (run '${CLI_NAME} uninstall' yourself if it is installed elsewhere)"
  fi

  local removed=0
  remove_bundle "$dry_run" && removed=1
  remove_source_install "$dry_run" && removed=1
  if [ "$removed" -eq 0 ]; then
    err "found no ${DIST_NAME} installed by this script; remove it however you installed it:"
    err "  pip uninstall ${DIST_NAME}"
  fi
  return 0
}

remove_bundle() {
  local dry_run="$1" link="$BIN_DIR/$CLI_NAME"
  [ -d "$INSTALL_HOME" ] || [ -L "$link" ] || return 1
  if [ "$dry_run" -eq 1 ]; then
    say "would remove: $INSTALL_HOME and $link"
    return 0
  fi
  say "removing the ${CLI_NAME} bundle…"
  # -L, not -e: the link is dangling by the time the directory it points into is gone,
  # and an uninstall that leaves a broken command on PATH is the worse of the two.
  [ -L "$link" ] && rm -f "$link"
  rm -rf "$INSTALL_HOME"
  return 0
}

remove_source_install() {
  local dry_run="$1"
  command -v uv >/dev/null 2>&1 || return 1
  uv tool list 2>/dev/null | grep -q "^${DIST_NAME}" || return 1
  if [ "$dry_run" -eq 1 ]; then
    say "would run: uv tool uninstall ${DIST_NAME}"
    return 0
  fi
  say "removing the source install of ${CLI_NAME}…"
  uv tool uninstall "$DIST_NAME" >&2
  return 0
}

main "$@"
