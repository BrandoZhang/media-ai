#!/usr/bin/env bash
# Unit tests for install.sh, run offline.
#
# The installer is the one file in this repo that nothing else can cover: it runs
# *before* anything is installed, so it cannot import the package, and its happy path
# ends with a 100 MB download. Two halves are worth testing anyway, and both have
# already been wrong:
#
# 1. **The release-tag parser.** It reads GitHub's JSON with grep/sed rather than a JSON
#    library, because there is no interpreter to lean on yet. The first version used one
#    greedy sed and silently returned the *oldest* release in the page, since the API
#    answers with compact single-line JSON.
#
# 2. **Unpack, link and prune.** `install.sh` reaches the network through exactly two
#    functions (`fetch`, `fetch_text`), which is what lets this file replace them with a
#    local fixture and then drive a real install into a scratch directory — checksum
#    check included. The mistakes here are the invisible kind: `ln -sf` over a symlink to
#    a directory creates a link *inside* it rather than replacing it, and a prune that
#    keeps only the newest version deletes the bundle whose own `upgrade` is running.
#
# The installer's `main` is never called: this sources everything above it and calls the
# functions directly. How it does that is load-bearing — see the note above the `source`
# below.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail=0
pass() { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s: %s\n' "$1" "$2"; fail=1; }

equal() {
  local name="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then pass "$name"; else bad "$name" "want $(printf %q "$want"), got $(printf %q "$got")"; fi
}

# Source the functions without running main().
#
# Through a **regular file**, and cut at an **explicit marker**. The obvious spelling,
# `source <(sed '$ d' install.sh)`, is wrong twice:
#
# 1. macOS ships bash 3.2, which can finish reading a `/dev/fd` process substitution
#    before `sed` has written all of a script this long — so the functions defined near
#    the end are simply absent, and the run dies with `parse_tag: command not found`
#    while loading its own subject. A regular file is complete before it is read.
# 2. `sed '$ d'` deletes the last *physical* line, which is only the entry point for as
#    long as nobody adds a trailing comment or blank line under it. The day someone does,
#    this file runs a real install of a real release into a developer's `~/.local` as a
#    side effect of running the unit tests. Cutting at `main "$@"` says what is meant, and
#    a missing marker is a refusal rather than a guess.
ENTRY_POINT='main "$@"'
grep -qxF "$ENTRY_POINT" "$HERE/install.sh" || {
  printf 'install.sh no longer ends with the %s entry point; refusing to source it\n' "$ENTRY_POINT" >&2
  exit 1
}
PREFIX="$(mktemp "${TMPDIR:-/tmp}/installer-prefix.XXXXXX")"
# Removed on the way out as well as after the source: `set -e` and a syntax error in
# install.sh would otherwise leave one copy of it in TMPDIR per run — an accumulation
# per run being the one thing the re-runnable-installer rule exists to prevent. The trap
# is cleared again immediately, so it cannot be the one the scratch directory needs.
trap 'rm -f "$PREFIX"' EXIT
# awk on a whole-line string comparison, so the marker needs no regex escaping — `$@`
# and the quotes around it are matched as the literal text they are.
awk -v marker="$ENTRY_POINT" '$0 == marker { exit } { print }' "$HERE/install.sh" > "$PREFIX"
# shellcheck disable=SC1090
source "$PREFIX"
rm -f "$PREFIX"
trap - EXIT

# --------------------------------------------------------------------- parse_tag

echo "parse_tag:"

check_tag() {
  local name="$1" payload="$2" want="$3" got
  got="$(printf '%s' "$payload" | parse_tag || true)"
  equal "$name" "$got" "$want"
}

check_tag "single release" \
  '[{"tag_name":"v0.2.0","prerelease":true,"draft":false}]' \
  "v0.2.0"

# The bug that motivated this file: newest-first, so the FIRST match wins.
check_tag "multiple releases on one line picks the newest" \
  '[{"tag_name":"v0.3.0","prerelease":true},{"tag_name":"v0.2.0","prerelease":true}]' \
  "v0.3.0"

check_tag "whitespace around the colon" \
  '[{ "tag_name" : "v1.0.0" }]' \
  "v1.0.0"

check_tag "pretty-printed across lines" \
  '[
  {
    "tag_name": "v2.0.0",
    "prerelease": false
  }
]' \
  "v2.0.0"

