"""The app end to end, over HTTP, with a scripted model (`config/test.yaml`).

Tier 2's done-when, without a network or a key: a signed-in policyholder reports
a collision and gets a claim reference from the claims system; a payout above
₹25,000 waits until a claims handler approves it on the desk, and is then paid;
a payout at the limit is paid at once; claim status is answered with no model
call. Everything real except the model: the claims system's MCP server on its
own throwaway database, the DBOS waits, the agent's state and its own approval
records on another, which the claims system reads read-only (A3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
import psycopg
import pytest
from agent_harness import adapters
from agent_harness import identity as ident
from agent_harness.identity.local import LocalIssuer
from pg import throwaway

from claims_fnol.contracts import ModelResponse, ToolCall, Usage
from claims_fnol_app import signin
from claims_fnol_app.compose import compose, hooks, overlay
from claims_system import server as srv
from claims_system import store as st
from claims_system.__main__ import approval_records, authorisation, world_records
from claims_system.__main__ import overlay as claims_overlay


def says(text: str = "", *calls: tuple[str, dict[str, object]]) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=tuple(
            ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)
        ),
        usage=Usage(input_tokens=10, output_tokens=5),
    )


@dataclass
class App:
    http: httpx2.AsyncClient
    claims_url: str
    issuer: LocalIssuer

    def token(self, login: str) -> str:
        return signin.mint(self.issuer, {u.login: u for u in signin.USERS}[login])

    async def say(self, login: str, text: str, conversation: str | None = None) -> Any:
        sent = {"text": text, "conversation_id": conversation}
        auth = {"authorization": f"Bearer {self.token(login)}"}
        return await self.http.post("/chat", json=sent, headers=auth)

    async def desk(self, method: str, path: str, **kwargs: Any) -> Any:
        auth = {"authorization": f"Bearer {self.token('asha')}"}
        return await self.http.request(method, f"/ops{path}", headers=auth, **kwargs)

    async def status(self, claim: str) -> str:
        async with await psycopg.AsyncConnection.connect(self.claims_url) as conn:
            cur = await conn.execute("SELECT status FROM claim WHERE id = %s", (claim,))
            row = await cur.fetchone()
        assert row is not None
        return str(row[0])

    async def usage(self) -> dict[str, int]:
        found: dict[str, int] = (await self.http.get("/dev/usage")).json()
        return found


class AppKeys:
    """The app's local issuer's keys, for a claims system built before the app
    is: what `/.well-known/jwks.json` serves the claims system on this Mac."""

    keys: ident.JWKS | None = None

    def key_for(self, token: str) -> Any:
        if self.keys is None:
            raise ident.InvalidSession("the app has published no keys yet")
        return self.keys.key_for(token)


@asynccontextmanager
async def app(script: Iterable[ModelResponse]) -> AsyncIterator[App]:
    """The app and the claims system as on this Mac: every call carries a token
    the app's local issuer minted for the claims system, which verifies it with
    the app's keys (`config/claims-system/local.yaml`, A1)."""
    async with throwaway("claims_app") as claims_url, throwaway("claims_agent") as agent_url:
        await st.migrate(claims_url)
        await st.seed(claims_url, world_records())
        published = AppKeys()
        checked = adapters.plan(claims_overlay("local"))
        async with (
            st.Store.open(claims_url) as store,
            approval_records(agent_url) as records,
            authorisation(checked, hooks={"keys": published}) as authorise,
        ):
            server = srv.build(store, authorise=authorise, approvals=records)
            given = hooks(script=script, claims_server=server)
            with pytest.MonkeyPatch.context() as env:
                env.setenv("CLAIMS_DBOS_DATABASE_URL", agent_url)
                planned = adapters.plan(overlay("test"))
                async with compose(planned, given=given) as built:
                    published.keys = ident.JWKS(built.state.local_issuer.jwks)
                    transport = httpx2.ASGITransport(app=built)
                    async with httpx2.AsyncClient(transport=transport, base_url="http://app") as h:
                        yield App(h, claims_url, built.state.local_issuer)


async def test_a_reported_collision_gets_a_reference_from_the_claims_system() -> None:
    script = [
        says("", ("register_claim", {"id": "POL-010001", "incident_type": "collision"})),
        says("I've registered the collision on your policy. An assessor will review it next."),
    ]
    async with app(script) as a:
        said = await a.say(
            "rohan", "A bus hit my car KA-01-AB-1234 this morning. I want to make a claim."
        )
        body = said.json()
        made = await a.status("CLM-019002")
    assert said.status_code == 200, body
    assert "CLM-019002" in body["reply"], "the reference the claims system made reaches the reply"
    assert made == "registered"


PAYOUTS = [
    # (case, claim, waits for a handler)
    ("one rupee past the limit waits for a handler", "CLM-010004", True),
    ("on the limit is paid at once", "CLM-010003", False),
]


