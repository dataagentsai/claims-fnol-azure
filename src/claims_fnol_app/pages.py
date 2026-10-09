"""This insurer's two pages: the policyholder's chat, and the claims handler's desk.

The edge behind them — `/chat`, the desk's API under `/ops`, the error contract —
is the harness's (`agent_harness.serve`, `agent_harness.reviewer`), exactly as the
reference agent's `serve`/`reviewer` wrap it; these are the words and layout.

Both pages take the session token from the link, as the reference agent's do,
and keep it only in the page's memory. Honest for a local test issuer; a
deployment reads its session from the sign-in instead (Tier 4).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

STYLE = """
:root{--bg:#F4F6F8;--card:#fff;--sunk:#EBEEF2;--ink:#141A21;--ink-2:#48545F;--dim:#78838D;
  --line:#DCE1E7;--accent:#2A5A8C;--no:#A6462B;--warn:#8A6608;--ok:#2E6B3F;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#0F1418;--card:#161C22;--sunk:#111820;
  --ink:#E4E9EE;--ink-2:#A2AEB9;--dim:#76828D;--line:#26303A;--accent:#7FB0DC;--no:#DE8E76;
  --warn:#D6AC4B;--ok:#7BC18E}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 var(--sans)}
"""

CHAT_PAGE = (
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Motor claims</title>
<style>"""
    + STYLE
    + """
body{display:flex;flex-direction:column;height:100vh}
header{padding:14px 16px;border-bottom:1px solid var(--line);display:flex;
  align-items:baseline;gap:14px;flex-wrap:wrap}
h1{font-size:16px;font-weight:600;margin:0}
.cid{font:12px var(--mono);color:var(--dim)}
a{color:var(--accent)}
#log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.turn{max-width:62ch;padding:10px 14px;border-radius:12px;white-space:pre-wrap;
  word-break:break-word}
.me{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px}
.them{align-self:flex-start;background:var(--card);border:1px solid var(--line);
  border-bottom-left-radius:4px}
.meta{font:11px var(--mono);color:var(--dim);margin-top:6px}
.them.refused .meta{color:var(--no)} .them.needsapproval .meta{color:var(--warn)}
.sys{align-self:center;font:12px var(--mono);color:var(--dim);background:var(--sunk);
  padding:6px 12px;border-radius:20px}
form{display:flex;gap:10px;padding:12px 16px;border-top:1px solid var(--line);
  background:var(--card)}
input{flex:1;min-width:0;padding:12px 14px;border:1px solid var(--line);border-radius:9px;
  background:var(--bg);color:var(--ink);font:15px var(--sans)}
input:focus{outline:2px solid var(--accent);outline-offset:1px}
button{padding:12px 18px;border:0;border-radius:9px;background:var(--accent);color:#fff;
  font:600 15px var(--sans);cursor:pointer}
button:disabled{opacity:.5;cursor:default}
</style></head><body>
<header>
  <h1>Motor claims</h1>
  <span class="cid" id="cid">new conversation</span>
  <a href="/signin" style="margin-left:auto;font-size:13px">switch user</a>
</header>
<div id="log" aria-live="polite"></div>
<form id="f" autocomplete="off">
  <input id="t" aria-label="Your message"
    placeholder="Try: a bus hit my car KA-01-AB-1234 this morning, I want to make a claim">
  <button id="send">Send</button>
</form>
<script>
const TOKEN = new URLSearchParams(location.search).get("token") || "";
let conversationId = null, pending = null;
const log = document.getElementById("log"), form = document.getElementById("f");
const input = document.getElementById("t"), send = document.getElementById("send");
const cid = document.getElementById("cid");
function bubble(cls, text, meta){
  const el = document.createElement("div");
  el.className = "turn " + cls; el.textContent = text;
  if (meta){ const m = document.createElement("div"); m.className = "meta";
    m.textContent = meta; el.appendChild(m); }
  log.appendChild(el); log.scrollTop = log.scrollHeight;
}
function system(text){
  const el = document.createElement("div"); el.className = "sys"; el.textContent = text;
  log.appendChild(el); log.scrollTop = log.scrollHeight;
}
async function opening(){
  if (!TOKEN){ system("Not signed in. Go to /signin."); return; }
  try {
    const res = await fetch("/opening", {headers: {"authorization": "Bearer " + TOKEN}});
    const data = await res.json();
    if (data.reply) bubble("them", data.reply, "opening · no model call");
    else system(data.error || "HTTP " + res.status);
  } catch (err) { system("Could not reach the claims assistant."); }
}
form.addEventListener("submit", async e => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  // One delivery id per message; a retry after a timeout reuses it on purpose.
  const delivery = pending || (crypto.randomUUID ? crypto.randomUUID()
                                                 : String(Date.now()) + Math.random());
  pending = delivery;
  bubble("me", text); input.value = ""; input.disabled = send.disabled = true;
  try {
    const res = await fetch("/chat", {method: "POST", headers: {
        "content-type": "application/json", "authorization": "Bearer " + TOKEN,
        "idempotency-key": delivery},
      body: JSON.stringify({text, conversation_id: conversationId})});
    const data = await res.json();
    if (data.status === "already handled"){ system("already handled — not sent twice"); }
    else if (data.error){ bubble("them", data.error, "HTTP " + res.status); }
    else {
      conversationId = data.conversation_id || conversationId;
      cid.textContent = conversationId || "new conversation";
      bubble("them " + (data.outcome || ""), data.reply || "(no reply)",
             data.outcome + " · HTTP " + res.status);
    }
    pending = null;
  } catch (err) {
    bubble("them", "Could not reach the claims assistant. " +
           "Sending again will not duplicate anything.", "network error");
  } finally { input.disabled = send.disabled = false; input.focus(); }
});
opening();
</script></body></html>
"""
)
"""The policyholder's chat. On load it asks `/opening` for their open claims and
policies (P-OPEN, no model call); every message goes to `/chat` with its own
delivery id, and the conversation id is echoed back."""