# A release body mentioning tag_name in prose must not be mistaken for the field.
check_tag "prose in the body does not shadow the field" \
  '[{"tag_name":"v0.4.0","body":"see tag_name: v0.1.0 for details"}]' \
  "v0.4.0"

check_tag "no releases yet falls through to the caller's default" '[]' ""
check_tag "error object instead of a list" \
  '{"message":"Not Found","documentation_url":"https://docs.github.com/"}' \
  ""
check_tag "empty response" "" ""

# ------------------------------------------------------------------- asset naming

echo
echo "hints:"

# Under the documented `curl … | bash`, `$0` is the string "bash" — so a hint built from
# it reads `bash --from-source`, which starts an interactive shell instead of installing
# anything. Every refusal in the installer ends by naming this, including the one every
# install hits while the newest release still predates bundles.
# SELF and RUNNING_BUNDLE below are read by the sourced installer, not by this file.
# shellcheck disable=SC2034
SELF="" # as when piped from curl
case "$(from_source_hint)" in
  curl*--from-source) pass "piped from curl: the hint is the documented one-liner" ;;
  *) bad "piped from curl" "got $(from_source_hint)" ;;
esac
# shellcheck disable=SC2034
SELF="$HERE/install.sh"
equal "run as a file: the hint names the file" \
  "$(from_source_hint)" "bash $HERE/install.sh --from-source"

echo
echo "asset naming:"

# The name the builder writes and the name the installer asks for are one decision made
# twice (see the `platform_triple` note in both files). These pin the installer's half.
equal "a tag names an asset without the leading v" \
  "$(asset_name "$CLI_NAME" v1.2.3 linux-x86_64)" "$CLI_NAME-1.2.3-linux-x86_64.tar.gz"
equal "a bare version names the same asset" \
  "$(asset_name "$CLI_NAME" 1.2.3 macos-arm64)" "$CLI_NAME-1.2.3-macos-arm64.tar.gz"
equal "the download URL hangs off the v-prefixed tag" \
  "$(asset_url 1.2.3 macos-arm64)" \
  "https://github.com/$REPO/releases/download/v1.2.3/$CLI_NAME-1.2.3-macos-arm64.tar.gz"

# `--from-file` reads the version back out of the name, which is the other half of that
# contract: a tarball whose name this cannot parse must be refused, not installed under
# a guess about which version it holds.
equal "the version is recovered from an asset name" \
  "$(version_from_asset "$CLI_NAME-1.2.3-linux-x86_64.tar.gz")" "1.2.3"
equal "…on macOS too" \
  "$(version_from_asset "$CLI_NAME-0.7.1-macos-arm64.tar.gz")" "0.7.1"
equal "…and round-trips whatever asset_name writes" \
  "$(version_from_asset "$(asset_name "$CLI_NAME" v9.10.11 macos-arm64)")" "9.10.11"
for name in "$CLI_NAME.tar.gz" "something-else-1.0.0-linux-x86_64.tar.gz" "$CLI_NAME-main-linux-x86_64.tar.gz"; do
  if version_from_asset "$name" >/dev/null 2>&1; then
    bad "unparseable asset name: $name" "accepted"
  else
    pass "unparseable asset name: $name"
  fi
done

# A branch or a sha has no published bundle and never will, so the installer must send
# it down the source path rather than fetching a 404.
for ref in v0.7.1 0.7.1 1.10.0 v2.0.0-rc1; do
  if is_release_ref "$ref"; then pass "release ref: $ref"; else bad "release ref: $ref" "rejected"; fi
