# Design audit: the series pages against the code

10 Oct 2026. All 173 pages of `~/Agent-Harness-Series.html` were checked against the code. Four read-only reviewers each took a band of pages. "The code" means three things:

- the clothing agent and the `agent_harness` library (`~/reference-agent`);
- this FNOL agent, with its claims system, overlays and Bicep;
- the live `claims_fnol_dbos` database.

The owner asked for the check: "check the whole html, if anything you have not followed".

**Verdict.**
- Most "Built" claims hold, with paths, names and constants matching the code. Two examples:
  - flow-journey-2: 16 of 16 Built steps.
  - The code pages: every `agent_harness` path cited exists.
- The gaps fall into four groups:
  - **A.** Ten things that must be fixed before the Azure end-to-end run is safe.
  - **B.** The evaluation loop is only half wired.
  - **C.** Many design items no plan tier owned.
  - **D.** About 25 stale statements on the pages themselves.

## A. Fix before the Azure end-to-end run (Phase 1)

| # | Design says | Code today | Why it matters |
|---|---|---|---|
| A1 | The claims system checks the caller's token on every call (on-behalf-of exchange, far-end verification) | The claims server trusts the session the agent asserts (`authorise=asserted`, `server.py:182`). The `entra-id` overlay builds no exchange (no client id or secret). MCP goes straight to internal ingress. | In Azure, anything that can reach the claims server can act as any policyholder (F-23). |
| A2 | Policyholders sign in through Entra (code flow + PKCE); a `sessions` table holds refresh tokens | Only the local `/signin` exists. Entra issues no `customer_id` claim. FNOL has no sessions table. | A policyholder can't use it in Azure (F-43). |
| A3 | Our own `approvals` and `escalations` tables. Id = DBOS workflow id, `args_digest`, states pending → approved, rejected, expired or cancelled. | The record is a DBOS workflow event. The claims system reads it from DBOS's internal tables (`get_event`, `server.py:292-303`). | It ties the money path to DBOS's storage format, which breaks code-to-interface. Swapping DBOS breaks the payout check. |
| A4 | A database role per job; the claims system can only read approvals | Both apps use the server admin login. The claims system gets the agent's database URL and could write the DBOS tables. | Least privilege on the money path. |
| A5 | A crash resumes from the last step; a tool is never called twice | One checkpoint per turn. A retry after the delivery claim expires gets a new `run_id`, so a new idempotency key: `register_claim` can run twice. | Duplicate claims after a crash. |
| A6 | Thresholds and flags come from App Configuration | It's provisioned, but nothing reads it (no config port, F-35). ₹25,000 is hard-coded in the agent **and** the claims server. | The Tier 5 exercise "change the limit without a redeploy" can't work. |
| A7 | Revisions give canary releases | `activeRevisionsMode: 'Single'` | The Tier 5 canary exercise can't work. |
| A8 | The gateway checks the agent may use the model it asked for | No allow-list in the APIM policy; only the agent checks | Planned for Tiers 3–4, not written |
| A9 | Content Safety results are recorded as check results | `guardrail_log` is a stub that raises `NotBuilt` | Tier 4's done-when depends on it |
| A10 | Gates run in CI before every deploy | No `.github/workflows` anywhere; the gates run by hand | Already Tier 4; listed so it isn't lost |

**Also small, and in Phase 1 because they're security basics** (`PRE_MODEL` and `POST_TOOL` are empty in both agents):

| # | Item |
|---|---|
| A11 | A pre-model injection rule, and a post-tool hidden-instruction rule |
| A12 | Aadhaar and PAN patterns in redaction and `no_pii_echo` (today Aadhaar is caught only by accident, as an "account" number; PAN never) |
| A13 | A kill switch per agent. The deck lists it as Built under runaway cost; it isn't found in either repo. |

## B. The evaluation loop (Tier 4b, new, in Phase 1)

| # | Design says | Code today |
|---|---|---|
| B1 | One rule library, run at three speeds | Two libraries: the inline rules (`policy`) and 24 watch rules (`watch/rules.py`) |
| B2 | `evaluators.yaml`'s `online` and `release` positions run | Their runners exist, but only a test calls them |
| B3 | Online rule → finding → alert → dashboard on Azure | None for FNOL: no online job, no findings table, no alerts, no dashboard, no canary job |

## C. Design items no tier owned: now placed

| Item (page) | Tier |
|---|---|
| Retention and erasure wired for FNOL, including DBOS records and the claims database (c-PV, c-L5-stateful) | 11 |
| PII masking before the model (AI Language PII, c-PV-flow) | 11 |
| Row-level security; `agent_id`, `tenant`, `retain_until` columns; `conversations` table (c-L5) | 11 |
| Saga / compensation table actually used (sw-patterns, F-12) | 11 |
| APIM backend pool and circuit breaker; token metric dimensions agent/model/tier; MCP behind APIM (c-L2-gateway, journey 39, 43, 49) | 10 |
| Model in an India region (Foundry), processing region recorded, AHC-0092 (c-PV, az-one) | 10 |
| Escalation states: assigned, returned to agent, re-route on SLA (c-L14-hitl) | 10 |
| Separate approval-workflow identity with `payouts.issue`; Teams card (c-L14, sec-auth) | 10 |
| Streaming replies; turn priority, deadline and one-turn lease (c-L8, journey 24–27) | 10 |
| Model contract v2; Anthropic adapter; ModelSelector (multi-model golden run); FallbackModel decision (c-L2-contract, c-L2-choose, c-L4-options) | 14 (new) |
| pydantic-evals; eval report; daily cost engine; production turns → golden cases; shadow mode (c-L12-offline, journey 72/77, test-golden) | 12 |
| Environments (test, UAT, prod); zone redundancy, HA, geo backups, private endpoints, paired region; PIM, Defender for AI; load testing and failure drills; alerts on unusual access; Blob storage for long captured text (c-AV, sec-auth, test-drills, c-PV) | 13 (new) |
| Pre-commit, pytest-cov ratchet, vcrpy, consumer contract tests for MCP, PyRIT / promptfoo / garak, change-based test selection (test-*, sw-lego) | 12 |
| A2A hand-off ticket, API Center registry, Communication Services (c-17, az-services) | Later (not needed for one agent) |
| `no_invented_date`, `no_unauthorised_offer` (c-L7); malformed output repair attempt (c-L10-flow) | 11 |