@pytest.mark.parametrize(("case", "claim", "waits"), PAYOUTS, ids=[p[0] for p in PAYOUTS])
async def test_a_payout_waits_for_a_handler_only_above_the_limit(
    case: str, claim: str, waits: bool
) -> None:
    script = [
        says("", ("request_payout", {"id": claim})),
        says(f"The payment for {claim} has been issued to the account on your policy."),
    ]
    async with app(script) as a:
        said = await a.say("rohan", f"Please release the payment for {claim}.")
        before = await a.status(claim)
        queue = (await a.desk("GET", "/approvals")).json()
        if waits:
            decided = await a.desk(
                "POST", f"/approvals/{queue[0]['id']}/decide", json={"granted": True}
            )
            assert decided.json()["state"] == "done", decided.json()
        after = await a.status(claim)
    assert said.status_code == (202 if waits else 200), case
    assert before == ("approved" if waits else "paid"), case
    assert len(queue) == (1 if waits else 0), case
    assert after == "paid", case


async def test_a_handler_cannot_be_the_policyholder_and_a_policyholder_cannot_decide() -> None:
    async with app([says("", ("request_payout", {"id": "CLM-010004"}))]) as a:
        await a.say("rohan", "Please release the payment for CLM-010004.")
        queue = (await a.desk("GET", "/approvals")).json()
        rohan = {"authorization": f"Bearer {a.token('rohan')}"}
        mine = await a.http.post(
            f"/ops/approvals/{queue[0]['id']}/decide", json={"granted": True}, headers=rohan
        )
        still = await a.status("CLM-010004")
    assert mine.status_code == 403 and still == "approved"


async def test_claim_status_is_answered_with_no_model_call() -> None:
    async with app([]) as a:  # an empty script raises if the model is asked anything
        said = await a.say("rohan", "What's the status of claim CLM-010007?")
        spent = await a.usage()
    assert said.status_code == 200 and "assess" in said.json()["reply"]
    assert spent["calls"] == 0


async def test_the_opening_shows_the_policyholders_own_claims_with_no_model_call() -> None:
    async with app([]) as a:
        shown = await a.http.get(
            "/opening", headers={"authorization": f"Bearer {a.token('rohan')}"}
        )
        stranger = await a.http.get(
            "/opening", headers={"authorization": f"Bearer {a.token('meera')}"}
        )
    assert "CLM-010004" in shown.json()["reply"] and "CLM-019001" not in shown.json()["reply"]
    assert "CLM-010004" not in stranger.json()["reply"]


SIGN_IN = [
    # (login, lands on)
    ("rohan", "/?token="),
    ("meera", "/?token="),
    ("asha", "/ops/desk?token="),
    ("mallory", None),
]


@pytest.mark.parametrize(("login", "lands"), SIGN_IN)
async def test_local_sign_in_sends_each_test_user_to_their_page(
    login: str, lands: str | None
) -> None:
    async with app([]) as a:
        went = await a.http.post("/signin", content=f"login={login}")
    if lands is None:
        assert went.status_code == 400
    else:
        assert went.status_code == 303 and went.headers["location"].startswith(lands)


# What gpt-oss-120b actually wrote in the first live run (FINDINGS F-17, F-18,
# F-21): a non-breaking hyphen in every reference, a narrow no-break space, a
# read of the claim before paying it. Each reply was correct, and each was
# replaced by "I am not able to confirm that".
LIVE = [
    # (case, the model's answers, message, status, the reply must hold)
    (
        "a registration reply naming the new claim with a non-breaking hyphen",
        [
            says("", ("list_policies", {})),
            says("", ("register_claim", {"id": "POL‑010001", "incident_type": "collision"})),
            says(
                "Your claim has been registered under reference **CLM‑019002**. An assessor "
                "will review the details of the incident and will be in touch with you shortly."
            ),
        ],
        "A bus hit my car KA-01-AB-1234 this morning. I want to make a claim.",
        200,
        "CLM-019002",
    ),
    (
        "a payout reply after reading the claim first",
        [
            says("", ("get_claim", {"id": "CLM-010003"})),
            says("", ("request_payout", {"id": "CLM-010003"})),
            says(
                "Your claim CLM‑010003 has now been paid. The payout has been released to "
                "the account linked to your policy."
            ),
        ],
        "CLM-010003 shows as approved. Can you release the payment please?",
        200,
        "has now been paid",
    ),
]


@pytest.mark.parametrize(
    ("case", "script", "text", "status", "holds"), LIVE, ids=[c[0] for c in LIVE]
)
async def test_the_live_models_correct_replies_reach_the_policyholder(
    case: str, script: list[ModelResponse], text: str, status: int, holds: str
) -> None:
    async with app(script) as a:
        said = await a.say("rohan", text)
    body = said.json()
    assert (said.status_code, body["outcome"]) == (status, "completed"), (case, body)
    assert holds in body["reply"], case