done
for ref in main HEAD feature/x 1a2b3c4 ""; do
  if is_release_ref "$ref"; then bad "not a release ref: ${ref:-<empty>}" "accepted"; else pass "not a release ref: ${ref:-<empty>}"; fi
done

# ----------------------------------------------------------- install, unpack, link

echo
echo "install_release (offline, against a fixture bundle):"

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

# A stand-in for a released bundle: a directory named after the CLI holding an
# executable that answers `--version`. The installer never looks inside further than
# that, which is the point — the smoke test is `packaging/build.sh`'s job.
make_fixture() {
  local version="$1" triple="$2" root="$SCRATCH/releases" stage
  stage="$(mktemp -d)"
  mkdir -p "$stage/$CLI_NAME" "$root"
  printf '#!/bin/sh\necho "%s %s"\n' "$CLI_NAME" "$version" > "$stage/$CLI_NAME/$CLI_NAME"
  chmod +x "$stage/$CLI_NAME/$CLI_NAME"
  ( cd "$stage" && tar -czf "$root/$(asset_name "$CLI_NAME" "$version" "$triple")" "$CLI_NAME" )
  sha256_of "$root/$(asset_name "$CLI_NAME" "$version" "$triple")" > "$root/$(asset_name "$CLI_NAME" "$version" "$triple").sha256"
  rm -rf "$stage"
}

# Replace the only two functions that touch the network. Everything downstream —
# checksum verification, tar, the symlink flip, the prune — runs for real.
#
# Both codes, because which one gets reported depends on the linter's version: 0.10 and
# earlier call an overridden definition unreachable (SC2317), 0.11 calls it never
# invoked (SC2329). It is neither — `install_release` calls them, from the other file.
# (Take care with the wrapping here: a comment line whose first word is the linter's
# own name is read as a directive, and an unparseable one is an error, not a warning.)
# shellcheck disable=SC2317,SC2329
fetch() { cp "$SCRATCH/releases/$(basename "$1")" "$2" 2>/dev/null; }
# shellcheck disable=SC2317,SC2329
fetch_text() { cat "$SCRATCH/releases/$(basename "$1")" 2>/dev/null; }

INSTALL_HOME="$SCRATCH/share/$CLI_NAME"
BIN_DIR="$SCRATCH/bin"
TRIPLE="$(platform_triple)"

make_fixture 1.0.0 "$TRIPLE"
if install_release v1.0.0 0 >/dev/null 2>&1; then pass "a first install succeeds"; else bad "a first install succeeds" "install_release returned $?"; fi
equal "the command runs from PATH" "$("$BIN_DIR/$CLI_NAME")" "$CLI_NAME 1.0.0"
equal "current points at the version" "$(readlink "$INSTALL_HOME/current")" "versions/1.0.0"

# The upgrade case, which is the one with all the sharp edges.
make_fixture 1.1.0 "$TRIPLE"
install_release v1.1.0 0 >/dev/null 2>&1
equal "an upgrade moves the command" "$("$BIN_DIR/$CLI_NAME")" "$CLI_NAME 1.1.0"
# `ln -sf` over an existing symlink-to-a-directory writes *inside* it. If that ever
# regresses, this is the assertion that catches it: the link would be
# versions/1.0.0/versions/1.1.0 and `current` would still resolve to the old bundle.
equal "current is replaced, not written into" "$(readlink "$INSTALL_HOME/current")" "versions/1.1.0"
if [ -d "$INSTALL_HOME/versions/1.0.0" ]; then
  pass "the replaced version is kept (upgrade runs from inside it)"
else
  bad "the replaced version is kept" "1.0.0 was deleted while it could still be executing"
fi

make_fixture 1.2.0 "$TRIPLE"
install_release v1.2.0 0 >/dev/null 2>&1
if [ -d "$INSTALL_HOME/versions/1.0.0" ]; then
  bad "older versions are pruned" "1.0.0 is still on disk after two upgrades"
