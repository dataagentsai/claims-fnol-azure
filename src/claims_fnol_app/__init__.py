"""The composition root for running the FNOL agent as an app (Tier 2).

Outside `claims_fnol` on purpose: it is the one place that wires the agent to
adapters, and the import contracts forbid the agent package from knowing any of
them. It does not choose them either: `config/<environment>.yaml` names each
port's adapter and `agent_harness.adapters` builds it (Tier 2b);
`CLAIMS_FNOL_ENV` (`local`, `test`, `azure`) only names which overlay to read.
"""
