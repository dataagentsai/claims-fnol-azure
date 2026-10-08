"""The motor-claims FNOL agent: this insurer's domain, composed on `agent_harness`.

Importing any part of it first declares the domain to the harness — its
intents, its reference shapes and status grammar, the rules every policy
position runs by default, and its telemetry names — so no route, rule or check
can run before they are known, whichever module a caller imports first.
"""

from claims_fnol import contracts as contracts
from claims_fnol import policy as policy
from claims_fnol.telemetry import spans as spans
