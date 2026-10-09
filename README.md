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
- `src/claims_fnol_app/` — the composition root (`CLAIMS_FNOL_ENV=local|test|azure`):
  Groq through the harness's Pydantic AI client, DBOS waits for payout approvals
  and escalations, the chat page and the claims handler's desk, local sign-in.
- `scripts/dev-up.sh`, `scripts/dev-down.sh`, `scripts/smoke.py`.

## Run it on your Mac

Needs: the Homebrew PostgreSQL service running (`brew services list` shows
`postgresql@16 started`; the scripts never start or stop it), `uv`, and a `.env`
(copy `.env.example`; it holds the database URLs and `GROQ_API_KEY`, and is
gitignored). The databases are `claims_fnol` (the claims system) and
`claims_fnol_dbos` (the DBOS waits and the agent's own state), owned by the role
`claims_fnol`. To create them on a new machine:

    psql -d postgres -c "CREATE ROLE claims_fnol LOGIN CREATEDB PASSWORD '<password>'"
    psql -d postgres -c "CREATE DATABASE claims_fnol OWNER claims_fnol"
    psql -d postgres -c "CREATE DATABASE claims_fnol_dbos OWNER claims_fnol"

Start, use, stop:

    scripts/dev-up.sh            # migrate + seed, start the claims MCP server (:9050) and the app (:8077)
    open http://127.0.0.1:8077/signin
    scripts/dev-down.sh          # stops both; PostgreSQL stays running

`scripts/dev-up.sh --fresh` drops every claim the demo made and resets the seeded
ones (CLM-010003 and CLM-010004 approved again), so the payout flows can be
tried again. Logs are in `.state/agent.log` and `.state/claims-system.log`;
every model call is one line in the agent's log, and the totals are at
`http://127.0.0.1:8077/dev/usage`.

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