DESK_PAGE = (
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claims handler desk</title>
<style>"""
    + STYLE
    + """
body{padding:24px 16px 64px}
main{max-width:900px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px} h2{font-size:15px;margin:28px 0 8px}
p.note{color:var(--dim);margin:0 0 16px;font-size:13px}
.row{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;margin-bottom:10px}
.head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.id{font:12px var(--mono);color:var(--dim)} .what{font-weight:600}
.waited{margin-left:auto;color:var(--warn);font-size:13px}
.why{color:var(--dim);font-size:13px;margin:6px 0 10px}
button{font:inherit;padding:6px 14px;border-radius:8px;border:1px solid var(--line);
  background:transparent;color:var(--ink);cursor:pointer;margin:0 8px 6px 0}
button.go{border-color:var(--accent);color:var(--accent)}
button.no{border-color:var(--no);color:var(--no)}
button:disabled{opacity:.45;cursor:default}
.empty{color:var(--dim);font-size:14px;padding:6px 0}
.said{margin-top:8px;font-size:13px} .said.bad{color:var(--no)} .said.ok{color:var(--ok)}
footer{margin-top:32px;color:var(--dim);font-size:12px}
</style></head><body><main>
<h1>Claims handler desk</h1>
<p class="note" id="who">Reading…</p>
<h2>Payouts waiting for your decision</h2>
<div id="approvals"><p class="empty">Reading…</p></div>
<h2>Conversations handed to a person</h2>
<div id="escalations"><p class="empty">Reading…</p></div>
<footer>Refreshes every 5 seconds. Approving tells the payout's wait what you decided;
the wait re-reads the claim and pays it under its own login. Nobody at this desk
moves money directly.</footer>
</main>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
const head = {"authorization": "Bearer " + token, "content-type": "application/json"};
const ago = s => s < 60 ? s + "s" : s < 3600 ? Math.floor(s/60) + "m" : Math.floor(s/3600) + "h";
const esc = t => String(t).replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const rupees = a => a ? "₹" + Number(a).toLocaleString("en-IN") : "";
async function call(path, options){
  const answered = await fetch(path, {headers: head, ...options});
  const body = await answered.json().catch(() => ({}));
  if (!answered.ok) throw new Error(body.detail || ("HTTP " + answered.status));
  return body;
}
function say(id, message, good){
  const row = document.getElementById(id); if (!row) return;
  const line = row.querySelector(".said") || document.createElement("div");
  line.className = "said " + (good ? "ok" : "bad"); line.textContent = message;
  row.appendChild(line);
}
function approval(row){
  const claim = (row.args && row.args.claim_id) || "";
  const amount = row.args && row.args.amount ? " · " + rupees(row.args.amount) : "";
  const what = "Payout · " + claim + amount;
  return `<div class="row" id="${esc(row.id)}">
    <div class="head"><span class="what">${esc(what)}</span><span class="id">${esc(row.id)}</span>
      <span class="waited">waiting ${ago(row.waiting_s)}</span></div>
    <div class="why">${esc(row.reason || "")} — policyholder ${esc(row.customer_id)}</div>
    <button class="go" onclick="decide('${esc(row.id)}', true, this)">Approve payout</button>
    <button class="no" onclick="decide('${esc(row.id)}', false, this)">Refuse</button></div>`;
}
function escalation(row){
  return `<div class="row" id="${esc(row.id)}">
    <div class="head"><span class="what">${esc(row.reason || row.rule_id)}</span>
      <span class="id">${esc(row.id)}</span>
      <span class="waited">waiting ${ago(row.waiting_s)}</span></div>
    <div class="why">policyholder ${esc(row.customer_id)} ·
      conversation ${esc(row.conversation_id)}</div>
    <button class="go" onclick="close_it('${esc(row.id)}', 'resolved', this)">Handled</button>
    <button onclick="close_it('${esc(row.id)}', 'agent_could_have', this)">Agent could have</button>
    <button onclick="close_it('${esc(row.id)}', 'misrouted', this)">Misrouted</button></div>`;
}
async function decide(id, granted, button){
  button.parentElement.querySelectorAll("button").forEach(b => b.disabled = true);
  try {
    const done = await call(`/ops/approvals/${id}/decide`, {method: "POST",
      body: JSON.stringify({granted})});
    const result = done.result ? ": " + done.result : "";
    say(id, (granted ? "Approved — " : "Refused — ") + done.state + result, true);
  } catch (wrong) {
    say(id, String(wrong.message), false);
    button.parentElement.querySelectorAll("button").forEach(b => b.disabled = false);
  }
  setTimeout(refresh, 1500);
}
async function close_it(id, outcome, button){
  button.parentElement.querySelectorAll("button").forEach(b => b.disabled = true);
  try {
    await call(`/ops/escalations/${id}/resolve`, {method: "POST", body: JSON.stringify({outcome})});
    say(id, "Closed as " + outcome + ".", true);
  } catch (wrong) {
    say(id, String(wrong.message), false);
    button.parentElement.querySelectorAll("button").forEach(b => b.disabled = false);
  }
  setTimeout(refresh, 1500);
}
async function one(path, into, render, nothing){
  const box = document.getElementById(into);
  try {
    const rows = await call(path);
    box.innerHTML = rows.length ? rows.map(render).join("") : `<p class="empty">${nothing}</p>`;
  } catch (wrong) { box.innerHTML = `<p class="empty">${esc(wrong.message)}</p>`; }
}
async function refresh(){
  document.getElementById("who").textContent = token
    ? "Signed in as a claims handler (local test sign-in)." : "Not signed in — go to /signin.";
  await Promise.all([
    one("/ops/approvals", "approvals", approval, "No payout is waiting for a decision."),
    one("/ops/escalations", "escalations", escalation, "Nobody is waiting for a person."),
  ]);
}
refresh(); setInterval(refresh, 5000);
</script></body></html>
"""
)
"""The claims handler's page: payouts waiting for a decision, and conversations
handed to a person. It calls nothing but the desk's API under `/ops`."""

desk_router = APIRouter()


@desk_router.get("/desk", response_class=HTMLResponse, include_in_schema=False)
async def desk() -> HTMLResponse:
    """The page itself; it carries no data, and every call it makes is authorised."""
    return HTMLResponse(DESK_PAGE)


__all__ = ["CHAT_PAGE", "DESK_PAGE", "desk_router"]
