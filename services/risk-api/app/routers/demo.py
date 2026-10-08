"""Demo control (D-11). Only available when DEMO_MODE=true. Stages scenarios; it is not part of the product."""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from .. import db
from ..bus import bus
from ..common import audit, clean, to_uuid
from ..core import warm
from ..core.features import AssessInput, DAY
from ..orchestrator import assess, get_snapshot, publish_outcome
from ..realtime import emit_user
from ..runtime import rt
from ..security import ApiError, Principal, get_principal, hash_secret_async, phone_hash, require_user
from ..workers import jobs, summariser
from .signals import CallEvent, ingest_call

router = APIRouter(prefix="/v1/demo", tags=["demo"])
DEMO_CALLERS = {0: "9111111100", 1: "9111111101", 2: "9111111102", 3: "9111111103"}
DEMO_HINDI = ["meera@banka", "sunita@bankb"]     # demo users whose app starts in Hindi (ml/upi_ml/simulator.py)

SCENARIOS: dict[str, dict] = {
    "digital_arrest": {
        "title": "Digital arrest", "payee": "cyber.cell.verify@bankb", "amount": 180000, "note": "",
        "claimed_name": "Cyber Cell Verification", "entry": "pasted",
        "call": {"risk": 3, "started_ago_s": 1500, "active": True, "label": "+91 91111 11103"},
        "session": {"vpa_entry_method": "pasted", "app_switch_count": 4, "time_on_pay_screen_s": 140},
        "steps": ["Accept the incoming call on the phone.", "Tap Pay, paste the UPI ID, enter the amount.",
                  "Watch the payer screen, the guardian's phone and this dashboard."]},
    "fake_refund": {
        "title": "Fake refund", "payee": "refund.desk7@bankb", "amount": 4999, "note": "refund processing fee",
        "claimed_name": "SBI Refund Desk", "entry": "link",
        "call": {"risk": 3, "started_ago_s": 660, "ended_ago_s": 360, "active": False, "label": "+91 91111 11103"},
        "message": "Dear customer, your SBI refund of Rs 4,999 is pending. To receive it pay Rs 4,999 verification "
                   "amount to refund.desk7@bankb within 2 hours. It will be returned with the refund. Helpline 9111111103",
        "session": {"vpa_entry_method": "link", "app_switch_count": 2, "time_on_pay_screen_s": 80},
        "steps": ["Open the simulated message and tap Check this message.", "Then pay the UPI ID it names."]},
    "mule_transfer": {
        "title": "Mule transfer", "payee": "booking.advance@bankb", "amount": 12000, "note": "advance for booking",
        "claimed_name": None, "entry": "pasted", "fan_in": 37,
        "session": {"vpa_entry_method": "pasted", "app_switch_count": 1, "time_on_pay_screen_s": 45},
        "steps": ["Tap Pay and enter the UPI ID. The chip turns amber before any amount is typed."]},
    "impersonation": {
        "title": "Impersonation", "payee": "vendor.payments@banka", "amount": 85000, "note": "vendor payment urgent new account",
        "claimed_name": "Mehta Suppliers Pvt Ltd", "entry": "pasted",
        "message": "Hi, this is Mr. Mehta (new number). I am in a board meeting and can't talk. Need you to clear the "
                   "vendor payment of Rs 85,000 right now to vendor.payments@banka. Account has changed. Keep it confidential.",
        "session": {"vpa_entry_method": "pasted", "app_switch_count": 3, "time_on_pay_screen_s": 90},
        "steps": ["Open the simulated message.", "Pay the UPI ID with the name you were given."]},
    "kyc_threat": {
        "title": "KYC threat", "payee": "kyc.update.sbi@bankb", "amount": 2499, "note": "kyc update charge",
        "claimed_name": "SBI KYC Update", "entry": "link",
        "call": {"risk": 2, "started_ago_s": 420, "ended_ago_s": 180, "active": False, "label": "+91 91111 11102"},
        "message": "Dear customer your SBI account will be blocked today due to pending KYC. Pay Rs 2,499 KYC update "
                   "charge to kyc.update.sbi@bankb immediately or call 9111111102.",
        "session": {"vpa_entry_method": "link", "app_switch_count": 2, "time_on_pay_screen_s": 60},
        "steps": ["Open the simulated message and check it.", "Then try the payment."]},
    "investment_scam": {
        "title": "Investment scam", "payee": "quick.returns@banka", "amount": 25000, "note": "task deposit plan A",
        "claimed_name": "Alpha Trading Desk", "entry": "pasted",
        "message": "Task 3 completed! Guaranteed 30% returns. To withdraw your earnings of Rs 9,800 deposit Rs 25,000 "
                   "as prepaid task amount to quick.returns@banka today.",
        "session": {"vpa_entry_method": "pasted", "app_switch_count": 2, "time_on_pay_screen_s": 70},
        "steps": ["Open the simulated message.", "Pay the UPI ID with the note shown."]},
}


