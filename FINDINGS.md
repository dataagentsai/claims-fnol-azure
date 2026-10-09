# Findings

What building the FNOL agent on `agent_harness` found: Tier 1, 2026-10-08/09;
Tier 2 (chat with it on the Mac, real model), 2026-10-09, from F-17; Tier 2b
(adapters from configuration, checks as plug-ins), 2026-10-09, from F-27.
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
| F-17 | The harness's `no_superseded_state` judged a sentence naming a row nobody had read against the one row that was read. The real model's correct FNOL reply, "registered under reference CLM-019002", was blocked as saying POL-010001 is registered, and the policyholder got "I am not able to confirm that". The gates' scripted reply named no reference, so they never met it. | Library | **Fixed**: agent_harness `10ca3d3` (the fallback applies only when the sentence names no row, as its docstring said); a row added to its table; reference suite 1656 passed. |
| F-18 | gpt-oss-120b writes references with a non-breaking hyphen (U+2011, "CLM‑019002") and puts narrow no-break spaces (U+202F) between words, in replies and potentially in tool arguments. Every pattern that reads a reply (identifiers, statuses, created references) matches the ASCII hyphen, so a correct reply read as naming nothing. | Library (the model boundary) | `entrypoint/plain.py`: `PlainText` normalises hyphens and spaces in every model answer, text and tool arguments, at `build`. Belongs at the harness's typed boundary for every agent. |
| F-19 | The AOAS says `submit_document` lowers `documents_missing` by one, and that `documents_pending` means at least one is missing; the last document breaks the invariant unless something moves the claim, and the only transition out is the assessor's (`by: external`). | Spec (AOAS) | The claims system moves a `documents_pending` claim whose last document arrives to `under_assessment`. AgentTwin's world does not count documents down (its `not_faithful_about`), so the gates never met it. |
| F-20 | `serve.build` and `reviewer.build` type their desk parameters as the Temporal classes (`ApprovalDesk`, `EscalationDesk`), not protocols, so the DBOS desks fit only by duck typing. | Library | `type: ignore[arg-type]` in `claims_fnol_app/edge.py`. Works at run time; a protocol in `contracts` would make it checked. |
| F-21 | After the real model read a claim and then paid it, "has now been paid" was blocked as superseded: the payout tool's answer did not name its row, so the earlier read stayed "latest". The harness's `_latest_states` also recognises a row only by `id` or `order_id` — another clothing word (F-14), and a far end answering with `claim_id` would be invisible to it. | Agent and Library | The payout tool's answer carries `id` (the AOAS: the write's own answer is authoritative). The library's `order_id` is still there. |
| F-22 | AgentTwin's projection ignores `register_claim.identity` (the same incident reported twice is one claim): it made CLM-019002 and CLM-019003 for one collision. Our claims system returns the open claim's reference again. A scenario that reports twice would pass against one far end and not the other. | AgentTwin | None here; the claims system follows the AOAS. |
| F-23 | Locally the claims system believes the session the agent asserts, as the AgentTwin world does: the harness's MCP client sends `{customer_id}` and no token without an exchange. Money is still checked at the far end: `issue_payout` must name an approval the claims system reads from the DBOS wait and finds covering the call (granted, unexpired, same policyholder, claim, amount and key, a person above ₹25,000). | Binding (Tier 4) | The `authorise` hook is where Tier 4 verifies an Entra token for the claims system's audience. |
| F-24 | The library ships the PostgreSQL adapters (`PostgresCheckpointStore`, `PostgresRequests`) but not the tables they write; the DDL lives in the reference agent's `sql/001_schemas.sql`. `agree_on_durability` rightly refuses an in-memory conversation beside DBOS waits, so a second agent must copy the DDL to run at all. | Library | Copied as `claims_fnol_app/migrations/001_agent_state.sql`. |
| F-25 | The real model wrote "£25,000" for a payout in rupees. No output rule checks the currency symbol: `no_ungrounded_entity` reads ₹, Rs and INR, and bare digits, so the figure was grounded and the pound sign passed. | Agent (an output rule), from the AOAS's `currency: INR` | Not fixed: recorded. A rule refusing any currency other than the AOAS's would replace a right amount in the wrong symbol with "I am not able to confirm that", so the better fix is to say the currency in the prompt and normalise the symbol. |
| F-26 | The task named `request_payout` and `escalate` among the claims system's operations; the AOAS lists them under `operations` but not under `external.claims_system.operations`. One is the agent's tool on the approval wait (`routes_to: issue_payout`), the other the escalation wait. | Spec (wording) | The claims system serves the eight operations of `external.claims_system`; a test holds its surface to that list. |
| F-27 | The Temporal approval wait expired the moment it was asked when asked with no reminder (`remind_before_s=0`): its second wait was `ttl − ttl = 0` seconds. The DBOS wait was right. Nothing had run the same row against both engines until the approval port's contract table did. | Library | **Fixed**: `agent_harness.approvals.durable` waits the whole validity when there is no reminder; the row is in `tests/test_adapter_contracts.py` (reference). |
| F-28 | `telemetry.configure` chose Azure Monitor by an environment variable (`APPLICATIONINSIGHTS_CONNECTION_STRING`), and the telemetry mechanism imported the Azure adapter to do it: an environment branch inside mechanism. | Library | `configure` takes an explicit `backend`, which the `azure-monitor-otel` adapter passes. The variable is still honoured when no backend is given, for the reference agent's root (follow-up). |
| F-29 | The model layer was chosen by branches: `llm.connect_model(layer="pydantic-ai")` on a string, and `llm.pydantic_ai.from_choice` on the provider's name (`AZURE_PROVIDERS`). | Library | The registry's model adapters are chosen by name from the overlay; both functions remain for the reference's composition root (follow-up). |
| F-30 | `serve` verifies every session with `identity.verify`, which reads Keycloak's claim shapes. Entra's `scp` is a space-separated string, which that reads as no scopes, so on the Azure stack every handler would be refused at the desk. | Library (Tier 4) | **Fixed**: agent_harness `63b7a12`. `serve` and the desk take `verify`, the identity adapter's verifier; `edge.py` passes `Sessions.verify`. The `entra-id` adapter's `role_scopes` maps the app role `desk.handler` to the desk's four scopes (`config/azure.yaml`). Rows at the edge in the library's identity table (every adapter) and here (`tests/test_identity_edge.py`, the local and Azure overlays); the Azure rows fail on the old wiring. |
| F-31 | The library has no in-memory approval or escalation adapter: the gates run Temporal's test server, and the evaluation code's `Remembered*` wrappers are not a wait. The `approval` port has two adapters, not three. | Library | Registry has `temporal-updates` and `dbos-workflows`. |
| F-32 | `stacks/*.yaml` name only what production runs. What a developer's Mac and a test run had no adapter names: `scripted`, `groq-direct`, `in-memory`, `local-dev`, `console` were added to the registry, and an overlay that uses one says `why` it leaves the profile. | Spec (stacks) | Registry names; `config/local.yaml` and `config/test.yaml` say why. |
| F-33 | Nothing realised the `secrets` binding: secrets were read ad hoc (`claims_fnol_app.settings`, `os.environ` across the reference's scripts), and an overlay could not say "this comes from Key Vault". | Library | A `secrets` port: `environment-settings` (environment, then a dotenv file) and `key-vault` (REST with the managed identity's token, no SDK). Overlays name secrets by reference only; a value is refused. |
| F-34 | The AOAS's `agent.currency: INR` has no statement id, so `money_in_rupees` (F-25) is tagged `P-PAYOUT` (the amount) — nothing in the spec says a reply states money in the agent's currency. | Spec (AOAS) | Tagged `P-PAYOUT`, `AHC-0094`. |
| F-35 | The `config` binding (`typed-settings`, Azure `app-configuration`) has no port: thresholds are pydantic settings read from the environment, and App Configuration has no adapter. | Library (Tier 3/4) | Not in the registry; listed as a follow-up. |
| F-36 | The `Evaluator` port is synchronous because the inline hook (`policy.enforce`) is. Azure's evaluators and a judge model are network calls; their adapters will run in a thread, or the port gains an async twin, when Tier 12 builds them. | Library (design) | Recorded. |
| F-37 | Slide 94's `min: baseline` has nothing to compare with: no per-evaluator baseline is stored. | Library (Tier 12) | Startup refuses a `min` that is not a pass rate. |
| F-38 | Tier 2b gives the harness injectable, versioned graders (`evaluators.yaml`, `EvalResult.version`), yet this profile still lists AHC-0028 as an accepted gap. | Profile | Left as accepted until the gates' harness check is re-run with tests discharging it here. |

