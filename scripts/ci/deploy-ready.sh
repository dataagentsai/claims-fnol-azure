#!/usr/bin/env bash
# CI only (deploy.yml, job `ready`): may this run deploy, and which commit?
#
# Writes `ready=true|false` and `sha=<commit>` to $GITHUB_OUTPUT. Never fails
# the run for a missing setup: it says what is missing and stops politely, so
# the workflow stays off until the owner has set it up (README, "CI and deploy").
#
#   1. The three OIDC ids are secrets (AZURE_CLIENT_ID, AZURE_TENANT_ID,
#      AZURE_SUBSCRIPTION_ID) and the azd values are variables
#      (scripts/ci/setup-federated-credential.sh sets or prints all of them).
#   2. After CI (workflow_run): only when the repository variable
#      DEPLOY_AFTER_CI is "true", and only for a CI run that succeeded on a push.
#   3. By hand (workflow_dispatch): the commit asked for, or the branch head;
#      CI must have succeeded on a push for exactly that commit, since only that
#      run pushed its images (ghcr.io/...:<sha>).
#
# Reads: EVENT, INPUT_SHA, HEAD_SHA, RUN_CONCLUSION, RUN_EVENT, DEPLOY_AFTER_CI,
# the secrets and variables named below, GH_TOKEN and GITHUB_REPOSITORY.
set -euo pipefail

out="${GITHUB_OUTPUT:-/dev/stdout}"
stop() {
  echo "::notice title=Deploy not run::$1"
  echo "Deploy not run: $1" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
  echo "ready=false" >> "$out"
  exit 0
}

missing=()
for name in AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_SUBSCRIPTION_ID; do
  [[ -n "${!name:-}" ]] || missing+=("secret $name")
done
for name in AZURE_KEY_VAULT_NAME ENTRA_APP_ID CLAIMS_SYSTEM_APP_ID BUDGET_CONTACT_EMAIL; do
  [[ -n "${!name:-}" ]] || missing+=("variable $name")
done
if (( ${#missing[@]} )); then
  stop "deploy is not set up yet (missing: ${missing[*]}). Run scripts/ci/setup-federated-credential.sh once and set what it prints (README, \"CI and deploy\")."
fi

case "${EVENT:-}" in
  workflow_run)
    [[ "${DEPLOY_AFTER_CI:-}" == "true" ]] || stop "deploy after CI is off (set the repository variable DEPLOY_AFTER_CI=true to turn it on)."
    [[ "${RUN_CONCLUSION:-}" == "success" ]] || stop "CI did not succeed for ${HEAD_SHA} (${RUN_CONCLUSION:-unknown})."
    [[ "${RUN_EVENT:-}" == "push" ]] || stop "CI ran on a ${RUN_EVENT:-unknown}, not a push to main: no images were pushed."
    sha="$HEAD_SHA"
    ;;
  workflow_dispatch)
    sha="${INPUT_SHA:-$HEAD_SHA}"
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "::error::'$sha' is not a full 40-character commit SHA."; exit 1; }
    passed="$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/ci.yml/runs?head_sha=${sha}&event=push&status=success" --jq '.total_count')"
    if [[ "$passed" == "0" ]]; then
      echo "::error::CI has not succeeded on a push for ${sha}, so its images were never pushed. Deploy a commit CI passed on main."
      exit 1
    fi
    ;;
  *)
    echo "::error::unexpected event '${EVENT:-}'."
    exit 1
    ;;
esac

echo "ready=true" >> "$out"
echo "sha=$sha" >> "$out"
echo "Deploying ${sha}." >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
