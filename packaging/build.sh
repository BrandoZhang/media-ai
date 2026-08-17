#!/usr/bin/env bash
# Build the standalone bundle for the machine this runs on, and tar it up.
#
#   bash packaging/build.sh                    # -> dist/<cli>-<version>-<os>-<arch>.tar.gz
#   bash packaging/build.sh --output /tmp/out  # somewhere else
#
# One bundle per (OS, architecture): a frozen build carries a compiled interpreter, the
# Pillow extension modules and an ffmpeg binary, none of which cross-compile. So this
# script does not take a target — it builds for `uname`, and the release workflow runs
# it once per runner. The `asset naming` block below is what names the result, and it is
# also what `install/install.sh` uses to ask for it; see the note above it.
#
# The build happens in a throwaway virtualenv rather than in `.venv`, because what goes
# into the bundle is exactly what is installed beside PyInstaller: a dev environment
# carries pytest, ruff and whatever was last experimented with, and `collect_submodules`
# plus a stray import would carry some of it along.

set -euo pipefail

# Pinned rather than floated. PyInstaller ships a compiled bootloader and decides how
# the archive is laid out, so an unpinned build tool would change what a release
# artifact *is* between two releases with no diff to point at. Bumping it is a normal
# dependency bump: change the line, run this, run the smoke test it ends with.
PYINSTALLER_VERSION="${MEDIA_AI_PYINSTALLER_VERSION:-6.22.1}"

#: The optional extras frozen into the bundle, as a `pyproject.toml` extras list.
#:
#: This is the one decision a bundle cannot leave to the user, so it is made here and
#: written down. An extra exists because not every installation should carry it — but
#: "should" assumes there is a later moment to add it, and for a frozen build there is
#: none: no environment, no package manager, nothing to `pip install` into. So the
#: choice is *ship it or make it permanently unavailable*, and it gets made per extra:
#:
#: ``otel``
#:     Shipped. Telemetry is still off by default and still costs nothing when off (the
#:     SDK is imported lazily, only when enabled), so the download is the whole price —
#:     and an operator who turns telemetry on and gets a `telemetry_unavailable` notice
#:     they cannot act on is the failure this avoids.
#:
#: Not shipped: ``keychain``, which is genuinely optional in a way ``otel`` is not —
#: `keyring` reaches an OS service that may not exist, and every binding can name an
#: `env://` or `cred://` source instead. ``docs/LIMITATIONS.md`` says so out loud.
BUNDLE_EXTRAS="${MEDIA_AI_BUNDLE_EXTRAS:-otel}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