## D. Stale or wrong statements on the pages (for the series' owner)

| Page | Says | Should say |
|---|---|---|
| code-map, code-run, code-llm, flow-code | Model: APIM → Foundry | APIM → Groq in dev; Foundry later (Tier 10) |
| code-map, code-evals | AzureEvaluator on the same port | A stub; To build (Tier 12) |
| code-evals | Reply evaluators run at REPLY | POST_MODEL for the model's reply; REPLY on every route for reply-only checks |
| c-L12-plugins | Status: To build | Built (Tier 2b); only the azure, presidio, guardrail_log and open_model kinds are stubs |
| c-L12-online | 20 alerts | 22 alerts + 7 recordings |
| c-L12-rules | 21 online rules | 24 (21 turn + 3 conversation) |
| c-L12-healing vs c-L12-online | Scores in App Insights vs PostgreSQL | PostgreSQL is the store of record |
| c-L5-postgres, perf-once, flow-journey-2/3 | DBOS approvals "To build" | Built (Tier 2) |
| flow-journey 4, 16 | Status card and rules-first routing "Proposed" | Built in FNOL |
| c-L5-pgdesign | `checkpoints(conversation_id, version…)`; `requests(scope_key…)` | `checkpoints(run_id…)` upserted per run; `requests(name, scope, state, outcome, expires_at…)` |
| c-L14-durable, c-L14-hitl | States pending → approved…; `args_digest`; DBOS only above ₹25,000 | assessing, waiting, carrying_out, done, failed, refused, expired, stale; full args; every payout is a workflow, auto-granted at or under the limit. A3 moves the code toward the deck. |
| c-L10-flow | 7 failure kinds; a repair attempt | 5 kinds (unreachable, refused, malformed, misconfigured, exhausted); no repair |
| c-L10-idempotency | `answered(policyholder, key, operation)` | Key is `(policyholder_id, key)` |
| c-L2-contract | Claude mapping ✓ | No Anthropic adapter |
| c-L4-options | Fallback model, Anthropic provider, instrumentation, pydantic-evals: "Use" | None used yet; instrumentation is deliberately off |
| sec-owasp LLM01 | Built | Partly (no pre-model rule; Prompt Shields not deployed) |
| res-risks | Kill switch Built; silent-agent alert Built | Not found; only CanarySilent |
| sw-ports | PydanticAIClient, EntraOnBehalfOf, DBOSApprovals are "proposed names"; the profile isn't read at runtime | All exist; overlays are read and checked against the profile at runtime |
| sw-lego | import-linter: 1 layer rule, 7 walls | 16 contracts (2 layers, 14 forbidden) |
| how-ttm | `Evaluator.check(req)` | `evaluate`; a new rule also needs a `RULES` entry |
| test-pyramid, test-gates | 1,611 tests in 89 files | 1,890 tests in 94 files; FNOL has 463, 29/29 behaviour |
| work-specs | AOAS 60/67, baseline 1 failing, 545/672 | 61/67, 0 failing, 547/674 |
| work-ways | Rebuilt four times | Five generation runs |
| test-redteam | 4 scenarios × 20 cases | 2 × 20 generated, plus hand-written ones |
| test-coverage | `(500_000, True) # at the limit` | At the limit there is no wait: 25,000 → no, 25,001 → yes (`test_payout_limit.py`) |
| test-drills | All To build | Three exist as tests (repeat delivery, webhook redelivery, approve twice) |
| test-twin | Adopted tools (LangWatch, DeepEval, Testcontainers…) | Only hypothesis + pytest; the rest are To adopt |
| trace-escalation | Tier 2 triggers; from a smoke run | Add `declined`; it comes from a scenario, not the smoke run |
| deploy-glance | Tiers 3–4 "in the reference agent" | In the claims agent; Tier 3 written, not deployed |
| az-one, az-families | Images in ACR; token limit per agent | GHCR; Consumption has only a request rate (F-39) |
| cost-bill | gpt-5-mini, Cosmos DB | Production estimate; dev is Groq + PostgreSQL |
| c-L7-policies | `AGENT_*` overrides fed from App Configuration | FNOL uses `CLAIMS_*`; App Configuration not read (A6) |

Correct, for balance:
- context assembly (9 of 9);
- the journey's Built steps (34 of 40);
- the evaluator plug-ins (8 of 8);
- the inline "ours" checks (14 verified);
- cost budgets and Meter;
- the idempotency keys;
- the code pages' paths and constants;
- the assurance-catalog counts.
