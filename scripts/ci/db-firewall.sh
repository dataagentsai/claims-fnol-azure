#!/usr/bin/env bash
# CI only (deploy.yml): admit this runner to Azure PostgreSQL for the database
# roles step (FINDINGS F-70), then take it out again.
#
#   open    adds the rule gh-runner-<run id>-<attempt> for this runner's public
#           IP (one address, a /32) and prints the IP for the next step
#   close   deletes every gh-runner-* rule, this run's and any a cancelled run
#           left behind; deploy.yml runs it under always()
#
# The owner's own rule (owner-ip) and "Allow Azure services" are never touched.
# The password (from Key Vault) and TLS (PGSSLMODE=require) still gate the
# login; the rule exists for the minute db-roles.sh takes.
#
# Reads the resource group and server from the azd environment (provision's
# outputs). Writes `ip=<address>` to $GITHUB_OUTPUT on open.
set -euo pipefail

value() { azd env get-value "$1"; }
rg="$(value AZURE_RESOURCE_GROUP)"
server="$(value AZURE_POSTGRES_SERVER)"
prefix="gh-runner-"

case "${1:-}" in
  open)
    ip="$(curl -fsS --max-time 10 https://api.ipify.org)"
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "::error::no public IPv4 for this runner ('$ip')." >&2; exit 1; }
    name="${prefix}${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
    az postgres flexible-server firewall-rule create -g "$rg" --name "$server" \
      --rule-name "$name" --start-ip-address "$ip" --end-ip-address "$ip" -o none
    echo "ip=$ip" >> "${GITHUB_OUTPUT:-/dev/null}"
    echo "Firewall rule $name admits this runner on $server."
    ;;
  close)
    rules="$(az postgres flexible-server firewall-rule list -g "$rg" --name "$server" \
               --query "[?starts_with(name, '${prefix}')].name" -o tsv)"
    for rule in $rules; do
      az postgres flexible-server firewall-rule delete -g "$rg" --name "$server" --rule-name "$rule" --yes -o none
      echo "Removed firewall rule $rule."
    done
    [[ -n "$rules" ]] || echo "No runner rule on $server."
    ;;
  *)
    echo "usage: $0 open|close" >&2
    exit 2
    ;;
esac
