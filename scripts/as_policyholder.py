"""Talk to the deployed agent as a test user, from the command line.

The chat page needs a person at a browser; this needs one only once. Each test
user signs in once with a device code (Microsoft Authenticator, because the
tenant's Security Defaults require MFA for everyone); the refresh token is kept
in `.state/tokens/<user>.json` (gitignored, mode 600) and renewed on every use, so
later requests need nobody. It signs in through a separate public client,
`claims-fnol-test-client`, consented for the agent's two scopes only — the
agent's own app registration is not loosened. Delete that app to cut it off.

    # once: shows a code to enter at microsoft.com/devicelogin
    python scripts/as_policyholder.py login rohan
    # simplest: the test user's password from Key Vault (test-user-<user>-password);
    # no browser at all
    python scripts/as_policyholder.py signin rohan
    # or: sign in on the chat page as Rohan, then take over that sign-in
    # (the agent's stored, encrypted refresh token)
    python scripts/as_policyholder.py adopt rohan
    python scripts/as_policyholder.py opening rohan [--new]
    python scripts/as_policyholder.py chat rohan "What's the status of my claim?" \
        [--continue | --conversation cnv_…] [--delivery ID]
    # a handler: waiting payouts and hand-offs
    python scripts/as_policyholder.py desk me
    python scripts/as_policyholder.py decide me apr_… --grant | --refuse
    python scripts/as_policyholder.py resolve me E-… resolved|agent_could_have|misrouted

Every request goes the whole way: Entra, the agent on Container Apps, APIM,
Content Safety, Groq, MCP, PostgreSQL — so each turn is in App Insights.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

TENANT = os.environ.get("AZURE_TENANT_ID", "9104b5a1-6043-41e9-ad88-7f3864306dc6")
CLIENT_ID = os.environ.get("TEST_CLIENT_ID", "cc51adae-09b9-4a4d-91d8-6e9985bd4465")
AGENT_URL = os.environ.get(
    "AGENT_URL", "https://ca-fnol-agent.agreeableriver-056a26f2.centralindia.azurecontainerapps.io"
)
SCOPES = (
    "api://claims-fnol/claims:read api://claims-fnol/claims:write offline_access openid profile"
)
AUTHORITY = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
AGENT_APP_ID = os.environ.get("ENTRA_APP_ID", "70d25155-e040-46e5-9d80-519de6a64924")
VAULT = os.environ.get("AZURE_KEY_VAULT_NAME", "kv-fnol-732oeqxbptfjc")
PG_HOST = os.environ.get("AZURE_POSTGRES_HOST", "pg-fnol-732oeqxbptfjc.postgres.database.azure.com")
UPN = "{user}@basantchoudhary09gmail.onmicrosoft.com"
TOKENS = Path(__file__).resolve().parents[1] / ".state" / "tokens"


def _post_form(url: str, fields: dict[str, str]) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def _saved(user: str) -> Path:
    return TOKENS / f"{user}.json"


def _store(user: str, record: dict) -> None:
    TOKENS.mkdir(parents=True, exist_ok=True)
    path = _saved(user)
    path.write_text(json.dumps(record))
    path.chmod(0o600)


def login(user: str) -> None:
    started = _post_form(f"{AUTHORITY}/devicecode", {"client_id": CLIENT_ID, "scope": SCOPES})
    if "device_code" not in started:
        sys.exit(f"could not start sign-in: {started.get('error_description', started)}")
    print(started["message"], flush=True)
    print(
        f"(sign in as the test user '{user}', not as yourself — unless '{user}' is you)", flush=True
    )
    deadline = time.time() + int(started["expires_in"])
    while time.time() < deadline:
        time.sleep(int(started.get("interval", 5)))
        got = _post_form(
            f"{AUTHORITY}/token",
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": started["device_code"],
            },
        )
        if "refresh_token" in got:
            _store(user, {"refresh_token": got["refresh_token"], "conversation_id": None})
            claims = _claims(got["access_token"])
            print(
                json.dumps(
                    {
                        "signed_in": claims.get("preferred_username"),
                        "policyholder": claims.get("extn.customer_id"),
                        "roles": claims.get("roles"),
                    },
                    indent=1,
                )
            )
            return
        if got.get("error") not in ("authorization_pending", "slow_down"):
            sys.exit(f"sign-in failed: {got.get('error_description', got)}")
    sys.exit("sign-in timed out")


def _secret(name: str) -> str:
    import subprocess

    out = subprocess.run(
        [
            "az",
            "keyvault",
            "secret",
            "show",
            "--vault-name",
            VAULT,
            "-n",
            name,
            "--query",
            "value",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _agent_refresh(refresh_token: str) -> dict:
    """The refresh grant exactly as the chat page's /signin/refresh does it: the
    agent's own (confidential) app, its client secret from Key Vault."""
    return _post_form(
        f"{AUTHORITY}/token",
        {
            "grant_type": "refresh_token",
            "client_id": AGENT_APP_ID,
            "client_secret": _secret("agent-obo-client-secret"),
            "refresh_token": refresh_token,
            "scope": SCOPES,
        },
    )