def _sms(sc: dict) -> dict:
    """A simulated message. A scenario whose payment starts from a link carries that link, so the app can
    open the payment the way a tapped link would (vpa_entry_method = link)."""
    out = {"kind": "message", "text": sc["message"], "sender": "VM-ALERTS"}
    if sc.get("entry") == "link":
        out["link"] = {"vpa": sc["payee"], "name": sc.get("claimed_name"), "amount": sc["amount"], "note": sc.get("note") or ""}
    return out


async def demo_admin(p: Principal = Depends(get_principal)) -> Principal:
    if not rt.settings.demo_mode:
        raise ApiError(404, "NOT_FOUND", "Demo mode is off")
    if p.kind == "service" or (p.kind == "staff" and p.role == "admin"):
        return p
    raise ApiError(403, "FORBIDDEN", "Demo control needs an admin")


async def _user(user_id: str) -> dict:
    u = rt.user_index.get(str(user_id))
    if u is None and await warm.load_user(str(user_id)):
        u = rt.user_index.get(str(user_id))
    if u is None:
        raise ApiError(404, "NOT_FOUND", "No such user")
    return u


@router.get("/scenarios")
async def scenarios(p: Principal = Depends(demo_admin)) -> dict:
    users = await db.fetch("SELECT user_id, display_name, tenant_id, locale, primary_vpa FROM users WHERE is_demo "
                           "ORDER BY created_at, display_name")
    return {"scenarios": [{"key": k, **{f: v[f] for f in ("title", "payee", "amount", "note", "claimed_name", "entry", "steps")},
                           "has_call": "call" in v, "has_message": "message" in v} for k, v in SCENARIOS.items()],
            "users": clean(users)}


@router.post("/scenario")
async def stage(body: dict, p: Principal = Depends(demo_admin)) -> dict:
    sc = SCENARIOS.get(str(body.get("scenario")))
    if not sc:
        raise ApiError(400, "VALIDATION_ERROR", f"scenario must be one of {sorted(SCENARIOS)}")
    user_id = str(body.get("user_id", ""))
    u = await _user(user_id)
    now = time.time()
    staged: list[str] = []
    r = rt.store.r
    await r.delete(f"demo_ovr:{user_id}")
    await r.hset(f"demo_ovr:{user_id}", mapping={k: str(v) for k, v in sc["session"].items()})
    await r.expire(f"demo_ovr:{user_id}", 1800)
    await warm.load_payee(u["tenant"], sc["payee"])
    if sc.get("fan_in"):
        pipe = r.pipeline(transaction=False)
        for i in range(sc["fan_in"]):
            pid = f"demo-payer-{i}"
            ts = now - random.uniform(60, 3000)
            pipe.zadd(f"vpa_in:{u['tenant']}:{sc['payee']}", {pid: ts})
            pipe.zadd(f"vpa_in1:{u['tenant']}:{sc['payee']}", {pid: ts})
        await pipe.execute()
        staged.append(f"{sc['fan_in']} first-time payers to {sc['payee']} in the last hour")
    call = sc.get("call")
    if call:
        caller = phone_hash(DEMO_CALLERS[call["risk"]])
        call_id = str(uuid.uuid4())
        started = now - call["started_ago_s"]
        res = await ingest_call(CallEvent(call_id=call_id, event="started", callee_user_id=user_id,
                                          caller_phone_hash=caller, caller_known=False, at=started if not call["active"] else now,
                                          started_at=started, source="demo"))
        if call["active"]:
            await emit_user(user_id, "call.incoming", {"call_id": call_id, "caller_label": call["label"],
                                                       "started_at": started, "caller_risk": res["caller_risk"]})
            staged.append(f"an active call from an unknown number rated risk {res['caller_risk']}, started "
                          f"{call['started_ago_s'] // 60} minutes ago")
        else:
            await ingest_call(CallEvent(call_id=call_id, event="ended", callee_user_id=user_id, caller_phone_hash=caller,
                                        caller_known=False, at=now - call["ended_ago_s"], source="demo"))
            staged.append(f"a call from an unknown number rated risk {res['caller_risk']} that ended "
                          f"{call['ended_ago_s'] // 60} minutes ago")
    if sc.get("message"):
        await emit_user(user_id, "sms.simulated", _sms(sc))
        staged.append("a scam message delivered to the phone (not yet checked)")
    await audit(p, "demo.scenario", str(body.get("scenario")), {"user_id": user_id})
    return {"scenario": body.get("scenario"), "user": u["name"], "staged": staged, "message": sc.get("message"),
            "suggested_payment": {"payee_vpa": sc["payee"], "amount": sc["amount"], "note": sc["note"],
                                  "claimed_name": sc["claimed_name"], "entry_method": sc["entry"]},
            "steps": sc["steps"]}


