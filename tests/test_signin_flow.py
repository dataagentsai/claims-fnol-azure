"""A2 — a policyholder signs in with Entra: the code flow, PKCE and a nonce, at this app's edge.

The app exactly as the Azure overlay composes its identity port
(`config/azure.yaml`, `entra-id` with `login_redirect_uri`), in process, with a
fake Entra: tokens signed with a local RSA key, its public half the `keys` hook
(the fake JWKS), and `/token` answered through `httpx.MockTransport` (the
`transport` hook). The fake checks the PKCE verifier against the challenge the
authorize URL carried and issues each user's tokens as Entra would: `uti`, `oid`,
`scp` as a string, the app role in `roles`, and the policyholder id under the
`extn.` name the overlay's `holder_claim` reads. No network, no tenant.

Every row starts at `GET /signin` and follows the browser: to the fake's
authorize step, back to `/signin/callback`, and on to wherever it lands; then
the row's own steps.
"""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import jwt
import pytest
from agent_harness import adapters
from agent_harness import identity as ident
from agent_harness.adapters import Wiring
from agent_harness.adapters.waits import Waits
from agent_harness.state import InMemorySessionStore
from test_identity_edge import ENTRA_KEY, REFERENCES, TENANT, _Agent, _EmptyQueue, _resolved

from claims_fnol_app import edge, signin_flow
from claims_fnol_app.compose import overlay
from claims_fnol_app.usage import Counted

APP_ID = REFERENCES["ENTRA_APP_ID"]
HOLDER = REFERENCES["HOLDER_CLAIM"]
ISS = f"https://login.microsoftonline.com/{TENANT}/v2.0"
BASE = "https://agent.test"


@dataclass(frozen=True)
class User:
    oid: str
    holder: str | None = None
    roles: tuple[str, ...] = ()


USERS = {
    "rohan": User("oid-rohan", holder="PH-1001"),
    "meera": User("oid-meera", holder="PH-1002"),
    "asha": User("oid-asha", roles=("desk.handler",)),
    "stranger": User("oid-stranger"),
}
"""The hook's three test users as Entra issues them, and one tenant user the
hook never linked to a policy."""


def _signed(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, ENTRA_KEY._key, algorithm="RS256", headers={"kid": ENTRA_KEY.kid})


def access_token(user: User, *, ttl_s: int = 3600, **over: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISS,
        "aud": APP_ID,
        "sub": f"sub-{user.oid}",
        "oid": user.oid,
        "uti": uuid.uuid4().hex,
        "iat": now - 5,
        "exp": now + ttl_s,
        "scp": "claims:read claims:write",
    }
    if user.holder:
        claims[HOLDER] = user.holder
    if user.roles:
        claims["roles"] = list(user.roles)
    return _signed({**claims, **over})


class FakeEntra:
    """The tenant's authorize and token endpoints, for one test."""

    def __init__(self, tamper: str = "") -> None:
        self.tamper = tamper
        self.codes: dict[str, tuple[User, str, str]] = {}
        self.refreshes: dict[str, User] = {}

    def authorize(self, url: str, user: User) -> str:
        """The user signs in at Entra; the browser is sent back with a code."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert query["client_id"] == APP_ID and query["code_challenge_method"] == "S256"
        code = uuid.uuid4().hex
        self.codes[code] = (user, query["code_challenge"], query["nonce"])
        return f"{query['redirect_uri']}?code={code}&state={query['state']}"

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("client_secret") != REFERENCES["agent-obo-client-secret"]:
            return httpx2.Response(401, json={"error": "invalid_client"})
        if form["grant_type"] == "refresh_token":
            user = self.refreshes.pop(form["refresh_token"], None)
            if user is None:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            return httpx2.Response(200, json=self._tokens(user, nonce=None))
        user, challenge, nonce = self.codes.pop(form["code"], (None, "", ""))
        digest = hashlib.sha256(form["code_verifier"].encode()).digest()
        if user is None or base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != challenge:
            return httpx2.Response(400, json={"error": "invalid_grant"})
        return httpx2.Response(200, json=self._tokens(user, nonce=nonce))

    def _tokens(self, user: User, *, nonce: str | None) -> dict[str, str]:
        aud = "another-app" if self.tamper == "audience" else APP_ID
        refresh = f"rt-{uuid.uuid4().hex}"
        self.refreshes[refresh] = user
        body = {"access_token": access_token(user, aud=aud), "refresh_token": refresh}
        if nonce is not None:
            now = int(time.time())
            said = "a-replayed-nonce" if self.tamper == "nonce" else nonce
            body["id_token"] = _signed(
                {"iss": ISS, "aud": APP_ID, "sub": f"sub-{user.oid}", "oid": user.oid}
                | {"nonce": said, "iat": now, "exp": now + 3600}
            )
        return body


async def azure_app(fake: FakeEntra) -> tuple[Any, InMemorySessionStore]:
    """The edge on the identity port exactly as `config/azure.yaml` binds it."""
    bound = adapters.plan(overlay("azure")).bound["identity"]
    settings = {k: _resolved(v) for k, v in bound.settings.items()}
    hooks = {"keys": ident.JWKS(ENTRA_KEY.jwks), "transport": httpx2.MockTransport(fake.handle)}
    held = bound.adapter.build(Wiring(settings, {}, hooks, "azure"))
    sessions = await held.__aenter__()
    queue = _EmptyQueue()
    waits = Waits(approvals=queue, escalations=queue, approver=None, desk=None)  # type: ignore[arg-type]
    stored = InMemorySessionStore()
    app = edge.build(
        _Agent(),  # type: ignore[arg-type]
        waits,
        sessions,
        counted=Counted(inner=None),  # type: ignore[arg-type]
        usage_route=False,
        stored=stored,
    )
    return app, stored


Step = tuple[str, str, int]
"""(method, path, the status), with the session the page holds; two verbs are
the browser's own: EXPIRE (its token runs out) and the `/signin/*` POSTs."""