def signin(user: str) -> None:
    """The resource-owner password grant, for TEST users only, with the password
    Key Vault holds for them. Works while the tenant does not demand MFA on this
    sign-in (it did not, on 14 Oct); if it starts to, use `login` (device code)."""
    got = _post_form(
        f"{AUTHORITY}/token",
        {
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "scope": SCOPES,
            "username": UPN.format(user=user),
            "password": _secret(f"test-user-{user}-password"),
        },
    )
    if "refresh_token" not in got:
        sys.exit(f"sign-in failed: {got.get('error_description', got)}")
    _store(user, {"refresh_token": got["refresh_token"], "conversation_id": None})
    claims = _claims(got["access_token"])
    print(
        json.dumps(
            {
                "signed_in": claims.get("preferred_username"),
                "policyholder": claims.get("extn.customer_id"),
                "roles": claims.get("roles"),
            },
            indent=1,
        )
    )


def adopt(user: str) -> None:
    """Take over a sign-in done on the chat page: decrypt the agent's stored
    refresh tokens (agent_state.sessions, Fernet with a key derived from Key
    Vault's agent-session-key, as agent_harness.adapters.sealing_key does) and keep
    the one that belongs to this user. Needs the repo's .venv (cryptography)."""
    import base64
    import hashlib
    import subprocess

    from cryptography.fernet import Fernet, InvalidToken

    key = base64.urlsafe_b64encode(
        hashlib.sha256(f"state.sessions\0{_secret('agent-session-key')}".encode()).digest()
    )
    rows = subprocess.run(
        [
            "psql",
            f"host={PG_HOST} user=claimsadmin dbname=claims_agent sslmode=require",
            "-X",
            "-tA",
            "-F",
            "|",
            "-c",
            "SELECT subject, encode(refresh_token, 'base64') FROM agent_state.sessions"
            " ORDER BY updated_at DESC",
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PGPASSWORD": _secret("postgres-admin-password")},
    ).stdout.split("\n")
    want = UPN.format(user=user).lower()
    for row in filter(None, rows):
        subject, sealed = row.split("|", 1)
        try:
            refresh = Fernet(key).decrypt(base64.b64decode(sealed.replace("\n", ""))).decode()
        except InvalidToken:
            continue
        got = _agent_refresh(refresh)
        if "access_token" not in got:
            continue
        claims = _claims(got["access_token"])
        if str(claims.get("preferred_username", "")).lower() == want:
            _store(
                user,
                {
                    "refresh_token": got.get("refresh_token", refresh),
                    "client": "agent",
                    "conversation_id": None,
                },
            )
            print(
                json.dumps(
                    {
                        "adopted": claims.get("preferred_username"),
                        "policyholder": claims.get("extn.customer_id"),
                        "roles": claims.get("roles"),
                    },
                    indent=1,
                )
            )
            return
    sys.exit(
        f"no stored sign-in for {want}: sign in on the chat page as that user first,"
        " then run adopt again"
    )


def _claims(token: str) -> dict:
    import base64

    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def _access(user: str) -> tuple[str, dict]:
    path = _saved(user)
    if not path.exists():
        sys.exit(f"'{user}' has not signed in: python scripts/as_policyholder.py login {user}")
    record = json.loads(path.read_text())
    if record.get("client") == "agent":
        got = _agent_refresh(record["refresh_token"])
    else:
        got = _post_form(
            f"{AUTHORITY}/token",
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": record["refresh_token"],
                "scope": SCOPES,
            },
        )
    if "access_token" not in got:
        sys.exit(f"token refresh failed ({got.get('error')}): sign in again with: login {user}")
    record["refresh_token"] = got.get("refresh_token", record["refresh_token"])
    _store(user, record)
    return got["access_token"], record


