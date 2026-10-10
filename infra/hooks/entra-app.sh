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
# The claims system's own app registration (A1), the far end the agent calls:
#   - found by display name (CLAIMS_APP_NAME, default claims-fnol-claims-system)
#   - identifier URI api://claims-system, v2 access tokens: a v2 token's `aud`
#     is this app's client id, which config/claims-system/azure.yaml checks
#   - delegated scopes claims:read and claims:write, which the agent's
#     on-behalf-of token for api://claims-system/.default carries
#   - app role payouts.issue, for applications: the agent's app holds it, and
#     its client-credentials token is the payout workflow's login (no person is
#     there when a handler approves); the claims system maps it to
#     claims:read + payouts:write
#   - the agent's app given those delegated scopes, admin consent granted (so
#     no policyholder is asked), and the app role assigned
#   - a client secret on the agent's app, written straight to Key Vault as
#     agent-obo-client-secret (tagged placeholder=false), never to a file
#   - azd env CLAIMS_SYSTEM_APP_ID and ENTRA_SECRET_IN_VAULT, read by the next
#     `azd provision` into both containers' environment
#
# Not done here (F-43): the policyholder-id claim. The claims system reads
# `customer_id`; Entra writes it only once a claims mapping policy is assigned
# to the claims system's service principal too, not only the agent's.
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

# ---------------------------------------------------------------- the claims system
claims_name="${CLAIMS_APP_NAME:-claims-fnol-claims-system}"
claims_audience="api://claims-system"
claims_read_id="7d3e8a10-5c1f-4c55-9b0e-1f0a2b3c4d21"
claims_write_id="7d3e8a10-5c1f-4c55-9b0e-1f0a2b3c4d22"
payouts_role_id="7d3e8a10-5c1f-4c55-9b0e-1f0a2b3c4d30"

claims_id="$(az ad app list --display-name "$claims_name" --query '[0].appId' -o tsv)"
if [[ -z "$claims_id" ]]; then
  claims_id="$(az ad app create --display-name "$claims_name" --sign-in-audience AzureADMyOrg \
                 --query appId -o tsv)"
  echo "Created app registration $claims_name ($claims_id)."
fi
claims_object="$(az ad app show --id "$claims_id" --query id -o tsv)"
claims_body="$(cat <<JSON
{
  "identifierUris": ["${claims_audience}"],
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [
      {"id": "${claims_read_id}", "value": "claims:read", "type": "Admin", "isEnabled": true,
       "adminConsentDisplayName": "Read a policyholder's claims",
       "adminConsentDescription": "The claims agent reads the signed-in policyholder's claims and policies in the claims system."},
      {"id": "${claims_write_id}", "value": "claims:write", "type": "Admin", "isEnabled": true,
       "adminConsentDisplayName": "Report and update a policyholder's claims",
       "adminConsentDescription": "The claims agent registers claims and sends documents in the claims system for the signed-in policyholder."}
    ]
  },
  "appRoles": [
    {"id": "${payouts_role_id}", "value": "payouts.issue", "isEnabled": true,
     "allowedMemberTypes": ["Application"],
     "displayName": "Issue approved payouts",
     "description": "The payout workflow pays a claim whose approval the claims system can read."}
  ]
}
JSON
)"
az rest --method PATCH --uri "${graph}/applications/${claims_object}" \
  --headers "Content-Type=application/json" --body "$claims_body" -o none

claims_sp="$(az ad sp list --filter "appId eq '${claims_id}'" --query '[0].id' -o tsv)"
[[ -n "$claims_sp" ]] || claims_sp="$(az ad sp create --id "$claims_id" --query id -o tsv)"

# The agent's app asks for both delegated scopes and the app role...
access="[{\"resourceAppId\": \"${claims_id}\", \"resourceAccess\": [
  {\"id\": \"${claims_read_id}\", \"type\": \"Scope\"},
  {\"id\": \"${claims_write_id}\", \"type\": \"Scope\"},
  {\"id\": \"${payouts_role_id}\", \"type\": \"Role\"}]}]"
az rest --method PATCH --uri "${graph}/applications/${object_id}" \
  --headers "Content-Type=application/json" \
  --body "{\"requiredResourceAccess\": ${access}}" -o none

# ...is granted them for the whole tenant (no policyholder is asked to consent)...
granted="$(az rest --method GET -o tsv --uri "${graph}/oauth2PermissionGrants" \
             --uri-parameters "\$filter=clientId eq '${sp_id}' and resourceId eq '${claims_sp}'" \
             --query 'value | length(@)')"
if [[ "$granted" == "0" ]]; then
  az rest --method POST -o none --uri "${graph}/oauth2PermissionGrants" \
    --headers "Content-Type=application/json" \
    --body "{\"clientId\": \"${sp_id}\", \"consentType\": \"AllPrincipals\", \"resourceId\": \"${claims_sp}\", \"scope\": \"claims:read claims:write\"}"
fi

# ...and holds payouts.issue, its workflow's login.
held_role="$(az rest --method GET -o tsv \
               --uri "${graph}/servicePrincipals/${claims_sp}/appRoleAssignedTo" \
               --query "value[?principalId=='${sp_id}' && appRoleId=='${payouts_role_id}'] | length(@)")"
if [[ "$held_role" == "0" ]]; then
  az rest --method POST -o none --uri "${graph}/servicePrincipals/${claims_sp}/appRoleAssignedTo" \
    --headers "Content-Type=application/json" \
    --body "{\"principalId\": \"${sp_id}\", \"resourceId\": \"${claims_sp}\", \"appRoleId\": \"${payouts_role_id}\"}"
fi

# The agent app's client secret, made once and kept only in Key Vault.
: "${AZURE_KEY_VAULT_NAME:?run azd provision first}"
tag="$(az keyvault secret show --vault-name "$AZURE_KEY_VAULT_NAME" --name agent-obo-client-secret \
         --query 'tags.placeholder' -o tsv 2>/dev/null || echo missing)"
if [[ "$tag" != "false" ]]; then
  az ad app credential reset --id "$app_id" --append --display-name agent-obo \
       --years 1 --query password -o tsv | tr -d '\n' \
    | az keyvault secret set --vault-name "$AZURE_KEY_VAULT_NAME" \
        --name agent-obo-client-secret --file /dev/stdin --encoding utf-8 \
        --tags placeholder=false -o none
  echo "The agent app's client secret is in Key Vault (agent-obo-client-secret)."
fi

azd env set CLAIMS_SYSTEM_APP_ID "$claims_id" >/dev/null
azd env set ENTRA_SECRET_IN_VAULT true >/dev/null
echo "Claims system app: $claims_name, client id $claims_id, audience $claims_id ($claims_audience)."
echo "Run 'azd provision' once more so both containers get ENTRA_APP_ID and CLAIMS_SYSTEM_APP_ID."
