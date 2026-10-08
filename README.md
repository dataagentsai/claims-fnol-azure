# claims-fnol-azure

A motor insurer's claims agent (first notice of loss) on Azure, with Pydantic AI as the model layer — T-099 in clean-ai-engineering/TODO.md.

Specs: `../clean-ai-engineering/drafts/examples/motor-claims-fnol.aoas.yaml` (what it does), `../clean-ai-engineering/stacks/azure.yaml` (what it runs on), `harness-profile.yaml` (this agent's own choices).

Status: Tier 1 done — the agent (`src/claims_fnol`, this insurer's seams on the
`agent_harness` library) passes all four gates on a Mac with a scripted model:

    cd ../clean-ai-engineering && uv run tools/gates.py ../claims-fnol-azure

Tests: `uv run python -m pytest -q`. The claims system for the gates is the
AgentTwin world (`evals/simulation.py`); the PostgreSQL MCP claims server is Tier 2.
