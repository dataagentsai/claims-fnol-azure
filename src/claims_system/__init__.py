"""The motor insurer's claims system: policies and claims on PostgreSQL, served over MCP.

A separate package from `claims_fnol` on purpose, and the import contracts keep
it so: this is what the insurer's own system runs, and the agent must not be
able to see or shortcut its checks. It reuses only the harness's contracts (the
approval record it reads), as a library.

    python -m claims_system migrate     # apply migrations/*.sql
    python -m claims_system seed        # the FNOL world's rows (add --fresh to drop demo claims)
    python -m claims_system serve       # MCP over streamable HTTP on :9050/mcp
"""