main() {
  local output="$ROOT/dist" keep_work=0 python="${MEDIA_AI_BUILD_PYTHON:-python3}"

  while [ $# -gt 0 ]; do
    case "$1" in
      --output)   need_value "$@"; output="$2"; shift 2 ;;
      --output=*) output="${1#*=}"; shift ;;
      --python)   need_value "$@"; python="$2"; shift 2 ;;
      --python=*) python="${1#*=}"; shift ;;
      --keep-work) keep_work=1; shift ;;
      -h|--help)  usage; return 0 ;;
      *) err "unknown option: $1"; usage; return 2 ;;
    esac
  done

  local triple
  triple="$(platform_triple)" || {
    err "no standalone build for $(uname -s)/$(uname -m); install from source instead:"
    err "  bash install/install.sh --from-source"
    return 1
  }

  local work venv
  work="$(mktemp -d)"
  # Expanded now, deliberately: $work is a local and is out of scope at exit.
  # shellcheck disable=SC2064
  [ "$keep_work" -eq 1 ] || trap "rm -rf '$work'" EXIT
  venv="$work/venv"

  # What goes into the bundle is exactly what is installed here, so the extras are
  # applied to the project requirement rather than added afterwards — `collect_submodules`
  # and the hooks read the environment, not a list in the spec.
  local project="$ROOT"
  # `${ROOT}[…]`, not `$ROOT[…]`: unbraced, the bracket reads as an array subscript.
  [ -z "$BUNDLE_EXTRAS" ] || project="${ROOT}[${BUNDLE_EXTRAS}]"

  say "creating the build environment ($python + pyinstaller $PYINSTALLER_VERSION)…"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python "$python" "$venv" >&2
    uv pip install --python "$venv/bin/python" "$project" "pyinstaller==$PYINSTALLER_VERSION" >&2
  else
    "$python" -m venv "$venv" >&2
    "$venv/bin/python" -m pip install --quiet --upgrade pip >&2
    "$venv/bin/python" -m pip install --quiet "$project" "pyinstaller==$PYINSTALLER_VERSION" >&2
  fi

  # Both read out of the build environment rather than off a line in this file: the
  # name and the version each have exactly one declaration in the package, and a
  # tarball labelled with a second copy of either is how a release ships an asset the
  # installer then cannot find.
  local cli version
  cli="$("$venv/bin/python" -c 'import media_ai.brand as b; print(b.CLI_NAME)')"
  version="$("$venv/bin/python" -c 'import media_ai; print(media_ai.__version__)')"

  say "building $cli $version for $triple…"
  ( cd "$work" && "$venv/bin/pyinstaller" --noconfirm --clean --log-level WARN \
      --distpath "$work/dist" --workpath "$work/build" "$ROOT/packaging/standalone.spec" >&2 )

  local bundle="$work/dist/$cli"
  [ -x "$bundle/$cli" ] || { err "the build produced no executable at $bundle/$cli"; return 1; }
  fix_permissions "$bundle"
  smoke_test "$bundle/$cli" "$cli" "$version" "$work/scratch"

  # Resolved to an absolute path before anything uses it. `tar` runs from inside the
  # build directory (it packs `<cli>/`, not a path with `$work` in it), so a relative
  # `--output dist` would otherwise be written relative to *that* — which is how CI
  # first failed here, with `tar: dist/…: No such file or directory` from a script that
  # had just created `dist/` a line earlier.
  mkdir -p "$output"
  output="$(cd "$output" && pwd)"
  local asset
  asset="$output/$(asset_name "$cli" "$version" "$triple")"
  say "packing $asset…"
  # COPYFILE_DISABLE stops BSD tar from writing an AppleDouble `._file` beside every
  # entry that carries an extended attribute, which is most of them on macOS.
  ( cd "$work/dist" && COPYFILE_DISABLE=1 tar -czf "$asset" "$cli" )
  checksum "$asset" > "$asset.sha256"

  say "done:"
  printf '  %s (%s)\n  %s\n' "$asset" "$(du -h "$asset" | cut -f1)" "$asset.sha256" >&2
  # The one line on stdout, so a caller can capture the path.
  printf '%s\n' "$asset"
}

usage() {
  cat >&2 <<'USAGE'
usage: build.sh [options]

  --output DIR   write the tarball and its checksum here (default: ./dist)
  --python PATH  build against this interpreter (default: python3, >= 3.11)
  --keep-work    leave the scratch build directory in place for inspection
USAGE
}

say() { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }
err() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; }