@router.post("/call")
async def demo_call(body: dict, p: Principal = Depends(demo_admin)) -> dict:
    user_id = str(body.get("user_id", ""))
    await _user(user_id)
    action = str(body.get("action", "start"))
    if action == "start":
        risk = int(body.get("caller_risk", 3))
        call_id = str(uuid.uuid4())
        res = await ingest_call(CallEvent(call_id=call_id, event="started", callee_user_id=user_id,
                                          caller_phone_hash=phone_hash(DEMO_CALLERS.get(risk, DEMO_CALLERS[0])),
                                          caller_known=bool(body.get("caller_known", False)), source="demo"))
        await emit_user(user_id, "call.incoming", {"call_id": call_id, "caller_label": f"+91 91111 1110{risk}",
                                                   "started_at": time.time(), "caller_risk": res["caller_risk"]})
        return {"call_id": call_id, **res}
    return await end_call_for(user_id)


async def end_call_for(user_id: str) -> dict:
    u = await _user(user_id)
    cur = await rt.store.r.hgetall(f"call_flag:{u['phone_hash']}")
    if not cur or cur.get("state") != "active":
        return {"ended": False}
    await ingest_call(CallEvent(call_id=cur["call_id"], event="ended", callee_phone_hash=u["phone_hash"],
                                caller_phone_hash="-", caller_known=cur.get("caller_known") == "1", source="demo"))
    await emit_user(user_id, "call.ended", {"call_id": cur["call_id"]})
    return {"ended": True, "call_id": cur["call_id"]}


@router.post("/my-call/end")
async def end_my_call(p: Principal = Depends(require_user)) -> dict:
    """The payer hangs up the simulated call from the in-call bar."""
    if not rt.settings.demo_mode:
        raise ApiError(404, "NOT_FOUND", "Demo mode is off")
    return await end_call_for(p.sub)


@router.get("/my-call")
async def my_call(p: Principal = Depends(require_user)) -> dict:
    u = await _user(p.sub)
    cur = await rt.store.r.hgetall(f"call_flag:{u['phone_hash']}")
    if cur and cur.get("state") == "active":
        return {"active": True, "call_id": cur["call_id"], "started_at": float(cur.get("started_at", 0))}
    return {"active": False}


@router.post("/message")
async def demo_message(body: dict, p: Principal = Depends(demo_admin)) -> dict:
    user_id = str(body.get("user_id", ""))
    await _user(user_id)
    sc = SCENARIOS.get(str(body.get("template")), {})
    text = body.get("text") or sc.get("message")
    if not text:
        raise ApiError(400, "VALIDATION_ERROR", "Give text or a template with a message")
    payload = _sms(sc) if (sc.get("message") and not body.get("text")) else {"kind": "message", "text": text, "sender": "VM-ALERTS"}
    await emit_user(user_id, "sms.simulated", payload)
    return {"delivered": True}


async def _sample_requests(n: int, fraud_share: float = 0.03) -> list[dict]:
    rows = await db.fetch(
        """SELECT tenant_id, user_id, payer_vpa, payee_vpa, amount, note, claimed_name, device_id, session, truth_is_fraud
           FROM transactions WHERE is_synthetic AND truth_is_fraud IS NOT NULL
             AND created_at > now() - interval '20 days' ORDER BY random() LIMIT $1""", n * 3)
    legit = [r for r in rows if not r["truth_is_fraud"]]
    fraud = [r for r in rows if r["truth_is_fraud"]]
    k = max(1, int(n * fraud_share))
    picked = legit[: n - k] + fraud[:k]
    random.shuffle(picked)
    return [dict(r) for r in picked[:n]]


