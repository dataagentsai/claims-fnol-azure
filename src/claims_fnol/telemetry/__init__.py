"""This agent's names in the harness's telemetry (L11).

The tracer, the span contract, redaction and the meters are the harness's
(`agent_harness.telemetry`). What is this agent's is the instrumentation scope
and service name every span and number carries, and the spans its own modules
open — declared in `spans`, at import of the package root.
"""

from claims_fnol.telemetry.spans import SCOPE, SERVICE, SPANS

__all__ = ["SCOPE", "SERVICE", "SPANS"]
