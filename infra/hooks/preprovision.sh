#!/usr/bin/env bash
# Before `azd provision`: make sure every value main.parameters.json reads is
# set in the azd environment, asking only for what azd cannot know.
#
#   BUDGET_CONTACT_EMAIL   asked once: where the $10 budget's alerts go
#   OWNER_IP_ADDRESS       asked once, optional: your IP, for psql from the Mac
#   AZURE_PRINCIPAL_NAME   your sign-in name (the PostgreSQL Entra admin's label)
#   GROQ_KEY_IN_VAULT      true once the real Groq key is in Key Vault, so the
#                          placeholder secret is never written over it
#   AGENT_IMAGE, CLAIMS_SYSTEM_IMAGE
#                          what `azd deploy` runs; a public placeholder until
#                          the GitHub Container Registry images exist (Tier 4)
#
# Idempotent: a value already set is kept. Needs `az` signed in to the same
# tenant as azd (`az login`). Writes nothing to any file of this repository;
# azd keeps its environment in .azure/ (gitignored).
set -euo pipefail

value() { azd env get-value "$1" 2>/dev/null || true; }
keep() { # name, value: set only if not set yet
  if [[ -z "$(value "$1")" ]]; then azd env set "$1" "$2" >/dev/null; fi
}

if [[ -z "$(value BUDGET_CONTACT_EMAIL)" ]]; then
  read -r -p "Email for the \$10/month budget alerts: " email
  [[ "$email" == *@*.* ]] || { echo "That is not an email address." >&2; exit 1; }
  azd env set BUDGET_CONTACT_EMAIL "$email" >/dev/null
fi

if ! azd env get-values | grep -q '^OWNER_IP_ADDRESS='; then
  read -r -p "Your public IP, to reach PostgreSQL from this Mac (Enter to skip): " ip
  azd env set OWNER_IP_ADDRESS "${ip:-}" >/dev/null
fi

keep AZURE_PRINCIPAL_NAME "$(az ad signed-in-user show --query userPrincipalName -o tsv)"

# The Groq key: in the vault for real once its secret exists without the
# placeholder tag the template gives it.
groq_in_vault=false
vault="$(value AZURE_KEY_VAULT_NAME)"
if [[ -n "$vault" ]]; then
  tag="$(az keyvault secret show --vault-name "$vault" --name groq-api-key \
           --query 'tags.placeholder' -o tsv 2>/dev/null || echo missing)"
  [[ "$tag" == "false" ]] && groq_in_vault=true
fi
azd env set GROQ_KEY_IN_VAULT "$groq_in_vault" >/dev/null

# The agent app's client secret (A1), likewise: real once the Entra hook wrote it.
entra_in_vault=false
if [[ -n "$vault" ]]; then
  tag="$(az keyvault secret show --vault-name "$vault" --name agent-obo-client-secret \
           --query 'tags.placeholder' -o tsv 2>/dev/null || echo missing)"
  [[ "$tag" == "false" ]] && entra_in_vault=true
fi
azd env set ENTRA_SECRET_IN_VAULT "$entra_in_vault" >/dev/null

placeholder="mcr.microsoft.com/k8se/quickstart:latest"
keep AGENT_IMAGE "$placeholder"
keep CLAIMS_SYSTEM_IMAGE "$placeholder"
if [[ "$(value AGENT_IMAGE)" == "$placeholder" ]]; then
  echo "Images: the public placeholder for now. Once Tier 4 has pushed them:"
  echo "  azd env set AGENT_IMAGE ghcr.io/<owner>/claims-fnol-agent:<tag>"
  echo "  azd env set CLAIMS_SYSTEM_IMAGE ghcr.io/<owner>/claims-fnol-claims-system:<tag>"
fi