async def _run(rows: list[dict], rate: float, amount_factor: float = 1.0, now_fn=None, settle: bool = True) -> dict:
    tiers: dict[str, int] = {}
    gap = 1.0 / max(rate, 0.1)
    for r in rows:
        inp = AssessInput(txn_id=f"RPL-{uuid.uuid4().hex[:14]}", tenant=r["tenant_id"], user_id=str(r["user_id"]),
                          payer_vpa=r["payer_vpa"], payee_vpa=r["payee_vpa"], amount=float(r["amount"]) * amount_factor,
                          device_id=str(r["device_id"]) if r["device_id"] else None, claimed_name=r["claimed_name"],
                          note=r["note"], session=r["session"] or {}, locale="en")
        res = await assess(inp, now=now_fn() if now_fn else None, synthetic=True)
        tiers[res["tier"]] = tiers.get(res["tier"], 0) + 1
        if settle and res["action"] == "ALLOW":
            snap = await get_snapshot(res["decision_id"])
            if snap:
                await publish_outcome(snap, "PROCEEDED", "SUCCESS")
        await asyncio.sleep(gap)
    return tiers


_replay_task: asyncio.Task | None = None


@router.post("/replay")
async def replay(body: dict, p: Principal = Depends(demo_admin)) -> dict:
    """Replay simulator traffic through the live pipeline so the feed moves."""
    global _replay_task
    rate = min(float(body.get("rate_per_s", 5)), 50.0)
    seconds = min(float(body.get("duration_s", 30)), 300.0)
    if _replay_task and not _replay_task.done():
        raise ApiError(409, "CONFLICT", "A replay is already running")
    rows = await _sample_requests(int(rate * seconds))
    _replay_task = asyncio.create_task(_run(rows, rate))
    return {"started": True, "payments": len(rows), "rate_per_s": rate}


@router.post("/drift")
async def drift(body: dict, p: Principal = Depends(demo_admin)) -> dict:
    """Send traffic the model was not trained on (much larger amounts, small hours), then report drift."""
    n = min(int(body.get("n", 400)), 2000)
    rows = [r for r in await _sample_requests(n, fraud_share=0.0) if not r["truth_is_fraud"]]
    base = datetime.now(timezone.utc).replace(hour=21, minute=40, second=0, microsecond=0).timestamp() - DAY  # 03:10 IST
    if base < time.time() - DAY + 3600:
        base += DAY if base + DAY < time.time() else 0
    tiers = await _run(rows, rate=200, amount_factor=float(body.get("amount_factor", 12)),
                       now_fn=lambda: base + random.uniform(0, 3000), settle=False)
    await bus().drain()
    report = await jobs.run_drift(hours=float(body.get("hours", 30)))
    return {"sent": len(rows), "tiers": tiers, "drift": report}


@router.post("/metrics-reset")
async def metrics_reset(p: Principal = Depends(demo_admin)) -> dict:
    """Start a clean latency measurement (used by the load test)."""
    rt.metrics.reset()
    return {"reset": True}


@router.post("/chaos")
async def chaos(body: dict, p: Principal = Depends(demo_admin)) -> dict:
    """Simulate a dependency failure for the failure tests: {"cache_down": true} or {"model_down": true}."""
    for k in ("cache_down", "model_down"):
        if k in body:
            rt.chaos[k] = bool(body[k])
    await audit(p, "demo.chaos", "runtime", dict(rt.chaos))
    return rt.chaos


