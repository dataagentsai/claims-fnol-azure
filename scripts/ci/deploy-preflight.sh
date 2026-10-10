#!/usr/bin/env bash
# CI only (deploy.yml): stop before `azd provision` if it would do harm.
#
#   before   the vault can be read, and the Groq key and the agent's Entra
#            client secret are real in it. The first `azd up` is the owner's,
#            from the Mac: it asks for the Groq key and makes the Entra apps,
#            which a runner cannot (no terminal; no Graph permission, on purpose).
#   flags    after infra/hooks/preprovision.sh: GROQ_KEY_IN_VAULT and
#            ENTRA_SECRET_IN_VAULT are true. If either were false, main.bicep
#            would write its placeholder over the real secret.
#
# Usage: deploy-preflight.sh before|flags
set -euo pipefail

value() { azd env get-value "$1" 2>/dev/null || true; }
fail() { echo "::error title=Deploy stopped::$1"; exit 1; }

case "${1:-}" in
  before)
    vault="$(value AZURE_KEY_VAULT_NAME)"
    [[ -n "$vault" ]] || fail "AZURE_KEY_VAULT_NAME is not set."
    az keyvault secret show --vault-name "$vault" --name postgres-admin-password --query id -o tsv >/dev/null \
      || fail "cannot read Key Vault $vault as this identity: run scripts/ci/setup-federated-credential.sh (Key Vault Secrets User), or the first 'azd up' from the Mac has not run."
    for secret in groq-api-key agent-obo-client-secret; do
      tag="$(az keyvault secret show --vault-name "$vault" --name "$secret" --query 'tags.placeholder' -o tsv 2>/dev/null || echo missing)"
      [[ "$tag" == "false" ]] || fail "Key Vault $vault holds no real $secret yet. Run 'azd up' once from the Mac (README, \"Deploy to Azure\"); CI deploys only after that."
    done
    echo "Key Vault $vault: readable; Groq key and Entra client secret in place."
    ;;
  flags)
    for flag in GROQ_KEY_IN_VAULT ENTRA_SECRET_IN_VAULT; do
      [[ "$(value "$flag")" == "true" ]] || fail "$flag is not true after preprovision: provision would write the placeholder over the real secret."
    done
    echo "Both secrets are marked as in the vault; provision keeps them."
    ;;
  *)
    echo "usage: $0 before|flags" >&2
    exit 2
    ;;
esac
