# claims-fnol-azure

A motor insurer's claims agent (first notice of loss) on Azure, with Pydantic AI as the model layer — T-099 in clean-ai-engineering/TODO.md.

Specs: `../clean-ai-engineering/drafts/examples/motor-claims-fnol.aoas.yaml` (what it does), `../clean-ai-engineering/stacks/azure.yaml` (what it runs on), `harness-profile.yaml` (this agent's own choices).

Status: Tier 2 done — you can chat with it on your Mac with the real model
(see "Run it on your Mac"). Tier 1: the agent (`src/claims_fnol`, this insurer's seams on the
`agent_harness` library) passes all four gates on a Mac with a scripted model:

    cd ../clean-ai-engineering && uv run tools/gates.py ../claims-fnol-azure

Tests: `uv run python -m pytest -q`. The claims system for the gates is the
AgentTwin world (`evals/simulation.py`) with a scripted model, and stays so.

Tier 2 — you can chat with it on your Mac:

- `src/claims_system/` — the claims system as our own MCP server on PostgreSQL
  (the AOAS's eight claims-system operations, plain-SQL migration, seeded from
  the FNOL world).
- `src/claims_fnol_app/` — the composition root: the chat page and the claims
  handler's desk on whichever adapters the overlay names (Groq through the
  harness's Pydantic AI client, DBOS waits for payout approvals and escalations,
  local sign-in, on this Mac).
- `scripts/dev-up.sh`, `scripts/dev-down.sh`, `scripts/smoke.py`.

Tier 2b — adapters from configuration, checks as plug-ins:

- `config/local.yaml`, `config/test.yaml`, `config/azure.yaml` — one overlay per
  environment: which adapter fills each port, its settings, secrets by reference
  only, and why it differs from the profile. `CLAIMS_FNOL_ENV` names the file and
  nothing else; `agent_harness.adapters` builds what it names.
- `src/claims_fnol/policy/evaluators.yaml` — where each reply check runs (reply,
  online, release), read through `agent_harness.evals`; moving one is a YAML edit.

Tier 3 — the Azure infrastructure as Bicep + azd, written and checked offline
(not yet deployed): see "Deploy to Azure".

## Run it on your Mac

