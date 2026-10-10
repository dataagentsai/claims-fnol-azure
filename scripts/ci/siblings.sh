#!/usr/bin/env bash
# CI only: say which commit of each sibling checkout this run used, in the log
# and on the run's summary page, so a failure can be reproduced on the Mac with
# `git -C ../<repo> checkout <sha>`. Run from claims-fnol-azure.
set -euo pipefail
{
  echo "| Sibling | Commit |"
  echo "|---|---|"
  for repo in reference-agent agenttwin clean-ai-engineering ai-harness-catalog ai-assurance-catalog; do
    if [[ -d "../$repo/.git" ]]; then
      echo "| $repo | \`$(git -C "../$repo" rev-parse HEAD)\` |"
    else
      echo "| $repo | not checked out |"
    fi
  done
} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
