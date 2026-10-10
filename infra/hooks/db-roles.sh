#!/usr/bin/env bash
# Tier 4a A4: the apps' own database logins on Azure PostgreSQL. Run by
# infra/hooks/postprovision.sh after every `azd provision`; idempotent.
#
# Bicep cannot make a PostgreSQL role, so psql does, from this Mac, as the
# server administrator, running the same infra/sql/roles.sql dev-up runs:
#   claims_agent   owns claims_fnol_dbos (agent_state.*, DBOS's dbos.*)
#   claims_system  owns claims_fnol; SELECT on agent_state.approvals only
# The administrator only makes them; no app logs in as it.
#
# Every password comes from Key Vault, where azd's secretOrRandomPassword keeps
# it (infra/main.parameters.json): the administrator's to log in with, and the
# two roles', which the URLs in Key Vault (infra/modules/postgres.bicep) were
# built from. Each is read into a variable and passed to psql in its
# environment, never on a command line, never echoed.
#
# Needs: psql 15+ (\getenv), `az` signed in with read access to the vault, and
# the server's firewall open to this Mac (OWNER_IP_ADDRESS, asked by the
# preprovision hook). Password authentication stays on (F-41).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"

: "${AZURE_KEY_VAULT_NAME:?run azd provision first}"
: "${AZURE_POSTGRES_HOST:?run azd provision first}"
admin="${AZURE_POSTGRES_ADMIN:-claimsadmin}"

command -v psql >/dev/null || { echo "psql is not installed: brew install postgresql@16" >&2; exit 1; }
if [[ -z "${OWNER_IP_ADDRESS:-}" ]]; then
  echo "PostgreSQL's firewall does not admit this Mac (OWNER_IP_ADDRESS is empty), so the" >&2
  echo "apps' database logins cannot be made. Set it, provision, and run this hook again:" >&2
  echo "  azd env set OWNER_IP_ADDRESS \"\$(curl -s https://api.ipify.org)\" && azd provision" >&2
  exit 1
fi

secret() {
  az keyvault secret show --vault-name "$AZURE_KEY_VAULT_NAME" --name "$1" --query value -o tsv
}

PGPASSWORD="$(secret postgres-admin-password)"
A4_AGENT_PASSWORD="$(secret postgres-agent-password)"
A4_SYSTEM_PASSWORD="$(secret postgres-claims-system-password)"
export PGPASSWORD A4_AGENT_PASSWORD A4_SYSTEM_PASSWORD

PGHOST="$AZURE_POSTGRES_HOST" PGPORT=5432 PGUSER="$admin" PGDATABASE=postgres PGSSLMODE=require \
  psql -X -q \
    -v agent_role=claims_agent -v system_role=claims_system \
    -v agent_db=claims_fnol_dbos -v claims_db=claims_fnol \
    -f "$root/infra/sql/roles.sql"
unset PGPASSWORD A4_AGENT_PASSWORD A4_SYSTEM_PASSWORD

echo "Database logins on $AZURE_POSTGRES_HOST: claims_agent, claims_system (passwords in Key Vault)."