else
  pass "older versions are pruned"
fi
equal "the version it replaced is still there" \
  "$([ -d "$INSTALL_HOME/versions/1.1.0" ] && echo yes)" "yes"

# Re-running the installer for a version already present must be a no-op that still
# works, not a half-removed directory: install is also the upgrade path.
install_release v1.2.0 0 >/dev/null 2>&1
equal "re-installing the current version is harmless" "$("$BIN_DIR/$CLI_NAME")" "$CLI_NAME 1.2.0"

# ------------------------------------------------------------------ the refusals

echo
echo "refusals:"

make_fixture 2.0.0 "$TRIPLE"
# Corrupt the published checksum: the bundle must not be installed on a mismatch.
printf '%s  %s\n' "0000000000000000000000000000000000000000000000000000000000000000" \
  "$(asset_name "$CLI_NAME" 2.0.0 "$TRIPLE")" > "$SCRATCH/releases/$(asset_name "$CLI_NAME" 2.0.0 "$TRIPLE").sha256"
if install_release v2.0.0 0 >/dev/null 2>&1; then
  bad "a checksum mismatch refuses" "installed anyway"
else
  pass "a checksum mismatch refuses"
fi
equal "and leaves the working install alone" "$("$BIN_DIR/$CLI_NAME")" "$CLI_NAME 1.2.0"

# An asset that is not published at all — the platform this release skipped.
if install_release v9.9.9 0 >/dev/null 2>&1; then
  bad "a missing asset refuses" "reported success"
else
  pass "a missing asset refuses"
fi

# --from-file installs a tarball already on disk, taking its word for the version.
make_fixture 3.0.0 "$TRIPLE"
install_file "$SCRATCH/releases/$(asset_name "$CLI_NAME" 3.0.0 "$TRIPLE")" 0 >/dev/null 2>&1
equal "--from-file installs a local bundle" "$("$BIN_DIR/$CLI_NAME")" "$CLI_NAME 3.0.0"
if install_file "$SCRATCH/releases/nope.tar.gz" 0 >/dev/null 2>&1; then
  bad "--from-file refuses a missing file" "reported success"
else
  pass "--from-file refuses a missing file"
fi

# The scratch directory each install downloads into has to go. Bash RETURN traps are
# global rather than per-frame, so a trap in `unpack_and_link` used to replace the one in
# `install_release` and leave the ~47 MB tarball in /tmp on every install — an accumulation
# per run, which is the thing the re-runnable-installer rule exists to prevent.
before="$(find "${TMPDIR:-/tmp}" -maxdepth 2 -name "$CLI_NAME-*.tar.gz" 2>/dev/null | grep -vc "^$SCRATCH" || true)"
make_fixture 4.0.0 "$TRIPLE"
install_release v4.0.0 0 >/dev/null 2>&1
after="$(find "${TMPDIR:-/tmp}" -maxdepth 2 -name "$CLI_NAME-*.tar.gz" 2>/dev/null | grep -vc "^$SCRATCH" || true)"
equal "an install leaves no scratch tarball behind" "$after" "$before"

# `<cli> upgrade --version <the version already installed>` reaches install_release with
# a destination that *is* the bundle this script is being read out of. The rm -rf that
# makes way for the new copy would take the running executable, its _internal/ and this
# file with it — so it is refused. The guard reads RUNNING_BUNDLE, which the real script
# derives from its own path; set it by hand here, since this file is not inside a bundle.
# shellcheck disable=SC2034
RUNNING_BUNDLE="$INSTALL_HOME/versions/4.0.0"
if install_release v4.0.0 0 >/dev/null 2>&1; then
  bad "refuses to overwrite the bundle it is running from" "went ahead"
else
  pass "refuses to overwrite the bundle it is running from"
fi
equal "and the running bundle is still there" \
  "$([ -x "$INSTALL_HOME/versions/4.0.0/$CLI_NAME" ] && echo yes)" "yes"
