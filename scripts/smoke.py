"""The live smoke check: Tier 2's done-when flows, over HTTP, with the real model.

    scripts/dev-up.sh --fresh          # the seeded claims as the world declares them
    .venv/bin/python scripts/smoke.py  # a handful of model calls on Groq's free tier
    scripts/dev-down.sh

Signs in as Rohan Iyer (PH-1001) and as Asha Rao (claims handler) through the
local sign-in, then:

    A  report a collision            → a CLM- reference the claims system made
    B  payout of CLM-010004 (₹25,001) → waits; Asha approves on the desk; paid
    C  payout of CLM-010003 (₹25,000) → paid at once, nobody asked
    D  claim status                   → answered with no model call

Each flow is a fresh conversation. Model calls and tokens per flow come from the
app's `/dev/usage`. Writes `.state/smoke.json` and prints a summary.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8077"
OUT = Path(__file__).resolve().parents[1] / ".state" / "smoke.json"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def http(method: str, path: str, *, token: str = "", body: Any = None) -> tuple[int, Any]:
    data = None
    headers = {"idempotency-key": uuid.uuid4().hex}
    if token:
        headers["authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as answered:
            return answered.status, json.loads(answered.read() or b"null")
    except urllib.error.HTTPError as failed:
        raw = failed.read()
        return failed.code, json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode()


def sign_in(login: str) -> str:
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(
        BASE + "/signin", data=f"login={login}".encode(), method="POST"
    )
    try:
        opener.open(request, timeout=10)
    except urllib.error.HTTPError as moved:
        where = moved.headers["location"]
        return urllib.parse.parse_qs(urllib.parse.urlsplit(where).query)["token"][0]
    raise SystemExit(f"sign-in for {login} did not redirect")


@dataclass
class Flow:
    name: str
    passed: bool = False
    turns: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


class Run:
    def __init__(self) -> None:
        self.rohan = sign_in("rohan")
        self.asha = sign_in("asha")

    def usage(self) -> dict[str, int]:
        return http("GET", "/dev/usage")[1]  # type: ignore[no-any-return]

    def say(self, flow: Flow, text: str, conversation: str | None = None) -> tuple[int, Any]:
        flow.turns += 1
        status, said = http(
            "POST", "/chat", token=self.rohan, body={"text": text, "conversation_id": conversation}
        )
        flow.notes.append(
            f"HTTP {status} {said.get('outcome') if isinstance(said, dict) else ''}: "
            f"{(said.get('reply') if isinstance(said, dict) else said)!s:.300}"
        )
        return status, said

    def status_of(self, flow: Flow, claim: str) -> str:
        _, said = self.say(flow, f"What's the status of claim {claim}?")
        return str(said.get("reply", "")) if isinstance(said, dict) else ""

    def measured(self, flow: Flow, body: Any) -> Flow:
        before, started = self.usage(), time.monotonic()
        try:
            flow.passed = bool(body(flow))
        except Exception as exc:  # noqa: BLE001 — a failed flow is a result, recorded
            flow.notes.append(f"error: {type(exc).__name__}: {exc}")
        after = self.usage()
        flow.seconds = round(time.monotonic() - started, 1)
        flow.model_calls = after["calls"] - before["calls"]
        flow.input_tokens = after["input_tokens"] - before["input_tokens"]
        flow.output_tokens = after["output_tokens"] - before["output_tokens"]
        if after["failures"] > before["failures"]:
            flow.notes.append(f"model failures: {after['failures'] - before['failures']}")
        return flow


def report_a_collision(run: Run, flow: Flow) -> bool:
    status, said = run.say(
        flow,
        "Hi, a BMTC bus hit my car from behind at the Silk Board signal this morning. Nobody "
        "was hurt but the rear bumper is badly damaged. The car is KA-01-AB-1234. I want to "
        "make a claim.",
    )
    # Any hyphen the model writes (gpt-oss uses U+2011; the agent normalises it).
    found = re.search(r"CLM[-\u2010\u2011]\d{6}", str(said.get("reply", "")))
    if status != 200 or found is None:
        return False
    reference = "CLM-" + found.group(0)[4:]
    flow.notes.append(f"reference {reference}")
    return "is registered" in run.status_of(flow, reference)


def large_payout_waits(run: Run, flow: Flow) -> bool:
    status, _ = run.say(flow, "Please release the payment for CLM-010004, the engine fire claim.")
    _, queue = http("GET", "/ops/approvals", token=run.asha)
    waiting = [a for a in queue if a.get("args", {}).get("claim_id") == "CLM-010004"]
    flow.notes.append(f"waiting on the desk: {len(waiting)}")
    if status != 202 or not waiting:
        return False
    unpaid = "is approved" in run.status_of(flow, "CLM-010004")
    decided_status, decided = http(
        "POST", f"/ops/approvals/{waiting[0]['id']}/decide", token=run.asha, body={"granted": True}
    )
    flow.notes.append(f"handler decided: HTTP {decided_status} {decided}")
    paid = "is paid" in run.status_of(flow, "CLM-010004")
    return unpaid and decided_status == 200 and decided.get("state") == "done" and paid


def small_payout_lands(run: Run, flow: Flow) -> bool:
    status, _ = run.say(flow, "CLM-010003 shows as approved. Can you release the payment please?")
    _, queue = http("GET", "/ops/approvals", token=run.asha)
    asked = [a for a in queue if a.get("args", {}).get("claim_id") == "CLM-010003"]
    return status == 200 and not asked and "is paid" in run.status_of(flow, "CLM-010003")


def status_without_a_model(run: Run, flow: Flow) -> bool:
    reply = run.status_of(flow, "CLM-010007")
    return "assess" in reply.lower()


FLOWS = [
    ("A report a collision → CLM- reference", report_a_collision),
    ("B payout ₹25,001 waits, handler approves, paid", large_payout_waits),
    ("C payout ₹25,000 paid at once", small_payout_lands),
    ("D claim status, no model call", status_without_a_model),
]


def main() -> int:
    run = Run()
    results = [run.measured(Flow(name), lambda f, b=body: b(run, f)) for name, body in FLOWS]
    d = results[3]
    if d.model_calls:
        d.passed = False
        d.notes.append("the model was called")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.name}")
        print(
            f"      turns={r.turns} model_calls={r.model_calls} tokens in/out="
            f"{r.input_tokens}/{r.output_tokens} {r.seconds}s"
        )
        for note in r.notes:
            print(f"      · {note}")
    print(f"\n  written to {OUT}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