ROWS: list[tuple[str, str, str, int, str, list[Step]]] = [
    # (row, who signs in, what is tampered with, the callback's status, where it
    #  lands or what it says, then the row's steps)
    (
        "a good code exchange creates the session, the policyholder id from the claim",
        "rohan",
        "",
        303,
        "/",
        [("GET", "/opening", 200)],
    ),
    ("a state that is not this browser's is refused", "rohan", "state", 400, "start again", []),
    (
        "a code minted for another PKCE challenge is refused",
        "rohan",
        "pkce",
        400,
        "start again",
        [],
    ),
    ("an id token whose nonce is not this flow's is refused", "rohan", "nonce", 400, "start", []),
    ("a token for another audience is refused", "rohan", "audience", 400, "start again", []),
    ("a login with no policyholder id is told why", "stranger", "", 403, "not linked", []),
    (
        "a handler reaches the desk and cannot chat as a policyholder",
        "asha",
        "",
        303,
        "/ops/desk",
        [("GET", "/ops/approvals", 200), ("GET", "/opening", 403), ("POST", "/chat", 403)],
    ),
    (
        "a policyholder has no desk",
        "meera",
        "",
        303,
        "/",
        [("GET", "/ops/approvals", 403), ("GET", "/ops/escalations", 403)],
    ),
    (
        "logging out deletes the session, and a refresh is refused",
        "rohan",
        "",
        303,
        "/",
        [("POST", "/signin/logout", 303), ("POST", "/signin/refresh", 401)],
    ),
    (
        "an expired session is refreshed from the stored refresh token",
        "rohan",
        "",
        303,
        "/",
        [
            ("EXPIRE", "", 0),
            ("GET", "/opening", 401),
            ("POST", "/signin/refresh", 200),
            ("GET", "/opening", 200),
        ],
    ),
]


@pytest.mark.discharges("AAC-0057", "AHC-0099")
@pytest.mark.parametrize(
    ("row", "login", "tamper", "status", "outcome", "steps"), ROWS, ids=[r[0] for r in ROWS]
)
async def test_a_sign_in_at_the_edge(
    row: str, login: str, tamper: str, status: int, outcome: str, steps: list[Step]
) -> None:
    fake = FakeEntra(tamper)
    app, stored = await azure_app(fake)
    user = USERS[login]
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as web:
        started = await web.get("/signin")
        assert (
            started.status_code == 303 and "/oauth2/v2.0/authorize?" in started.headers["location"]
        )
        back = fake.authorize(started.headers["location"], user)
        if tamper == "state":
            back = back.split("&state=")[0] + "&state=someone-elses"
        if tamper == "pkce":  # another browser's flow; this browser's cookie and state
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url=BASE
            ) as elsewhere:
                other = await elsewhere.get("/signin")
            code = fake.authorize(other.headers["location"], user).split("code=")[1].split("&")[0]
            back = back.replace(back.split("code=")[1].split("&")[0], code)
        answered = await web.get(back.replace(BASE, ""))
        assert answered.status_code == status, (row, answered.text)
        subject = f"sub-{user.oid}"
        if status != 303:
            assert outcome in answered.text.lower(), answered.text
            assert await stored.get(subject) is None, "a refused sign-in stores nothing"
            assert "access_token" not in answered.text and "eyJ" not in answered.text
            return
        where, _, fragment = answered.headers["location"].partition("#")
        assert where == outcome
        token = parse_qs(fragment)["token"][0]
        assert "token=" not in where, "the session is never in a query a server logs"
        kept = await stored.get(subject)
        assert kept is not None and kept.refresh_token not in answered.headers["set-cookie"]
        for method, path, expected in steps:
            if method == "EXPIRE":
                token = access_token(user, ttl_s=-60)
                continue
            body = {"text": "hello", "conversation_id": None} if path == "/chat" else None
            headers = {} if path.startswith("/signin") else {"authorization": f"Bearer {token}"}
            got = await web.request(method, path, headers=headers, json=body)
            assert got.status_code == expected, (row, method, path, got.text)
            if path == "/signin/refresh" and expected == 200:
                token = got.json()["token"]  # verified at the next step
            if path == "/signin/logout":
                assert got.headers["location"].startswith(
                    f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/logout?"
                )
                assert await stored.get(subject) is None, "logged out: the session is gone"


@pytest.mark.discharges("AAC-0057")
def test_the_message_for_an_unlinked_login_is_plain() -> None:
    assert "not linked to a policy" in signin_flow.NOT_LINKED
    assert "claim" not in signin_flow.NOT_LINKED.lower(), "no token words for a policyholder"
