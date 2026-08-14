#!/usr/bin/env bash
# Regression tests for install.sh: the release-tag parser, and the options.
#
# The parser reads GitHub's JSON with grep/sed rather than a JSON library, because the
# installer must run before anything is installed. That is workable but easy to get
# subtly wrong — the first version used one greedy sed and silently returned the
# *oldest* release in the page, since the API answers with compact single-line JSON.
#
# The options are here because two of them change what gets installed rather than only
# what gets configured, and because the failure they are guarded against is silence: a
# flag that is parsed, accepted and then dropped looks exactly like one that worked.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Source the functions without running main().
# shellcheck disable=SC1090
source <(sed '$ d' "$HERE/install.sh")

fail=0

check() {
  local name="$1" payload="$2" want="$3" got
  got="$(printf '%s' "$payload" | parse_tag || true)"
  if [ "$got" = "$want" ]; then
    printf '  ok   %s\n' "$name"
  else
    printf '  FAIL %s: want %q, got %q\n' "$name" "$want" "$got"
    fail=1
  fi
}

echo "parse_tag:"

check "single release" \
  '[{"tag_name":"v0.2.0","prerelease":true,"draft":false}]' \
  "v0.2.0"

# The bug that motivated this file: newest-first, so the FIRST match wins.
check "multiple releases on one line picks the newest" \
  '[{"tag_name":"v0.3.0","prerelease":true},{"tag_name":"v0.2.0","prerelease":true}]' \
  "v0.3.0"

check "whitespace around the colon" \
  '[{ "tag_name" : "v1.0.0" }]' \
  "v1.0.0"

check "pretty-printed across lines" \
  '[
  {
    "tag_name": "v2.0.0",
    "prerelease": false
  }
]' \
  "v2.0.0"

# A release body mentioning tag_name in prose must not be mistaken for the field.
check "prose in the body does not shadow the field" \
  '[{"tag_name":"v0.4.0","body":"see tag_name: v0.1.0 for details"}]' \
  "v0.4.0"

check "no releases yet falls through to the caller's default" \
  '[]' \
  ""

check "error object instead of a list" \
  '{"message":"Not Found","documentation_url":"https://docs.github.com/"}' \
  ""

check "empty response" "" ""

echo
echo "options:"

# The functions are sourced, so the two that touch the machine are replaced here: this
# file is about argument handling, and neither checking the platform nor installing uv
# is part of that. Passing `--dry-run --version` keeps it off the network as well, so
# these run anywhere, including on a box with no uv at all.
#
# Both are called by `main`, not from this file, which is what SC2329 cannot see.
# shellcheck disable=SC2329
check_platform() { :; }
# shellcheck disable=SC2329
ensure_uv() { :; }

# `main` is run in a subshell because a missing flag value ends in `exit 2` — from a
# sourced script that would take the test runner with it.
opt() {
  local name="$1" want="$2"
  shift 2
  local got
  got="$( (main "$@") 2>&1 || true )"
  case "$got" in
    *"$want"*) printf '  ok   %s\n' "$name" ;;
    *) printf '  FAIL %s: want %q in output, got %q\n' "$name" "$want" "$got"; fail=1 ;;
  esac
}

opt "an ordinary install resolves the bare git spec" \
  "uv tool install --force git+https://github.com/" \
  --dry-run --version main

# The point of the flag: telemetry configured without the SDK that exports it writes a
# collector's address into the config and then sends it nothing.
opt "--telemetry-endpoint adds the otel extra to the same install" \
  "[otel] @ git+https://github.com/" \
  --dry-run --version main --telemetry-endpoint http://collector:4318

opt "--telemetry-endpoint=URL is the same flag" \
  "[otel] @ git+https://github.com/" \
  --dry-run --version main --telemetry-endpoint=http://collector:4318

opt "--telemetry-endpoint with no value is refused" \
  "--telemetry-endpoint needs a value" \
  --dry-run --version main --telemetry-endpoint

# A value starting with `-` is the flag that follows it, swallowed.
opt "--telemetry-endpoint does not swallow the next flag" \
  "--telemetry-endpoint needs a value" \
  --dry-run --telemetry-endpoint --version main

opt "--telemetry-endpoint is not silently dropped by --no-init" \
  "--no-init skips" \
  --dry-run --version main --telemetry-endpoint http://collector:4318 --no-init

echo
if [ "$fail" -eq 0 ]; then
  echo "all install.sh tests passed"
else
  echo "install.sh tests FAILED"
fi
exit "$fail"
