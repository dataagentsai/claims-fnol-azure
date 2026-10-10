#!/usr/bin/env bash
# CI only (deploy.yml): the azd environment a runner starts without.
#
# On the Mac, azd keeps its environment in .azure/<env>/.env (gitignored) and
# the hooks ask for what it lacks. A runner has neither, so the values that
# must stay what the owner's environment holds come from repository variables
# (scripts/ci/setup-federated-credential.sh prints them from the owner's
# environment). Each matters:
#
#   AZURE_KEY_VAULT_NAME   azd's secretOrRandomPassword reads the PostgreSQL
#                          passwords from it; without it azd would make new ones
#                          and provision would change every database password
#   ENTRA_APP_ID, CLAIMS_SYSTEM_APP_ID
#                          the apps' Entra ids; empty would blank them in the apps
#   BUDGET_CONTACT_EMAIL   the budget's alert address (preprovision asks for it)
#   OWNER_IP_ADDRESS       the owner's own firewall rule, kept as it is; empty is
#                          fine (an absent rule is not deleted: incremental mode)
#   AZURE_PRINCIPAL_NAME   left empty on purpose: main.bicep skips the PostgreSQL
#                          Entra admin when it is empty, so the owner stays the
#                          admin. azd always fills AZURE_PRINCIPAL_ID with the
#                          signed-in principal (FINDINGS F-101).
#   AGENT_IMAGE, CLAIMS_SYSTEM_IMAGE
#                          the commit's images from CI (IMAGE_TAG)
#
# Reads them from its environment (the workflow maps vars.* to env).
set -euo pipefail

env_name="${AZURE_ENV_NAME:-dev}"
location="${AZURE_LOCATION:-centralindia}"
: "${AZURE_SUBSCRIPTION_ID:?}" "${AZURE_KEY_VAULT_NAME:?}" "${ENTRA_APP_ID:?}"
: "${CLAIMS_SYSTEM_APP_ID:?}" "${BUDGET_CONTACT_EMAIL:?}" "${IMAGE_TAG:?}"
registry="${IMAGE_REGISTRY:-ghcr.io/dataagentsai}"

azd env new "$env_name" --subscription "$AZURE_SUBSCRIPTION_ID" --location "$location" --no-prompt
set_value() { azd env set "$1" "$2" >/dev/null; }
set_value AZURE_KEY_VAULT_NAME "$AZURE_KEY_VAULT_NAME"
set_value ENTRA_APP_ID "$ENTRA_APP_ID"
set_value CLAIMS_SYSTEM_APP_ID "$CLAIMS_SYSTEM_APP_ID"
set_value BUDGET_CONTACT_EMAIL "$BUDGET_CONTACT_EMAIL"
set_value OWNER_IP_ADDRESS "${OWNER_IP_ADDRESS:-}"
set_value AZURE_PRINCIPAL_NAME ""
set_value AGENT_IMAGE "$registry/claims-fnol-agent:$IMAGE_TAG"
set_value CLAIMS_SYSTEM_IMAGE "$registry/claims-fnol-claims-system:$IMAGE_TAG"

echo "azd environment '$env_name' ($location): images $registry/claims-fnol-{agent,claims-system}:$IMAGE_TAG"
