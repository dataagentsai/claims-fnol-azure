#!/usr/bin/env bash
# Once, by the owner, after `az login` and the first `azd up` from the Mac:
# give GitHub Actions an Azure identity for deploy.yml, with no secret.
#
#   scripts/ci/setup-federated-credential.sh          # asks before each change
#   scripts/ci/setup-federated-credential.sh --yes    # makes every change asked
#
# Idempotent: what exists is kept and said so; only what is missing is made.
# Never deletes anything. Prints the three ids for the GitHub secrets and the
# azd values for the GitHub variables last; sets nothing on GitHub itself.
#
# WHAT IT MAKES
#   1. An app registration, claims-fnol-github, and its service principal: the
#      deploy's own identity. Not the agent's registration (claims-fnol-agent):
#      that one holds a client secret and the payout role, and the people who
#      may change the deploy are not the people the agent acts for.
#   2. Two federated credentials on it (issuer token.actions.githubusercontent.com,
#      audience api://AzureADTokenExchange):
#        repo:dataagentsai/claims-fnol-azure:ref:refs/heads/main
#        repo:dataagentsai/claims-fnol-azure:environment:dev
#      deploy.yml's job runs in the `dev` environment, so its token carries the
#      second subject; the first allows a job on main with no environment.
#   3. The fewest roles provision, the database step and deploy need:
#      | Role | Scope | Why |
#      |---|---|---|
#      | claims-fnol subscription deployer (custom) | subscription | main.bicep is subscription-scoped: create/validate the deployment there, and PUT the resource group it declares. Read the subscription and its locations (azd). Nothing else at subscription scope. |
#      | Contributor | rg-claims-fnol-dev | every resource main.bicep makes or updates, the firewall rule for the runner |
#      | Role Based Access Control Administrator, with a condition | rg-claims-fnol-dev | main.bicep assigns roles (Key Vault, App Configuration, Content Safety); the condition lets it assign or remove only those five roles, never Owner, Contributor or User Access Administrator |
#      | Key Vault Secrets User | the vault | read the PostgreSQL passwords (azd's secretOrRandomPassword, db-roles.sh) and the Groq/Entra secrets' tags (preprovision) |
#      No Microsoft Graph permission: the Entra registrations change from the Mac.
#      Contributor on the whole subscription would also work and is what azd's
#      own `azd pipeline config` assigns; it is not used here because it would
#      let the deploy identity create anything anywhere in the subscription.
#
# Needs: az signed in as an Owner of the subscription (to create a custom role
# and role assignments) who may create app registrations; azd with the dev
# environment the first `azd up` made (for the variables it prints); the
# resource group and vault to exist (that first `azd up`).
set -euo pipefail

REPO="${GITHUB_REPO:-dataagentsai/claims-fnol-azure}"
APP_NAME="${CI_APP_NAME:-claims-fnol-github}"
RG="${AZURE_RESOURCE_GROUP:-rg-claims-fnol-dev}"
ROLE_NAME="claims-fnol subscription deployer"
ISSUER="https://token.actions.githubusercontent.com"
AUDIENCE="api://AzureADTokenExchange"

# The roles main.bicep assigns (infra/modules/*.bicep); tests/test_ci.py holds
# this list equal to the role ids in the Bicep.
ASSIGNABLE_ROLES=(
  4633458b-17de-408a-b874-0445c86b69e6 # Key Vault Secrets User
  b86a8fe4-44ce-4948-aee5-eccb2c155cd7 # Key Vault Secrets Officer
  516239f1-63e1-4d78-a4de-a74fb236a071 # App Configuration Data Reader
  5ae67dd6-50cb-40e7-96ff-dc2bfa4b606b # App Configuration Data Owner
  a97b65f3-24c7-4388-baec-2e87135dc908 # Cognitive Services User
)

yes=false
case "${1:-}" in
  --yes) yes=true ;;
  "") ;;
  *) echo "usage: $0 [--yes]" >&2; exit 2 ;;
esac

confirm() { # what: ask, unless --yes
  if $yes; then return 0; fi
  local answer
  read -r -p "$1 [y/N] " answer
  [[ "$answer" == [yY]* ]] || { echo "Stopped; nothing more changed." >&2; exit 1; }
}
have() { echo "  kept: $1"; }
made() { echo "  made: $1"; }

az account show -o none 2>/dev/null || { echo "Run 'az login' first." >&2; exit 1; }
SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
SUB_SCOPE="/subscriptions/$SUBSCRIPTION_ID"
RG_SCOPE="$(az group show -n "$RG" --query id -o tsv 2>/dev/null)" \
  || { echo "Resource group $RG not found: run 'azd up' from the Mac once first." >&2; exit 1; }
VAULT="$(azd env get-value AZURE_KEY_VAULT_NAME 2>/dev/null || true)"
[[ -n "$VAULT" ]] || { echo "No AZURE_KEY_VAULT_NAME in the azd environment: run from this repository after 'azd up'." >&2; exit 1; }
VAULT_SCOPE="$(az keyvault show -n "$VAULT" --query id -o tsv)"

echo "Subscription $SUBSCRIPTION_ID, tenant $TENANT_ID, repository $REPO"

# 1. The app registration and its service principal
APP_ID="$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)"
if [[ -n "$APP_ID" ]]; then
  have "app registration $APP_NAME ($APP_ID)"
