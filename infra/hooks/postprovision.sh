#!/usr/bin/env bash
# After `azd provision`: the two things Bicep should not do.
#
# 1. The Groq API key into Key Vault. Asked for here, read from the terminal
#    without echoing, and handed to `az keyvault secret set` on its standard
#    input, so it is never in a file, a parameter, the shell history or the
#    process list. Asked only while the vault holds the template's placeholder.
#    APIM's named value is then refreshed so it does not wait up to four hours.
#
# 2. The Entra app registration (infra/hooks/entra-app.sh): Bicep's Microsoft
#    Graph extension is a preview, so the az CLI does it, idempotently.
#
# Needs `az` signed in to the same tenant as azd. azd passes its environment
# (the deployment's outputs) to this script as variables.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

: "${AZURE_KEY_VAULT_NAME:?run azd provision first}"
: "${AZURE_RESOURCE_GROUP:?}" "${AZURE_APIM_NAME:?}" "${AZURE_SUBSCRIPTION_ID:?}"

tag="$(az keyvault secret show --vault-name "$AZURE_KEY_VAULT_NAME" --name groq-api-key \
         --query 'tags.placeholder' -o tsv 2>/dev/null || echo missing)"
if [[ "$tag" != "false" ]]; then
  echo "Key Vault $AZURE_KEY_VAULT_NAME has no Groq key yet."
  read -r -s -p "Paste your Groq API key (it will not be shown): " groq_key
  echo
  [[ -n "$groq_key" ]] || { echo "No key given; run 'azd hooks run postprovision' later." >&2; exit 1; }
  printf '%s' "$groq_key" | az keyvault secret set \
    --vault-name "$AZURE_KEY_VAULT_NAME" --name groq-api-key \
    --file /dev/stdin --encoding utf-8 --tags placeholder=false -o none
  unset groq_key
  azd env set GROQ_KEY_IN_VAULT true >/dev/null
  # APIM follows a Key Vault update within four hours; ask it to now.
  az rest --method post -o none --url \
    "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.ApiManagement/service/${AZURE_APIM_NAME}/namedValues/groq-api-key/refreshSecret?api-version=2024-05-01"
  echo "Groq key stored in Key Vault; APIM refreshed."
else
  echo "Groq key already in Key Vault $AZURE_KEY_VAULT_NAME."
fi

"$here/entra-app.sh"