# shellcheck disable=SC2034
RUNNING_BUNDLE=""

# Where the command went is remembered, so an upgrade a year later — run by the CLI
# itself, with no --bin-dir to pass on — puts it back in the same place rather than in
# the default one.
equal "the bin directory is recorded" "$(cat "$INSTALL_HOME/$BIN_DIR_RECEIPT")" "$BIN_DIR"
recovered="$(
  export MEDIA_AI_HOME="$INSTALL_HOME"
  unset MEDIA_AI_BIN_DIR
  resolve_layout ""
  printf '%s' "$BIN_DIR"
)"
equal "…and read back by resolve_layout" "$recovered" "$SCRATCH/bin"

# An archive that does not extract cleanly must not be installed — and, crucially, must
# not be *reported* as installed. Both tar implementations this runs under already refuse
# a member with `..` in it, a leading `/`, or a write through a symlink, and exit non-zero
# saying so; the bug was that nothing here looked at that status. `main` used to call these
# functions as `install_file … || return $?`, which switches `errexit` off for the whole
# call chain, so a failed extraction sailed on to "installed to …" and exit 0.
python3 - "$SCRATCH/releases/$(asset_name "$CLI_NAME" 5.0.0 "$TRIPLE")" <<'PYEOF'
import io, sys, tarfile
with tarfile.open(sys.argv[1], "w:gz") as tf:
    d = tarfile.TarInfo("media-ai"); d.type = tarfile.DIRTYPE; d.mode = 0o755; tf.addfile(d)
    exe = b"#!/bin/sh\necho ok\n"
    e = tarfile.TarInfo("media-ai/media-ai"); e.size = len(exe); e.mode = 0o755
    tf.addfile(e, io.BytesIO(exe))
    bad = b"OWNED\n"; b = tarfile.TarInfo("../../../../ESCAPED"); b.size = len(bad)
    tf.addfile(b, io.BytesIO(bad))
PYEOF
if install_file "$SCRATCH/releases/$(asset_name "$CLI_NAME" 5.0.0 "$TRIPLE")" 0 >/dev/null 2>&1; then
  bad "an archive that fails to extract is refused" "reported success"
else
  pass "an archive that fails to extract is refused"
fi
equal "…and nothing was installed under that version" \
  "$([ -e "$INSTALL_HOME/versions/5.0.0" ] && echo present || echo absent)" "absent"
equal "…and the working install is untouched" "$("$BIN_DIR/$CLI_NAME")" "$CLI_NAME 4.0.0"

# A payload root that is a symlink rather than a directory. tar will not write *through*
# one, but it will create it — and moving it into place would leave the installed command
# resolving wherever the archive pointed.
python3 - "$SCRATCH/releases/$(asset_name "$CLI_NAME" 5.1.0 "$TRIPLE")" <<'PYEOF'
import sys, tarfile
with tarfile.open(sys.argv[1], "w:gz") as tf:
    ln = tarfile.TarInfo("media-ai"); ln.type = tarfile.SYMTYPE; ln.linkname = "/tmp"
    tf.addfile(ln)
PYEOF
if install_file "$SCRATCH/releases/$(asset_name "$CLI_NAME" 5.1.0 "$TRIPLE")" 0 >/dev/null 2>&1; then
  bad "a symlink payload root is refused" "reported success"
else
  pass "a symlink payload root is refused"
fi

# A dry run says what it would do and writes nothing.
rm -rf "$SCRATCH/dryrun"
INSTALL_HOME="$SCRATCH/dryrun/share" BIN_DIR="$SCRATCH/dryrun/bin" install_release v1.0.0 1 >/dev/null 2>&1
if [ -e "$SCRATCH/dryrun" ]; then bad "--dry-run writes nothing" "created $SCRATCH/dryrun"; else pass "--dry-run writes nothing"; fi

echo
if [ "$fail" -eq 0 ]; then
  echo "all installer tests passed"
else
  echo "installer tests FAILED"
fi
exit "$fail"
