#!/usr/bin/env bash
# Regression tests for install.sh's release-tag parser.
#
# It parses GitHub's JSON with grep/sed rather than a JSON library, because the
# installer must run before anything is installed. That is workable but easy to get
# subtly wrong — the first version used one greedy sed and silently returned the
# *oldest* release in the page, since the API answers with compact single-line JSON.

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
if [ "$fail" -eq 0 ]; then
  echo "all parse_tag tests passed"
else
  echo "parse_tag tests FAILED"
fi
exit "$fail"
