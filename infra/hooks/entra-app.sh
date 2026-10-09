#!/usr/bin/env bash
# The agent's Entra app registration, made or brought up to date (idempotent).
#
# What it is: the agent's identity in Microsoft Entra ID. Policyholders and the
# claims handler sign in against it, and the tokens they get are addressed to
# it (audience api://claims-fnol, which config/azure.yaml checks).
#
#   - found by display name (ENTRA_APP_NAME, default claims-fnol-agent);
#     created only if no app has that name
#   - identifier URI api://claims-fnol
#   - access tokens v2 (requestedAccessTokenVersion 2): the entra-id identity
#     adapter accepts the v2 issuer only; null would mean v1 tokens
#     (learn.microsoft.com/graph/api/resources/apiapplication, read 9 Oct 2026)
#   - delegated scopes claims:read and claims:write, what a policyholder's
#     session holds (claims_fnol.binding.POLICYHOLDER_SCOPES)
#   - app role desk.handler, for users: config/azure.yaml maps it to the desk's
#     four scopes (role_scopes)
#   - a service principal, and the signed-in owner given desk.handler, so the
#     desk can be tried at once
#   - the agent's URL as a single-page-app redirect URI
#
# Not done here (FINDINGS F-43): a policyholder-id claim (`customer_id`) in the
# token. Entra issues no such claim until a directory extension and a claims
# mapping exist; until then an Entra policyholder session is "not a
# customer's" at /chat. Tier 4.
#
# Bicep's Microsoft Graph extension is a preview, so this is the az CLI and
# Microsoft Graph's REST API through `az rest`. Stable ids below keep every run
# an update of the same scopes and role, never a second copy.
set -euo pipefail

name="${ENTRA_APP_NAME:-claims-fnol-agent}"
audience="api://claims-fnol"
read_scope_id="7d3e8a10-5c1f-4c55-9b0e-1f0a2b3c4d01"
write_scope_id="7d3e8a10-5c1f-4c55-9b0e-1f0a2b3c4d02"
handler_role_id="7d3e8a10-5c1f-4c55-9b0e-1f0a2b3c4d10"
graph="https://graph.microsoft.com/v1.0"

app_id="$(az ad app list --display-name "$name" --query '[0].appId' -o tsv)"
if [[ -z "$app_id" ]]; then
  app_id="$(az ad app create --display-name "$name" --sign-in-audience AzureADMyOrg \
              --query appId -o tsv)"
  echo "Created app registration $name ($app_id)."
else
  echo "App registration $name exists ($app_id); bringing it up to date."
fi
object_id="$(az ad app show --id "$app_id" --query id -o tsv)"

redirects="[]"
[[ -n "${AGENT_URL:-}" ]] && redirects="[\"${AGENT_URL}/\"]"

body="$(cat <<JSON
{
  "identifierUris": ["${audience}"],
  "spa": {"redirectUris": ${redirects}},
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [
      {"id": "${read_scope_id}", "value": "claims:read", "type": "User", "isEnabled": true,
       "adminConsentDisplayName": "Read your claims",
       "adminConsentDescription": "The claims agent reads the signed-in policyholder's claims and policies.",
       "userConsentDisplayName": "Read your claims",
       "userConsentDescription": "The claims agent reads your claims and policies."},
      {"id": "${write_scope_id}", "value": "claims:write", "type": "User", "isEnabled": true,
       "adminConsentDisplayName": "Report and update your claims",
       "adminConsentDescription": "The claims agent registers claims and sends documents for the signed-in policyholder.",
       "userConsentDisplayName": "Report and update your claims",
       "userConsentDescription": "The claims agent registers your claims and sends your documents."}
    ]
  },
  "appRoles": [
    {"id": "${handler_role_id}", "value": "desk.handler", "isEnabled": true,
     "allowedMemberTypes": ["User"],
     "displayName": "Claims handler (desk)",
     "description": "Works the escalation queue and decides payouts above the automatic limit."}
  ]
}
JSON
)"
az rest --method PATCH --uri "${graph}/applications/${object_id}" \
  --headers "Content-Type=application/json" --body "$body" -o none

sp_id="$(az ad sp list --filter "appId eq '${app_id}'" --query '[0].id' -o tsv)"
if [[ -z "$sp_id" ]]; then
  sp_id="$(az ad sp create --id "$app_id" --query id -o tsv)"
fi

owner_id="$(az ad signed-in-user show --query id -o tsv)"
held="$(az rest --method GET -o tsv \
          --uri "${graph}/servicePrincipals/${sp_id}/appRoleAssignedTo" \
          --query "value[?principalId=='${owner_id}' && appRoleId=='${handler_role_id}'] | length(@)")"
if [[ "$held" == "0" ]]; then
  az rest --method POST -o none --uri "${graph}/servicePrincipals/${sp_id}/appRoleAssignedTo" \
    --headers "Content-Type=application/json" \
    --body "{\"principalId\": \"${owner_id}\", \"resourceId\": \"${sp_id}\", \"appRoleId\": \"${handler_role_id}\"}"
  echo "You hold desk.handler on $name."
fi

azd env set ENTRA_APP_ID "$app_id" >/dev/null
echo "Entra app: $name, client id $app_id, audience $audience."