else
  confirm "Create the app registration $APP_NAME?"
  APP_ID="$(az ad app create --display-name "$APP_NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv)"
  made "app registration $APP_NAME ($APP_ID)"
fi
SP_ID="$(az ad sp list --filter "appId eq '$APP_ID'" --query '[0].id' -o tsv)"
if [[ -n "$SP_ID" ]]; then
  have "service principal ($SP_ID)"
else
  confirm "Create its service principal?"
  SP_ID="$(az ad sp create --id "$APP_ID" --query id -o tsv)"
  made "service principal ($SP_ID)"
fi

# 2. The federated credentials
credential() { # name, subject
  local existing
  existing="$(az ad app federated-credential list --id "$APP_ID" --query "[?subject=='$2'].name" -o tsv)"
  if [[ -n "$existing" ]]; then
    have "federated credential $existing ($2)"
    return
  fi
  confirm "Add the federated credential for $2?"
  az ad app federated-credential create --id "$APP_ID" -o none --parameters "$(cat <<JSON
{"name": "$1", "issuer": "$ISSUER", "subject": "$2", "audiences": ["$AUDIENCE"],
 "description": "GitHub Actions, $REPO (deploy.yml)"}
JSON
)"
  made "federated credential $1 ($2)"
}
credential github-main "repo:$REPO:ref:refs/heads/main"
credential github-env-dev "repo:$REPO:environment:dev"

# 3. The roles
if [[ -n "$(az role definition list --name "$ROLE_NAME" --scope "$SUB_SCOPE" --query '[0].id' -o tsv)" ]]; then
  have "custom role '$ROLE_NAME'"
else
  confirm "Create the custom role '$ROLE_NAME' (subscription deployments and the resource group only)?"
  az role definition create -o none --role-definition "$(cat <<JSON
{
  "Name": "$ROLE_NAME",
  "Description": "GitHub Actions deploy of claims-fnol-azure: run the subscription-scoped deployment and PUT its resource group. Nothing else at subscription scope.",
  "Actions": [
    "Microsoft.Resources/deployments/*",
    "Microsoft.Resources/subscriptions/read",
    "Microsoft.Resources/subscriptions/locations/read",
    "Microsoft.Resources/subscriptions/resourceGroups/read",
    "Microsoft.Resources/subscriptions/resourceGroups/write"
  ],
  "NotActions": [],
  "AssignableScopes": ["$SUB_SCOPE"]
}
JSON
)"
  made "custom role '$ROLE_NAME'"
fi

assign() { # role, scope, [condition]
  if [[ -n "$(az role assignment list --assignee "$SP_ID" --role "$1" --scope "$2" --query '[0].id' -o tsv)" ]]; then
    have "'$1' on $2"
    return
  fi
  confirm "Assign '$1' on $2?"
  local extra=()
  if [[ -n "${3:-}" ]]; then extra=(--condition "$3" --condition-version 2.0); fi
  az role assignment create -o none --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
    --role "$1" --scope "$2" "${extra[@]}"
  made "'$1' on $2"
}

ids="$(IFS=,; echo "${ASSIGNABLE_ROLES[*]}")"
condition="((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${ids}})) AND ((!(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${ids}}))"

assign "$ROLE_NAME" "$SUB_SCOPE"
assign "Contributor" "$RG_SCOPE"
assign "Role Based Access Control Administrator" "$RG_SCOPE" "$condition"
assign "Key Vault Secrets User" "$VAULT_SCOPE"

# What to put into GitHub
var() { azd env get-value "$1" 2>/dev/null || true; }
cat <<EOF

Done. In GitHub, $REPO -> Settings:

  Environments: create one named dev (deploy.yml's job runs in it).

  Secrets and variables -> Actions -> Repository secrets:
    AZURE_CLIENT_ID        $APP_ID
    AZURE_TENANT_ID        $TENANT_ID
    AZURE_SUBSCRIPTION_ID  $SUBSCRIPTION_ID

  Repository variables (from this Mac's azd environment):
    AZURE_ENV_NAME         $(var AZURE_ENV_NAME)
    AZURE_LOCATION         $(var AZURE_LOCATION)
    AZURE_KEY_VAULT_NAME   $VAULT
    ENTRA_APP_ID           $(var ENTRA_APP_ID)
    CLAIMS_SYSTEM_APP_ID   $(var CLAIMS_SYSTEM_APP_ID)
    BUDGET_CONTACT_EMAIL   $(var BUDGET_CONTACT_EMAIL)
    OWNER_IP_ADDRESS       $(var OWNER_IP_ADDRESS)   (optional; keeps your own firewall rule as it is)
    DEPLOY_AFTER_CI        true                      (optional; deploy after every green CI on main)

Or with gh:
  gh api -X PUT repos/$REPO/environments/dev
  gh secret set AZURE_CLIENT_ID -R $REPO -b $APP_ID
  gh secret set AZURE_TENANT_ID -R $REPO -b $TENANT_ID
  gh secret set AZURE_SUBSCRIPTION_ID -R $REPO -b $SUBSCRIPTION_ID
  for v in AZURE_ENV_NAME AZURE_LOCATION AZURE_KEY_VAULT_NAME ENTRA_APP_ID CLAIMS_SYSTEM_APP_ID BUDGET_CONTACT_EMAIL OWNER_IP_ADDRESS; do
    gh variable set "\$v" -R $REPO -b "\$(azd env get-value "\$v")"
  done

Then: gh workflow run deploy.yml -R $REPO
EOF