Needs: the Homebrew PostgreSQL service running (`brew services list` shows
`postgresql@16 started`; the scripts never start or stop it), `uv`, and a `.env`
(copy `.env.example`; it holds the database URLs and `GROQ_API_KEY`, and is
gitignored). The databases are `claims_fnol` (the claims system) and
`claims_fnol_dbos` (the DBOS waits and the agent's own state). Each app logs in
as its own role (A4, `infra/sql/roles.sql`):

| Role | Owns | In the other database |
|---|---|---|
| `claims_agent` | `claims_fnol_dbos`: `agent_state.*` and DBOS's `dbos.*` (DBOS makes its schema at launch, as this role) | no CONNECT on `claims_fnol` |
| `claims_system` | `claims_fnol` and its tables | CONNECT, and `SELECT` on `agent_state.approvals` only |
| `claims_fnol` (admin) | nothing an app uses | makes the databases and the two roles; no app runs as it |

On a new machine, make only the admin role; `scripts/dev-up.sh` does the rest
(the databases, both roles, a password per role kept in `.env`, the grants), and
on an existing setup hands over what the admin role made before A4:

    psql -d postgres -c "CREATE ROLE claims_fnol LOGIN CREATEDB CREATEROLE PASSWORD '<password>'"

Start, use, stop:

    scripts/dev-up.sh            # migrate + seed, start the claims MCP server (:9050) and the app (:8077)
    open http://127.0.0.1:8077/signin
    scripts/dev-down.sh          # stops both; PostgreSQL stays running

`scripts/dev-up.sh --fresh` drops every claim the demo made and resets the seeded
ones (CLM-010003 and CLM-010004 approved again), so the payout flows can be
tried again. Logs are in `.state/agent.log` and `.state/claims-system.log`;
every model call is one line in the agent's log, and the totals are at
`http://127.0.0.1:8077/dev/usage`.

The claims system believes no one's word for who is asking (A1). Every MCP call
from the app carries a 5-minute token the app's local issuer mints for the
claims system (`Authorization: Bearer`), and the claims system verifies it with
the keys the app publishes at `http://127.0.0.1:8077/.well-known/jwks.json`
(`config/claims-system/local.yaml`). A call with no token, or with the
policyholder's own session token, is refused. If you start the app on another
port (`AGENT_PORT`), `dev-up.sh` points the claims system at it.

At `/signin`, pick **Rohan Iyer** (PH-1001) or **Meera Khanna** (PH-1002) for the
chat, or **Asha Rao** (claims handler) for the desk. Then, as Rohan:

| Say | What happens |
|---|---|
| A bus hit my car KA-01-AB-1234 this morning. I want to make a claim. | The claims system registers it and the reply gives its CLM- reference. |
| Please release the payment for CLM-010004. | ₹25,001 is past the ₹25,000 limit: it waits. As Asha, approve it on the desk; it is paid. |
| Please release the payment for CLM-010003. | ₹25,000 is on the limit: paid at once, nobody asked. |
| What's the status of claim CLM-010007? | Answered from the claims system with no model call. |

The live check of those four, with the real model: `.venv/bin/python scripts/smoke.py`
after `scripts/dev-up.sh --fresh`. Its last results are under "Smoke check" in
FINDINGS.md.

## Deploy to Azure

Tier 3: `infra/` (Bicep, one module per resource, each starting with what it is,
why it is here and what it costs), `azure.yaml` (the azd project),
`infra/hooks/` and the two Dockerfiles. **Written and checked offline only**
(`tests/test_infra.py`): `az`, `azd` and `bicep` are not installed on this Mac,
so nothing has been compiled or deployed yet.

### What it creates

In one resource group, `rg-claims-fnol-dev`, Central India (Content Safety in
South India, the only Indian region that has it):

| Resource | Tier | What it is for |
|---|---|---|
| Budget | $10/month | Alerts at 50% and 80% spent and at a 100% forecast; created first |
| Log Analytics + Application Insights | PerGB2018, 30 days, 0.1 GB/day cap | Traces of every conversation, APIM's token metrics |
| Container Apps environment + 2 apps | Consumption, min 0 / max 1 replicas | `agent` (public HTTPS), `claims-system` (internal only) |
| 3 user-assigned managed identities | — | One per app and one for APIM; secrets read through them |
| PostgreSQL flexible server | Burstable B1ms, 32 GB, no HA, 7-day backups | `claims_fnol` and `claims_fnol_dbos`; `vector` allowed |
| Key Vault | Standard, RBAC, soft delete, no purge protection | Every secret; the overlay names them, never holds them |
| API Management | Consumption | The model gateway: rate limit, token metrics, Content Safety, Groq |
| Content Safety | F0 (free) | Screens each prompt and completion at APIM |
| App Configuration | Free | `payout_limit_inr`, `online_sample_rate` (read by nothing yet, F-35) |
| Entra app registration | — | Made by a hook: `api://claims-fnol`, role `desk.handler` |

Images come from GitHub Container Registry as public images, so there is no
Azure Container Registry: azd deploys a `containerapp` service that names an
`image` and no `project` from that image as it is. Until Tier 4 pushes them, the
apps run a public placeholder image.

### Prerequisites

- An Azure subscription where you are Owner and can create app registrations.
- `az` (Azure CLI) and `azd` (Azure Developer CLI); Bicep comes with `az`.
- A Groq API key, typed once when the hook asks; it goes straight to Key Vault.

### Steps

    az login                      # the hooks use az (Key Vault, Entra)
    azd auth login                # azd's own sign-in, same tenant
    azd env new dev               # once
    azd env set AZURE_LOCATION centralindia
    azd up                        # provision, then deploy

`azd up` asks:

1. **Before provisioning** (`infra/hooks/preprovision.sh`): the email for the
   budget alerts, and optionally your public IP so `psql` from the Mac can reach
   PostgreSQL. Asked once; kept in `.azure/dev/.env` (gitignored).
2. **After provisioning** (`infra/hooks/postprovision.sh`): your Groq API key.
   It is read without being shown and passed to `az keyvault secret set` on its
   standard input: it is never in a file, a parameter or the shell history.
   Asked only while the vault holds the placeholder. Then the Entra app
   registration is created or brought up to date, and you are given the
   `desk.handler` role.
   Last, the apps' own database logins (A4, `infra/hooks/db-roles.sh`): psql
   from this Mac, as the PostgreSQL administrator, runs `infra/sql/roles.sql`,
   the same script dev-up runs. `claims_agent` gets `claims_fnol_dbos`,
   `claims_system` gets `claims_fnol` and `SELECT` on `agent_state.approvals`
   only. Every password (the administrator's and one per login) is made by azd
   (`secretOrRandomPassword`), kept in Key Vault and read from there without
   being shown. It needs `psql` and your IP in the firewall (the preprovision
   question); without them the hook stops and says so, and the apps cannot log
   in until `azd hooks run postprovision` succeeds.

Each app's identity may read only its own secrets in Key Vault, one role
assignment per secret (`infra/modules/keyvault-access.bicep`): the agent its
database URL (`claims_agent`), its telemetry, APIM key and Entra secret; the
claims system its two URLs (`claims_system`). Neither may read the other's
login or any PostgreSQL password; only APIM reads the vault as a whole.

Once Tier 4 has pushed the images:

    azd env set AGENT_IMAGE ghcr.io/<owner>/claims-fnol-agent:<tag>
    azd env set CLAIMS_SYSTEM_IMAGE ghcr.io/<owner>/claims-fnol-claims-system:<tag>
    azd deploy

The images are built with the sibling checkouts named as build contexts (the
command is at the top of each Dockerfile; FINDINGS F-44).

### What it costs

Running means PostgreSQL started; stopped means stopped. Everything else costs
nothing at dev traffic either way.

| Resource | Price | Running | Stopped | Source |
|---|---|---|---|---|
| PostgreSQL B1ms compute | $0.0245/hour | $0.59/day | $0 | Retail Prices API, Central India, meter "B1MS" |
| PostgreSQL storage, 32 GB | $0.131/GB-month | $0.14/day | $0.14/day | Retail Prices API, "Storage Data Stored" |
| PostgreSQL backups, 7 days | $0.095/GB-month beyond the free amount | ~$0 | ~$0 | Retail Prices API, "Backup Storage LRS"; free amount equal to provisioned storage: estimate, verify |
| Container Apps | first 180,000 vCPU-s, 360,000 GiB-s, 2M requests a month free; then $0.000024/vCPU-s, $0.000003/GiB-s, $0.40/M requests | $0 | $0 | azure.microsoft.com/pricing/details/container-apps (grant); Retail Prices API (rates) |
| API Management, Consumption | first 1M calls a month free, then $0.035/10K | $0 | $0 | Retail Prices API, "Consumption Calls" |
| Log Analytics / App Insights | first 5 GB a month free, then $3.22/GB; 0.1 GB/day cap | $0 | $0 | Retail Prices API, "Analytics Logs Data Ingestion"; free 5 GB per billing account: estimate, verify |
| Key Vault, Standard | $0.03/10K operations | ~$0 | ~$0 | Retail Prices API, "Standard Operations" |
| App Configuration, Free | $0 | $0 | $0 | Retail Prices API, "Free Instance" |
| Content Safety, F0 | 5,000 text records a month free | $0 | $0 | azure.microsoft.com/pricing/details/cognitive-services/content-safety |
| Budget, identities, Entra app | free | $0 | $0 | — |
| **Total** | | **~$0.73/day** | **~$0.14/day** | |

Retail Prices API: `https://prices.azure.com/api/retail/prices`, filtered by
service and `armRegionName eq 'centralindia'`, read 9 Oct 2026, USD. Left running
for a whole month PostgreSQL alone is about $22, more than twice the budget
(F-47), so stop it when you are done:

    az postgres flexible-server stop  -g rg-claims-fnol-dev -n "$(azd env get-value AZURE_POSTGRES_SERVER)"
    az postgres flexible-server start -g rg-claims-fnol-dev -n "$(azd env get-value AZURE_POSTGRES_SERVER)"

Azure starts a stopped server again by itself after 7 days.

### Roll a canary (not run yet; Tier 5 exercise)

The agent app runs in **Multiple** revision mode (Tier 4a A7): a new revision
starts with no traffic until you give it some. The claims system stays Single.

```bash
RG=rg-claims-fnol-dev
APP=$(az containerapp list -g $RG --query "[?contains(name,'agent')].name" -o tsv)
az containerapp revision list -g $RG -n $APP -o table          # see both revisions
az containerapp ingress traffic set -g $RG -n $APP \
  --revision-weight <old-revision>=95 <new-revision>=5          # 5% to the canary
az containerapp ingress traffic set -g $RG -n $APP \
  --revision-weight latest=100                                  # promote, or point back to roll back
```

### Change the payout limit without a redeploy (not run yet; Tier 5 exercise)

The automatic payout limit (₹25,000, the AOAS's `issue_payout.authority`) is
the App Configuration key `payout.automatic_limit_inr` under the label `dev`
(Tier 4a A6). The agent and the claims system both read that one key with
their own identities (App Configuration Data Reader), once per payout, and
re-read it every 30 seconds, so a change reaches the next payout with no
redeploy and no restart.

```bash
STORE=$(azd env get-value AZURE_APP_CONFIGURATION_ENDPOINT | sed -E 's#https://([^.]+)\..*#\1#')
az appconfig kv set --name $STORE --key payout.automatic_limit_inr --label dev --value 20000
az appconfig kv show --name $STORE --key payout.automatic_limit_inr --label dev
```

That is `az appconfig kv set --name <store> --key payout.automatic_limit_inr --label dev --value 20000`.
Within 30 seconds a ₹25,000 payout (CLM-010003) waits for a claims handler
instead of being paid, and its `agent.approval.assess` span records
`agent.payout.automatic_limit_inr = 20000`. Set it back to 25000, or delete
the key, to restore the AOAS's limit. A value above 25000, or one that is not
a number, is refused: both apps keep the last good value and log why, because
a limit above the AOAS's is a spec change, not a setting.

On this Mac the same key is `PAYOUT_AUTOMATIC_LIMIT_INR` in `.env`, re-read
every 5 seconds (`config/local.yaml`).

### Remove it

    azd down --purge

deletes the resource group and purges what Azure would otherwise keep
soft-deleted (Key Vault, API Management, Content Safety), so the names can be
used again at once. The Entra app registration is not in the resource group:
`az ad app delete --id "$(azd env get-value ENTRA_APP_ID)"`.