@router.post("/reset")
async def reset(p: Principal = Depends(demo_admin)) -> dict:
    """Restore the seeded state. The audit log is kept."""
    t0 = time.time()
    await bus().drain()
    seed = await db.fetchval("SELECT value FROM meta WHERE key='seed'")
    if not seed:
        raise ApiError(409, "CONFLICT", "No seed marker; run the bootstrap first")
    cut = datetime.fromtimestamp(float(seed["seeded_at"]) + 1, timezone.utc)
    async with db.pool().acquire() as c:
        async with c.transaction():
            ids = [r["decision_id"] for r in await c.fetch("SELECT decision_id FROM risk_decisions WHERE created_at > $1", cut)]
            for table in ("outcomes", "decision_labels", "signal_links", "stepup_challenges", "case_summaries"):
                await c.execute(f"DELETE FROM {table} WHERE decision_id = ANY($1::uuid[])", ids)
            await c.execute("DELETE FROM risk_decisions WHERE created_at > $1", cut)
            await c.execute("DELETE FROM transactions WHERE created_at > $1", cut)
            await c.execute("DELETE FROM decision_labels")
            for table in ("guardian_requests", "held_payments", "pair_blocks", "complaint_packs", "rehearsal_runs"):
                await c.execute(f"DELETE FROM {table}")
            await c.execute("DELETE FROM message_checks WHERE created_at > $1", cut)
            await c.execute("DELETE FROM call_events WHERE started_at > $1 OR source='demo'", cut)
            await c.execute("DELETE FROM fraud_reports WHERE created_at > $1", cut)
            await c.execute("DELETE FROM vpa_events WHERE at > $1", cut)
            await c.execute("DELETE FROM vpa_reputation_history WHERE at > $1", cut)
            await c.execute("DELETE FROM flagged_indicator_reporters r USING flagged_indicators i WHERE "
                            "i.tenant_id=r.tenant_id AND i.indicator_type=r.indicator_type AND i.value=r.value AND i.first_seen > $1", cut)
            await c.execute("DELETE FROM flagged_indicators WHERE first_seen > $1", cut)
            await c.execute("DELETE FROM caller_risk_list WHERE source IN ('flagged_messages','caller_reports')")
            await c.execute("DELETE FROM caller_reports")
            await c.execute("DELETE FROM user_blocks")
            # the seed list may have been raised by reports during the demo: put the seeded levels back
            for risk, number in DEMO_CALLERS.items():
                if risk > 0:
                    await c.execute("UPDATE caller_risk_list SET risk=$2, source='seed' WHERE caller_hash=$1",
                                    phone_hash(number), risk)
            await c.execute("DELETE FROM consortium_signals WHERE updated_at > $1", cut)
            await c.execute("DELETE FROM vpa_reputation")
            await c.execute("INSERT INTO vpa_reputation SELECT * FROM vpa_reputation_seed")
            await c.execute("UPDATE users SET balance = seed_balance")
            # settings a presenter may have changed on a demo phone: language, voice, text size
            await c.execute("UPDATE users SET locale = CASE WHEN primary_vpa = ANY($1::text[]) THEN 'hi' ELSE 'en' END, "
                            "voice_warnings = true, large_text = false, active = true, failed_attempts = 0, "
                            "lockouts = 0, locked_until = NULL WHERE is_demo", DEMO_HINDI)
            # sign-in details a presenter may have changed, back to the seeded ones (SEED_USER_PIN, SEED_STAFF_PASSWORD)
            await c.execute("UPDATE users SET pin_hash=$1 WHERE is_demo AND pin_changed_at IS NOT NULL",
                            await hash_secret_async(rt.settings.seed_user_pin))
            await c.execute("UPDATE users SET pin_changed_at = NULL WHERE is_demo")
            await c.execute("UPDATE staff_users SET password_hash=$1 WHERE email LIKE '%.demo' AND password_changed_at IS NOT NULL",
                            await hash_secret_async(rt.settings.seed_staff_password))
            await c.execute("UPDATE staff_users SET password_changed_at = NULL, active = true, failed_attempts = 0, "
                            "lockouts = 0, locked_until = NULL WHERE email LIKE '%.demo'")
            await c.execute("DELETE FROM guardians WHERE NOT is_seed")
            # accounts registered through the app since the seed (the seeded ones are demo or simulated)
            await c.execute("DELETE FROM vpas WHERE owner_user_id IN (SELECT user_id FROM users WHERE NOT is_demo AND NOT is_synthetic)")
            await c.execute("DELETE FROM users WHERE NOT is_demo AND NOT is_synthetic")
            await c.execute("DELETE FROM staff_users WHERE email NOT LIKE '%.demo'")
            await c.execute("UPDATE guardians SET status='ACTIVE', pending_change=NULL, change_effective_at=NULL, "
                            "mode='co_approve', amount_threshold=20000 WHERE is_seed")
            await c.execute("UPDATE policy_config SET mode='enforce', t_warning=0.30, t_stepup=0.60, t_block=0.85, "
                            "t_decline=0.95, block_mode='hold'")
            await c.execute("DELETE FROM drift_reports")
    rt.metrics.reset()
    rt.chaos.update({"cache_down": False, "model_down": False})
    summariser.clear_queue()
    counts = await warm.warm_all()
    try:
        await jobs.run_graph_job(force_communities=True)
    except Exception:
        pass
    await audit(p, "demo.reset", "all", {"seconds": round(time.time() - t0, 2)})
    return {"reset": True, "seconds": round(time.time() - t0, 2), "cache": counts}
