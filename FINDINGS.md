# Findings

What building the FNOL agent on `agent_harness` found: Tier 1, 2026-10-08/09.
Code comments cite these as "FINDINGS F-n". **Route** says where the change
belongs. Unless the row says otherwise, nothing outside this repo was edited, and each
was worked around here.

| # | Finding | Route | Done here |
|---|---|---|---|
| F-1 | `agent-harness` imports `jinja2` but does not declare it. | Library | Declared in this pyproject. |
| F-2 | The approval-backed action tool (the clothing agent's `refund.py`) is mechanism living in the agent, not the library. | Library | Copied as `approvals/payout.py`. |
| F-3 | The scenario wiring (Temporal test server on the scenario clock, provider-fault twin) lives in reference-agent's `evals/`. | Library, or AgentTwin's binding kit | Copied (`evals/durable.py`, part of `evals/simulation.py`). |
| F-4 | The harness's `no_superseded_state` skips a whole sentence if any modal appears in it, so "…still under assessment, and yes, you can send…" escaped it. | Library | An agent rule judges each clause separately, reusing the harness rule. |
| F-5 | The MCP client sends the session as `customer_id`; the AOAS session field is `policyholder_id`. | Library | The binding's `authorise` hook renames it. |
| F-6 | The AOAS does not say whether a refusal or a handoff wins when one message has both; P-CONCERNS implies the handoff. The several-concerns scenario also scripts an `escalate` call that is never offered to the model. | Spec (AOAS, scenario) | The router checks handoffs before refusals. |
| F-7 | P-OWN-WORDS requires "a named claim or policy", but policyholders name their car's registration. | Spec (AOAS) | `register_claim` is consented by the report itself when no policy reference is named. |
| F-8 | P-FNOL's claim reference is lost: the loop's trace keeps only the row each write acted on, not what it created. | Library / AHC | `entrypoint/reference.py` names any created reference the reply left out. |
| F-9 | The owed-concerns check ignores the harness's own re-read of a row. The resumed-conversation scenario had **passed for the wrong reason** (the model ran past its script and the turn ended in a provider error). | Library | `still_owed` filter in the entrypoint. |
| F-10 | Redaction patterns cannot be extended by an agent; they are a private tuple. | Library | Rebound at import to add driving-licence and bank-account patterns. |
| F-11 | AgentTwin's projection publishes no `entity` on its tools, so the freshness re-read used `get_policy` on a claim, and `_is_about` accepted "not found" as a fresh read. | AgentTwin and Library | The binding declares each tool's entity from the world. |
| F-12 | `resilience.COMPENSATIONS` is the clothing agent's table, and nothing calls it. | Library | AHC-0058 accepted as a gap. |
| F-13 | The AOAS termination list lacked `tool_call_budget_exhausted`, which the harness produces. | Spec | **Fixed**: clean-ai-engineering `d81c39c`. The clothing AOAS has the same gap, still open. |
| F-14 | Clothing words remain in the library: `CUSTOMER_SCOPES` (`orders:*`), the `refunds:write` default, "our order system" in a failure reply, `order_id` in freshness and notify. | Library | Own scopes used. **A turn where the claims system is unreachable still says "order system"**, which a policyholder would see. |
| F-15 | Turn orchestration (`entrypoint`, `promise`, `pending`, `handoff`) is mechanism but lives as an agent seam. | Library | Copied. |
| F-16 | The status vocabulary read "neither has been paid" as asserting `paid`. | Agent | Fixed in `contracts/reading.py`. |

## The reuse measurement (G2.6, second agent)

- `agent_harness` installed by path, not copied: ~15,950 lines (Azure adapters included), unedited.
- `src/claims_fnol`: 3,011 lines, so the library is **~84%** of what this agent runs.
  - ~1,110 lines are mechanism copied from the clothing agent with nouns changed (F-2, F-15). Moving them into the library would put reuse at **~90%**.
  - ~1,900 lines are this insurer's own parts: router and concerns, policy rules, direct answers, the ₹25,000 payout policy, escalation wording and rules, vocabulary, binding, config, telemetry names.
- What would have falsified the reuse decision (T-019): a second agent that had to *edit* a mechanism module. None was edited. The cost showed up instead as **copying** (F-2, F-3, F-15) and **clothing words left in the library** (F-14).
