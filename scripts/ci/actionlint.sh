#!/usr/bin/env bash
# Lint .github/workflows with actionlint (github.com/rhysd/actionlint): the
# expressions, the contexts each may use, and every `run:` through shellcheck
# when it is installed. Uses an actionlint already on PATH (brew install
# actionlint); otherwise, in CI, downloads the pinned release below.
set -euo pipefail
version=1.7.12
cd "$(dirname "$0")/../.."
if ! command -v actionlint >/dev/null; then
  if [[ -z "${CI:-}" ]]; then
    echo "actionlint is not installed (brew install actionlint); skipped." >&2
    exit 0
  fi
  dir="${RUNNER_TEMP:-/tmp}/actionlint"
  mkdir -p "$dir"
  curl -fsSL "https://github.com/rhysd/actionlint/releases/download/v${version}/actionlint_${version}_linux_amd64.tar.gz" \
    | tar -xz -C "$dir" actionlint
  PATH="$dir:$PATH"
fi
actionlint -color
