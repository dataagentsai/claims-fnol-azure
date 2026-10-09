"""The composition root for running the FNOL agent as an app (Tier 2).

Outside `claims_fnol` on purpose: it is the one place that knows every concrete
adapter — the model provider's client, the DBOS waits, the claims system's URL,
the sign-in — and the import contracts forbid the agent package from knowing any
of them. It selects them by `CLAIMS_FNOL_ENV` (`local`, `test`, `azure`).
"""