## The reuse measurement (G2.6, second agent)

- `agent_harness` installed by path, not copied: ~15,950 lines (Azure adapters included), unedited.
- `src/claims_fnol`: 3,011 lines, so the library is **~84%** of what this agent runs.
  - ~1,110 lines are mechanism copied from the clothing agent with nouns changed (F-2, F-15). Moving them into the library would put reuse at **~90%**.
  - ~1,900 lines are this insurer's own parts: router and concerns, policy rules, direct answers, the ₹25,000 payout policy, escalation wording and rules, vocabulary, binding, config, telemetry names.
- What would have falsified the reuse decision (T-019): a second agent that had to *edit* a mechanism module. None was edited. The cost showed up instead as **copying** (F-2, F-3, F-15) and **clothing words left in the library** (F-14).

## Smoke check (Tier 2, real model)

`scripts/smoke.py` against the running app (`scripts/dev-up.sh --fresh`), model
`openai/gpt-oss-120b` on Groq through the harness's Pydantic AI client, 9 Oct 2026.
Each flow is a fresh conversation; tokens are from the app's `/dev/usage`.

| Flow | Run 1 | Run 3 (after F-17, F-18, F-21) | Turns | Model calls | Tokens in / out |
|---|---|---|---|---|---|
| A — report a collision → CLM- reference from the claims system | **fail**: CLM-019002 registered, reply replaced by the safe reply (F-17, F-18) | pass: "registered under reference CLM-019002", status then answered as registered | 2 | 3 (list_policies, register_claim, reply) | 2,869 / 255 |
| B — payout of CLM-010004 (₹25,001) waits; Asha approves on the desk; paid | pass | pass: 202 and "sent to a claims handler"; one row on the desk; decided `done`; status paid | 3 | 1 (request_payout) | 661 / 52 |
| C — payout of CLM-010003 (₹25,000) paid at once | **fail** in words: paid, reply replaced by the safe reply (F-21) | pass: paid, nobody asked; reply said "£25,000" (F-25) | 2 | 3 (get_claim, request_payout, reply) | 2,375 / 186 |
| D — claim status with no model call | pass | pass | 1 | 0 | 0 / 0 |

Run 3 in all: 7 model calls, 5,905 input and 493 output tokens, no rate limit, no
provider error, each call under a second. Run 2 repeated run 1 with a turn log
added to find the rules. gpt-oss-120b chose the right tools every time
(it reads policies before registering and reads the claim before paying) and
never called `issue_payout` itself. What failed was ours: three readers of its
replies (F-17, F-18, F-21).