def _call(
    method: str, path: str, token: str, body: dict | None = None, headers: dict | None = None
) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{AGENT_URL}{path}",
        data=data,
        method=method,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            **(headers or {}),
        },
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            status, payload = r.status, json.load(r)
    except urllib.error.HTTPError as e:
        status, payload = e.code, json.loads(e.read() or b"{}")
    if not isinstance(payload, dict):
        payload = {"items": payload}
    return {"http": status, "ms": int((time.time() - started) * 1000), **payload}


def _policyholder_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    o = sub.add_parser("opening")
    o.add_argument("user")
    o.add_argument("--new", action="store_true")
    c = sub.add_parser("chat")
    c.add_argument("user")
    c.add_argument("text")
    c.add_argument(
        "--continue",
        dest="cont",
        action="store_true",
        help="continue this user's last conversation",
    )
    c.add_argument("--conversation")
    c.add_argument("--delivery", help="reuse a delivery id (duplicate test)")


def _handler_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sub.add_parser("desk").add_argument("user")
    dc = sub.add_parser("decide")
    dc.add_argument("user")
    dc.add_argument("approval_id")
    g = dc.add_mutually_exclusive_group(required=True)
    g.add_argument("--grant", action="store_true")
    g.add_argument("--refuse", action="store_true")
    r = sub.add_parser("resolve")
    r.add_argument("user")
    r.add_argument("escalation_id")
    r.add_argument("outcome")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="command", required=True)
    for name in SIGN_IN:
        sub.add_parser(name).add_argument("user")
    _policyholder_commands(sub)
    _handler_commands(sub)
    return p


def _chat(a: argparse.Namespace, token: str, record: dict) -> dict:
    conversation = a.conversation or (record.get("conversation_id") if a.cont else None)
    delivery = a.delivery or str(uuid.uuid4())
    out = _call(
        "POST",
        "/chat",
        token,
        {"text": a.text, "conversation_id": conversation},
        {"idempotency-key": delivery},
    )
    out["delivery"] = delivery
    if out.get("conversation_id"):
        record["conversation_id"] = out["conversation_id"]
        _store(a.user, record)
    return out


def _request(a: argparse.Namespace, token: str, record: dict) -> dict:
    if a.command == "opening":
        return _call("GET", "/opening?new=1" if a.new else "/opening", token)
    if a.command == "chat":
        return _chat(a, token, record)
    if a.command == "desk":
        return {
            "approvals": _call("GET", "/ops/approvals", token),
            "escalations": _call("GET", "/ops/escalations", token),
        }
    if a.command == "decide":
        return _call(
            "POST", f"/ops/approvals/{a.approval_id}/decide", token, {"granted": bool(a.grant)}
        )
    return _call(
        "POST", f"/ops/escalations/{a.escalation_id}/resolve", token, {"outcome": a.outcome}
    )


SIGN_IN = {"login": login, "adopt": adopt, "signin": signin}


def main() -> None:
    a = _parser().parse_args()
    if a.command in SIGN_IN:
        return SIGN_IN[a.command](a.user)
    token, record = _access(a.user)
    print(json.dumps(_request(a, token, record), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
