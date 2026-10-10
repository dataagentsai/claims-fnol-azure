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
`infra/hooks/` and the two Dockerfiles. Checked offline (`tests/test_infra.py`);
CI compiles the Bicep and builds both images on every push (see "CI and
deploy" below). Nothing has been deployed yet.

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
| Entra app registration | — | Made by a hook: `api://claims-fnol`, role `desk.handler`, the sign-in's redirect URI, the policyholder-id attribute; test users with your yes |

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
   `desk.handler` role. The hook then lists the test users it would create in
   your tenant (Rohan Iyer PH-1001, Meera Khanna PH-1002, Asha Rao the claims
   handler) and asks before creating any: type `yes`, or run
   `infra/hooks/entra-app.sh --yes`. See "Sign in on Azure" below.
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
database URL (`claims_agent`), its telemetry, APIM key, Entra secret and
session secret (`agent-session-key`, A2); the
claims system its two URLs (`claims_system`). Neither may read the other's
login or any PostgreSQL password; only APIM reads the vault as a whole.

To run the images CI pushed from the Mac (any commit CI passed on main):

    azd env set AGENT_IMAGE ghcr.io/dataagentsai/claims-fnol-agent:<sha>
    azd env set CLAIMS_SYSTEM_IMAGE ghcr.io/dataagentsai/claims-fnol-claims-system:<sha>
    azd deploy

The images are built with the sibling checkouts named as build contexts (the
command is at the top of each Dockerfile; FINDINGS F-44).

### Sign in on Azure (Tier 4a A2; not run against a tenant yet)

On Azure there is no test page: `/signin` sends the browser to your tenant's
Microsoft sign-in, the OAuth 2.0 code flow with PKCE and a nonce
(`src/claims_fnol_app/signin_flow.py`, on the library's `EntraLogin`). The app
is a confidential client: the server redeems the code with the app's existing
client secret from Key Vault, so the browser never sees a refresh token.

    open "$(azd env get-value AGENT_URL)/signin"

Who lands where, decided from the token Entra signs:

| Signed in as | Lands on | Why |
|---|---|---|
| a policyholder (the token carries `extn.customer_id`) | the chat, as that policyholder | the policyholder id is a directory extension attribute on the user (FINDINGS F-91) |
| a claims handler (app role `desk.handler`) | the desk | the role maps to the desk's four scopes (`role_scopes`, F-30) |
| anyone else in the tenant | "your account is not linked to a policy yet" | no policyholder id and no desk role |

The page keeps the access token in memory only (it arrives in the link's
fragment, which no server logs). When it expires the page asks
`/signin/refresh` for a new one: the refresh token is kept server-side in
`agent_state.sessions`, encrypted with a key derived from `agent-session-key`.
**Sign out** deletes that stored login and ends the Entra session too; an
access token already issued stays valid until it expires (F-94).

**The test users.** `infra/hooks/entra-app.sh` (run by postprovision) prints
the users it would create in your tenant and creates them only after you type
`yes`, or with `--yes`:

    infra/hooks/entra-app.sh --yes      # with azd's environment loaded, as postprovision runs it

| User | In the token | Can |
|---|---|---|
| `rohan@<your domain>`, Rohan Iyer | `extn.customer_id = PH-1001` | chat as PH-1001 |
| `meera@<your domain>`, Meera Khanna | `extn.customer_id = PH-1002` | chat as PH-1002 |
| `asha@<your domain>`, Asha Rao | role `desk.handler` | decide payouts and escalations at the desk |

Each new user's temporary password is generated by the hook, written straight
to Key Vault as `test-user-<login>-password`, and never shown or written to a
file. Read it when you need it; Microsoft asks for a new one at the first
sign-in:

    az keyvault secret show --vault-name "$(azd env get-value AZURE_KEY_VAULT_NAME)" \
      --name test-user-rohan-password --query value -o tsv

Re-running the hook never remakes a user or resets a password: it only sets
the attribute and the role again. The attribute's name comes from one setting,
`HOLDER_CLAIM` (Bicep `holderClaim`, default `extn.customer_id`), which the
agent's sign-in, the claims system's token check and the hook all read. To
link another person in your tenant to a policy:

    az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/users/<user@domain>" \
      --body "{\"extension_$(azd env get-value ENTRA_APP_ID | tr -d -)_customer_id\": \"PH-1001\"}"

Remove the test users with `az ad user delete --id rohan@<your domain>` (and
`meera`, `asha`); `azd down` does not, since they are not in the resource group.

### CI and deploy

**On every push and pull request to main**, `.github/workflows/ci.yml`:

1. checks out this repository and its five siblings beside it, as on the Mac
   (`../reference-agent`, `../agenttwin`, `../clean-ai-engineering`,
   `../ai-harness-catalog`, `../ai-assurance-catalog`), each at `main`; the
   run's summary lists the commit of each (pin one by setting its `*_REF` to a SHA);
2. starts PostgreSQL 16 as a service and sets `CLAIMS_TEST_SERVER_URL` to its
   superuser, so the role tests run;
3. ruff, mypy, lint-imports, actionlint;
4. the full suite (the scripted model; no call reaches Groq), writing the JUnit
   report the gates read;
5. the four gates (`clean-ai-engineering/tools/gates.py`); the verdict is the
   run's `gates-verdict` artifact;
6. compiles `infra/main.bicep`;
7. builds both images. Only on a push to main, and only after all of the
   above passed, they are pushed to GitHub Container Registry as
   `ghcr.io/dataagentsai/claims-fnol-agent` and
   `ghcr.io/dataagentsai/claims-fnol-claims-system`, tagged with the commit SHA
   and `latest`. A pull request builds them and pushes nothing.

New packages on GHCR start private. Once, after the first push to main, make
both public (the package's settings, "Change visibility"), so Container Apps
can pull them with no registry credentials.

**Deploy** (`.github/workflows/deploy.yml`) runs `azd provision` then
`azd deploy` with one commit's images. It signs in to Azure with OIDC: GitHub's
token for the job is exchanged for an Entra token, so no client secret exists.
It deploys only a commit whose CI succeeded on a push to main. It stays off
until it is set up, and says so on the run page.

Set it up once, after the first `azd up` from the Mac (that run asks for the
Groq key and makes the Entra apps; a runner can do neither):

    az login
    scripts/ci/setup-federated-credential.sh        # asks before each change; --yes to skip asking

The script is idempotent and makes:

- a deploy identity, `claims-fnol-github`;
- federated credentials for `main` and for the `dev` environment;
- the fewest roles that identity needs:
  - a custom role at subscription scope, for the deployment and its resource group only;
  - Contributor on `rg-claims-fnol-dev`;
  - Role Based Access Control Administrator there, limited to the five roles the Bicep assigns;
  - Key Vault Secrets User on the vault.

It gives the identity no Microsoft Graph permission. It ends by printing what
to set in GitHub:

- **Repository secrets** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
  `AZURE_SUBSCRIPTION_ID`. These are ids, not passwords; nothing secret is stored.
- **Repository variables**, from your azd environment: `AZURE_KEY_VAULT_NAME`,
  `ENTRA_APP_ID`, `CLAIMS_SYSTEM_APP_ID`, `BUDGET_CONTACT_EMAIL` (required),
  and `AZURE_ENV_NAME`, `AZURE_LOCATION`, `OWNER_IP_ADDRESS` (optional).
- **An environment** named `dev`.

Then deploy:

    gh workflow run deploy.yml                       # the head of main
    gh workflow run deploy.yml -f sha=<full sha>     # a given commit CI passed

To deploy after every green CI on main, set the repository variable
`DEPLOY_AFTER_CI` to `true`.

The runner skips the hooks that ask questions or need Graph, and runs the rest
itself:

- `preprovision.sh` runs unchanged.
- It checks that the Groq key and the Entra client secret are real in the
  vault. Otherwise provision would write placeholders over them.
- `entra-app.sh` is not run. App registrations change from the Mac
  (`azd hooks run postprovision`).
- `db-roles.sh` runs unchanged. A firewall rule admits the runner's IP for
  that one step and is removed afterwards, even when the step fails.

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

### Pause the agent (not run yet)

The kill switch is the App Configuration key `agent.enabled` under the label
`dev` (Tier 4a A13). The agent reads it before every turn and re-reads it every
30 seconds. Pause the agent: `az appconfig kv set --name <store> --key agent.enabled --label dev --value false --yes`

```bash
STORE=$(azd env get-value AZURE_APP_CONFIGURATION_ENDPOINT | sed -E 's#https://([^.]+)\..*#\1#')
az appconfig kv set --name $STORE --key agent.enabled --label dev --value false --yes
az appconfig kv set --name $STORE --key agent.enabled --label dev --value true --yes   # back on
```

Within 30 seconds every new message gets the paused reply ("The claims
assistant is paused for now…"): no model call and no route runs, the message
is kept on the conversation, and the turn is counted as
`agent.turns{result="paused"}` with `agent.enabled = false` on its span.
Approvals and escalations already waiting at the handler desk are not
cancelled and can still be decided there. A value that is not `true` or
`false` is refused and the last good one kept. `azd provision` writes the
key back to `true` (FINDINGS F-88). On this Mac it is `AGENT_ENABLED=false` in
`.env`, re-read every 5 seconds.

### Remove it

    azd down --purge

deletes the resource group and purges what Azure would otherwise keep
soft-deleted (Key Vault, API Management, Content Safety), so the names can be
used again at once. The Entra app registration is not in the resource group:
`az ad app delete --id "$(azd env get-value ENTRA_APP_ID)"`.