need_value() {
  if [ $# -lt 2 ] || case "$2" in -*) true ;; *) false ;; esac; then
    err "$1 needs a value"
    usage
    exit 2
  fi
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

fix_permissions() {
  # PyInstaller collects the bundled ffmpeg as *data*, and a data file's mode is not
  # something it promises to carry across. An ffmpeg without its execute bit fails
  # `imageio_ffmpeg`'s own validity check, which then falls through to "no ffmpeg
  # found" — a message pointing at a missing dependency that is in fact sitting right
  # there. Cheap to assert unconditionally; expensive to debug from the error.
  local bundle="$1" found=0
  while IFS= read -r exe; do
    chmod +x "$exe"
    found=1
  done < <(find "$bundle" -type f -name 'ffmpeg*' ! -name '*.md')
  [ "$found" -eq 1 ] || { err "no bundled ffmpeg found under $bundle"; return 1; }
}

smoke_test() {
  # Run the thing that was just built, before anyone downloads it. Each step is here
  # for a different half of the bundle: `--version` proves the bootloader and the
  # frozen package work at all, the image proves Pillow's extension modules and the
  # binding manifests came along, the video and the animation prove the bundled ffmpeg
  # is present *and* executable, and `doctor` walks the packaged skills and the
  # credential machinery. Between them they cover everything the spec file has to get
  # right, which is why this is not optional and not a separate script.
  local exe="$1" cli="$2" version="$3" scratch="$4"
  mkdir -p "$scratch"
  # A scratch HOME so the build cannot read — or write — the builder's own config,
  # and the usage ledger goes with it rather than into the current directory.
  export HOME="$scratch" MEDIA_USAGE_LOG="$scratch/usage.jsonl"

  local got
  got="$("$exe" --version)"
  [ "$got" = "$cli $version" ] || { err "the bundle reports '$got', expected '$cli $version'"; return 1; }

  step "$exe" image generate --binding mock/mock --prompt "smoke test" --output "$scratch/probe.png"
  step "$exe" video generate --binding mock/mock --prompt "smoke test" --output "$scratch/probe.mp4"
  step "$exe" animation export --binding local/ffmpeg --input "$scratch/probe.mp4" \
       --output "$scratch/probe.webp" --max-width 160
  step "$exe" doctor
  case " $BUNDLE_EXTRAS " in *" otel "*) telemetry_test "$exe" "$scratch" ;; esac
  say "smoke test passed (offline, no key needed)"
}

telemetry_test() {
  # Prove the frozen SDK actually *exports*, not merely that it imports.
  #
  # This is the one part of the bundle that fails politely by design: with the SDK
  # missing the CLI degrades to a no-op and says so in `notices[]`, which is correct
  # behaviour for a pip install and a shipped bug for a bundle that is supposed to
  # carry it. So a bundle that quietly stopped collecting OpenTelemetry would pass
  # every other step here — and go on passing until an operator turned telemetry on.
  #
  # The console exporter, to stderr, is what makes this checkable with no collector: it
  # is also the path that would break first, since OpenTelemetry resolves its context
  # runtime and its exporters through entry points, and entry points need distribution
  # metadata that a freeze does not carry unless it is told to.
  local exe="$1" scratch="$2" out
  out="$(MEDIA_TELEMETRY=1 MEDIA_TELEMETRY_EXPORTER=console \
         "$exe" image generate --binding mock/mock --prompt "telemetry check" \
         --output "$scratch/otel.png" 2>&1 >"$scratch/otel.json")" || {
    err "the bundle failed with telemetry on:"
    printf '%s\n' "$out" | sed 's/^/    /' >&2
    return 1
  }
  if grep -q '"kind": *"telemetry_unavailable"' "$scratch/otel.json"; then
    err "the bundle was built with the otel extra but reports the SDK as missing:"
    sed 's/^/    /' "$scratch/otel.json" >&2
    return 1
  fi
  # A span reaches stderr, and stdout stays the one JSON object it is contractually
  # required to be — the console exporters are constructed with `out=sys.stderr` for
  # exactly this reason, and a freeze is a fine place for that to silently regress.
  grep -q '"name":' <<<"$out" || { err "telemetry is on but no span was exported"; return 1; }
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$scratch/otel.json" \
    || { err "telemetry on stdout: the result is no longer one JSON object"; return 1; }
  say "telemetry check passed (spans on stderr, stdout still one JSON object)"
}

step() {
  local out
  if ! out="$("$@" 2>&1)"; then
    err "smoke test failed: $*"
    printf '%s\n' "$out" | sed 's/^/    /' >&2
    return 1
  fi
}

checksum() {
  # Whichever of the three is on the box. macOS ships `shasum`, Linux `sha256sum`, and
  # anything with OpenSSL has the third. The output shape is `<hash>  <name>`, which is
  # what `shasum -c` and `sha256sum -c` both read — the installer does not use `-c`
  # (see `verify_checksum` there), but a human checking by hand should not have to
  # reshape it first.
  local path="$1" name
  name="$(basename "$path")"
  if command -v sha256sum >/dev/null 2>&1; then
    ( cd "$(dirname "$path")" && sha256sum "$name" )
  elif command -v shasum >/dev/null 2>&1; then
    ( cd "$(dirname "$path")" && shasum -a 256 "$name" )
  elif command -v openssl >/dev/null 2>&1; then
    printf '%s  %s\n' "$(openssl dgst -sha256 -r "$path" | cut -d' ' -f1)" "$name"
  else
    err "no sha256 tool (sha256sum, shasum or openssl) to checksum the release asset"
    return 1
  fi
}

main "$@"
