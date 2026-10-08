"""Async-path workers: outcome logger, reputation engine, consortium publisher (F-10, F-11, F-24)."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from .. import db
from ..bus import bus
from ..core import reputation as rep
from ..realtime import emit_analyst
from ..runtime import rt
from ..security import vpa_token

log = logging.getLogger("workers")
NEAR_MISS_TIERS = ("WARNING", "STEP_UP", "BLOCK")


def dt(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, timezone.utc) if ts else None


def _uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


# ------------------------------------------------------------------ outcome logger --
TXN_SQL = """INSERT INTO transactions(txn_id,tenant_id,user_id,payer_vpa,payee_vpa,claimed_name,amount,note,device_id,session,
                                      parent_decision_id,status,is_synthetic,created_at)
             VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'PENDING',$12,$13) ON CONFLICT (txn_id) DO NOTHING"""
DEC_SQL = """INSERT INTO risk_decisions(decision_id,txn_id,tenant_id,user_id,payee_vpa,amount,score,tier,action,enforced,
                reason_codes,reasons,rules_fired,contributions,scam_family,family_sim,features,detail,model_version,
                rules_version,degraded,latency_ms,stage_ms,is_synthetic,created_at)
             VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25)
             ON CONFLICT (txn_id) DO NOTHING"""
BATCH = 200
_pending: list[tuple[tuple, tuple]] = []
_notices: set = set()


def _notify(tenant: str, event: str, data: dict) -> None:
    """Send a dashboard notice without making the caller wait for the socket."""
    task = asyncio.ensure_future(emit_analyst(tenant, event, data))
    _notices.add(task)
    task.add_done_callback(_notices.discard)


async def flush_assessed() -> None:
    """Write the buffered payments and decisions. Two statements for the whole batch, not two per payment:
    at 100 payments a second the per-payment round trips were the slowest link in the service."""
    global _pending
    if not _pending:
        return
    batch, _pending = _pending, []
    async with db.pool().acquire() as c:
        await c.executemany(TXN_SQL, [t for t, _ in batch])
        await c.executemany(DEC_SQL, [d for _, d in batch])


async def log_assessed(ev: dict) -> None:
    req, dec, det = ev["request"], ev["decision"], ev.get("detail", {})
    created = dt(ev["at"])
    did = uuid.UUID(dec["decision_id"])
    syn = bool(ev.get("synthetic"))
    _pending.append((
        (req["txn_id"], ev["tenant_id"], _uuid(req["user_id"]), req["payer_vpa"], req["payee_vpa"], req.get("claimed_name"),
         req["amount"], req.get("note"), _uuid(req.get("device_id")), req.get("session") or {},
         _uuid(req.get("parent_decision_id")), syn, created),
        (did, req["txn_id"], ev["tenant_id"], _uuid(req["user_id"]), req["payee_vpa"], req["amount"], dec["score"],
         dec["tier"], dec["action"], dec["enforced"], dec.get("reason_codes", []), dec.get("reasons", []),
         dec.get("rules_fired", []), dec.get("contributions", {}), dec.get("scam_family_code"), dec.get("family_sim"),
         ev["features"], det, dec["model_version"], dec["rules_version"], dec["degraded"], dec["latency_ms"],
         ev.get("stage_ms", {}), syn, created)))
    # An ordinary allowed payment has nothing more to write and can wait for the batch. Anything else
    # (links, a step-up, a decline) is written now, with everything buffered before it.
    plain = (dec["action"] == "ALLOW" and not dec["links"]["calls"] and not dec["links"]["messages"]
             and not det.get("step_up") and not det.get("hold") and not det.get("guardian"))
    if not plain or len(_pending) >= BATCH:
        await flush_assessed()
    links = [(did, "call", _uuid(c)) for c in dec["links"]["calls"]] + \
            [(did, "message", _uuid(m)) for m in dec["links"]["messages"]]
    await db.executemany("INSERT INTO signal_links(decision_id,signal_type,signal_id) VALUES($1,$2,$3) "
                         "ON CONFLICT DO NOTHING", [x for x in links if x[2]])
    su = det.get("step_up")
    if su and dec["enforced"]:
        chal = await rt.store.r.get(f"chal:{su['challenge_id']}")
        import json

        c = json.loads(chal) if chal else {}
        await db.execute(
            """INSERT INTO stepup_challenges(challenge_id,decision_id,question_code,risky_answer,cooloff_until,status)
               VALUES($1,$2,$3,$4,$5,'OPEN') ON CONFLICT DO NOTHING""",
            uuid.UUID(su["challenge_id"]), did, c.get("question_code", "UNCLASSIFIED"), c.get("risky_answer", "yes"),
            dt(c.get("cooloff_until", ev["at"])))
    if dec["enforced"] and dec["action"] == "DECLINE":
        await db.execute(
            """INSERT INTO outcomes(decision_id,user_action,final_status,created_at) VALUES($1,'BLOCKED','NOT_ATTEMPTED',$2)
               ON CONFLICT DO NOTHING""", did, created)
        await db.execute("UPDATE transactions SET status='NOT_ATTEMPTED' WHERE txn_id=$1", req["txn_id"])
        if det.get("pair_block_until"):
            await db.execute(
                """INSERT INTO pair_blocks(user_id,payee_vpa,until,decision_id) VALUES($1,$2,$3,$4)
                   ON CONFLICT (user_id,payee_vpa) DO UPDATE SET until=EXCLUDED.until, decision_id=EXCLUDED.decision_id""",
                _uuid(req["user_id"]), req["payee_vpa"], dt(det["pair_block_until"]), did)
    # The live feed is a convenience: the dashboard re-reads the list on a timer. So the notice is sent
    # without waiting for it, and for Safe payments only while the logger is keeping up. Waiting on a
    # socket for every payment made the logger fall thousands of events behind under load.
    if dec["tier"] == "SAFE" and bus().backlog() > 50:
        return
    top = dec["reasons"][0]["message"] if dec.get("reasons") else None
    en = det.get("reasons_en") or []
    _notify(ev["tenant_id"], "decision.created", {
        "decision_id": dec["decision_id"], "at": ev["at"], "amount": req["amount"], "payee_vpa": req["payee_vpa"],
        "payee_name": (det.get("payee") or {}).get("registered_name"), "user_id": req["user_id"],
        "tier": dec["tier"], "action": dec["action"], "score": dec["score"],
        "top_reason": en[0]["message"] if en else top, "family": det.get("family_en"),
        "family_code": dec.get("scam_family_code"), "enforced": dec["enforced"], "degraded": dec["degraded"],
        "latency_ms": dec["latency_ms"], "synthetic": syn,
        "linked": {"call": bool(dec["links"]["calls"]), "message": bool(dec["links"]["messages"])},
        "guardian": bool(det.get("guardian"))})


async def log_outcome(ev: dict) -> None:
    did = uuid.UUID(ev["decision_id"])
    action = ev["user_action"]
    weight = rep.near_miss_weight(action) if (ev.get("enforced", True) and ev.get("tier") in NEAR_MISS_TIERS) else 0.0
    await db.execute(
        """INSERT INTO outcomes(decision_id,user_action,final_status,is_near_miss,near_miss_weight,created_at)
           VALUES($1,$2,$3,$4,$5,$6)
           ON CONFLICT (decision_id) DO UPDATE SET user_action=EXCLUDED.user_action, final_status=EXCLUDED.final_status,
             is_near_miss=EXCLUDED.is_near_miss, near_miss_weight=EXCLUDED.near_miss_weight, created_at=EXCLUDED.created_at""",
        did, action, ev.get("final_status"), weight > 0, weight, dt(ev.get("at") or time.time()))
    if ev.get("final_status"):
        await db.execute("UPDATE transactions SET status=$2 WHERE txn_id=$1", ev["txn_id"], ev["final_status"])
    if action == "STEPUP_FAILED":
        await db.execute("UPDATE risk_decisions SET action='DECLINE' WHERE decision_id=$1", did)
        await db.execute("UPDATE stepup_challenges SET status='FAILED' WHERE decision_id=$1", did)
    elif action == "STEPUP_PASSED":
        await db.execute("UPDATE stepup_challenges SET status='PASSED' WHERE decision_id=$1", did)
    elif action == "STEPUP_ABANDONED":
        await db.execute("UPDATE stepup_challenges SET status='ABANDONED' WHERE decision_id=$1 AND status='OPEN'", did)
    await emit_analyst(ev["tenant_id"], "outcome.recorded", {
        "decision_id": ev["decision_id"], "user_action": action, "final_status": ev.get("final_status"),
        "is_near_miss": weight > 0, "payee_vpa": ev.get("payee_vpa")})


# ------------------------------------------------------------------ reputation engine --
async def persist_reputation(tenant: str, vpa: str, ts: float, cause: str | None) -> dict:
    """Write this bank's current view of a payee and, if its band or score moved, tell everyone."""
    snap = await rt.store.rep_snapshot(tenant, vpa, ts)
    if not snap["exists"]:
        return snap
    prev = await db.fetchrow("SELECT band, trust_score FROM vpa_reputation WHERE tenant_id=$1 AND vpa_id=$2", tenant, vpa)
    await db.execute(
        """INSERT INTO vpa_reputation(tenant_id,vpa_id,recv_decayed,bad_decayed,lifetime_recv,reports_30d,confirmed_90d,
              near_miss_14d,fan_in_1h,fan_in_24h,first_time_payer_ratio_24h,p_bad,maturity,trust_score,band,band_reason,
              counters_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
           ON CONFLICT (tenant_id,vpa_id) DO UPDATE SET recv_decayed=EXCLUDED.recv_decayed, bad_decayed=EXCLUDED.bad_decayed,
              lifetime_recv=EXCLUDED.lifetime_recv, reports_30d=EXCLUDED.reports_30d, confirmed_90d=EXCLUDED.confirmed_90d,
              near_miss_14d=EXCLUDED.near_miss_14d, fan_in_1h=EXCLUDED.fan_in_1h, fan_in_24h=EXCLUDED.fan_in_24h,
              first_time_payer_ratio_24h=EXCLUDED.first_time_payer_ratio_24h, p_bad=EXCLUDED.p_bad,
              maturity=EXCLUDED.maturity, trust_score=EXCLUDED.trust_score, band=EXCLUDED.band,
              band_reason=EXCLUDED.band_reason, counters_at=EXCLUDED.counters_at, updated_at=EXCLUDED.updated_at""",
        tenant, vpa, snap["recv"], snap["bad"], snap["lifetime_recv"], snap["reports_30d"], snap["confirmed_90d"],
        snap["near_miss_14d"], snap["fan_in_1h"], snap["fan_in_24h"], snap["first_time_payer_ratio_24h"], snap["p_bad"],
        snap["maturity"], snap["trust_score"], snap["band"], snap["band_reason"], dt(snap["counters_at"]), dt(ts))
    old_band = prev["band"] if prev else "NO_HISTORY"
    old_score = prev["trust_score"] if prev else 45
    if cause and (old_band != snap["band"] or abs(old_score - snap["trust_score"]) >= 1):
        await db.execute(
            """INSERT INTO vpa_reputation_history(tenant_id,vpa_id,at,trust_score,band,cause) VALUES($1,$2,$3,$4,$5,$6)
               ON CONFLICT DO NOTHING""", tenant, vpa, dt(ts), snap["trust_score"], snap["band"], cause)
        await bus().publish("reputation.updated", {
            "tenant_id": tenant, "vpa": vpa, "old_band": old_band, "new_band": snap["band"], "old_score": old_score,
            "new_score": snap["trust_score"], "cause": cause, "p_bad": snap["p_bad"], "bad_now": snap["bad_now"],
            "recv_now": snap["recv_now"], "confirmed": snap["confirmed_90d"] >= 1, "at": ts})
    return snap


async def add_bad_event(tenant: str, vpa: str, weight: float, kind: str, ts: float, ref: str, cause: str) -> dict:
    store_kind = "report" if kind == "flagged_message" else kind
    await rt.store.bad_event(tenant=tenant, vpa=vpa, weight=weight, kind=store_kind, ts=ts, ref=ref)
    await db.execute("INSERT INTO vpa_events(tenant_id,vpa_id,kind,weight,ref,at) VALUES($1,$2,$3,$4,$5,$6)",
                     tenant, vpa, kind, weight, ref, dt(ts))
    return await persist_reputation(tenant, vpa, ts, cause)


async def rep_on_outcome(ev: dict) -> None:
    ts = ev.get("at") or time.time()
    tenant, vpa = ev["tenant_id"], ev["payee_vpa"]
    if ev.get("final_status") == "SUCCESS":
        await rt.store.payment_success(tenant=tenant, user_id=ev["user_id"], payee=vpa, amount=float(ev["amount"]),
                                       ts=ts, device_id=ev.get("device_id"), debit=not ev.get("debited"))
        await db.execute("UPDATE users SET balance = balance - $2 WHERE user_id=$1", _uuid(ev["user_id"]), ev["amount"])
        owner = await db.fetchval("SELECT owner_user_id FROM vpas WHERE vpa_id=$1", vpa)
        if owner:
            await db.execute("UPDATE users SET balance = balance + $2 WHERE user_id=$1", owner, ev["amount"])
            await rt.store.r.hincrbyfloat(f"user_prof:{owner}", "balance", float(ev["amount"]))
        if ev.get("device_id"):
            await db.execute(
                """INSERT INTO devices(device_id,user_id,fingerprint,first_seen,last_seen) VALUES($1,$2,$3,$4,$4)
                   ON CONFLICT (device_id) DO UPDATE SET last_seen=EXCLUDED.last_seen""",
                _uuid(ev["device_id"]), _uuid(ev["user_id"]), f"fp-{str(ev['device_id'])[:12]}", dt(ts))
        await persist_reputation(tenant, vpa, ts, "payment")
    weight = rep.near_miss_weight(ev["user_action"]) if (ev.get("enforced", True)
                                                          and ev.get("tier") in NEAR_MISS_TIERS) else 0.0
    if weight > 0:
        # One payer lowers one payee's reputation at most once a day. Without this, a single person could
        # run a UPI ID down to Caution by starting and abandoning flagged payments to it over and over.
        first = await rt.store.r.set(f"nm:{tenant}:{vpa}:{ev['user_id']}", ev["decision_id"], nx=True, ex=86400)
        if first:
            await add_bad_event(tenant, vpa, weight, "nearmiss", ts, ev["decision_id"], "near_miss")


async def rep_on_report(ev: dict) -> None:
    """type: user_report (weight 1, if credible) | confirmed (analyst label or confirmed report, weight 5)."""
    ts = ev.get("at") or time.time()
    tenant, vpa = ev["tenant_id"], ev.get("payee_vpa")
    if not vpa:
        return
    if ev["type"] == "confirmed":
        await add_bad_event(tenant, vpa, rep.W_CONFIRMED, "confirmed", ts, ev.get("ref") or uuid.uuid4().hex,
                            "confirmed_fraud")
    elif ev["type"] == "user_report":
        reporter = ev.get("reporter_id")
        # credible only from someone who paid, or tried to pay, that UPI ID; one per reporter; five a day
        touched = await db.fetchval(
            "SELECT 1 FROM risk_decisions WHERE user_id=$1 AND payee_vpa=$2 UNION ALL "
            "SELECT 1 FROM transactions WHERE user_id=$1 AND payee_vpa=$2 LIMIT 1", _uuid(reporter), vpa)
        already = await db.fetchval(
            "SELECT count(*) FROM fraud_reports WHERE reporter_id=$1 AND payee_vpa=$2 AND kind='FRAUD'",
            _uuid(reporter), vpa)
        today = await db.fetchval(
            "SELECT count(*) FROM fraud_reports WHERE reporter_id=$1 AND kind='FRAUD' AND created_at > now() - interval '1 day'",
            _uuid(reporter))
        if touched and already <= 1 and today <= 5:
            await add_bad_event(tenant, vpa, rep.W_REPORT, "report", ts, ev.get("ref") or uuid.uuid4().hex, "report")


async def rep_on_message(ev: dict) -> None:
    """Indicators named in flagged messages start to count once two different users have flagged them."""
    if not ev.get("flag_set"):
        return
    tenant, user, ts = ev["tenant_id"], _uuid(ev["user_id"]), ev.get("at") or time.time()
    for kind, values in (("vpa", ev["entities"].get("vpas", [])), ("phone", ev["entities"].get("phones", [])),
                         ("url", ev["entities"].get("urls", []))):
        for value in values:
            new = await db.fetchval(
                """INSERT INTO flagged_indicator_reporters(tenant_id,indicator_type,value,user_id) VALUES($1,$2,$3,$4)
                   ON CONFLICT DO NOTHING RETURNING 1""", tenant, kind, value, user)
            if not new:
                continue
            n = await db.fetchval(
                """INSERT INTO flagged_indicators(tenant_id,indicator_type,value,reporter_count,first_seen,last_seen)
                   VALUES($1,$2,$3,1,$4,$4)
                   ON CONFLICT (tenant_id,indicator_type,value) DO UPDATE
                     SET reporter_count = flagged_indicators.reporter_count + 1, last_seen = EXCLUDED.last_seen
                   RETURNING reporter_count""", tenant, kind, value, dt(ts))
            if kind == "vpa" and n == 2:
                if await db.fetchval("SELECT 1 FROM vpas WHERE vpa_id=$1", value):
                    await add_bad_event(tenant, value, rep.W_FLAGGED_MESSAGE, "flagged_message", ts,
                                        f"msg-{ev['check_id']}", "flagged_message")
            elif kind == "phone" and n in (2, 5):
                risk = 1 if n == 2 else 2
                await db.execute(
                    """INSERT INTO caller_risk_list(caller_hash,risk,source,updated_at) VALUES($1,$2,'flagged_messages',$3)
                       ON CONFLICT (caller_hash) DO UPDATE SET risk=GREATEST(caller_risk_list.risk, EXCLUDED.risk),
                         updated_at=EXCLUDED.updated_at""", value, risk, dt(ts))
                cur = int(await rt.store.r.get(f"caller_risk:{value}") or 0)
                await rt.store.r.set(f"caller_risk:{value}", max(cur, risk))


# ------------------------------------------------------------------ consortium --
async def consortium_on_reputation(ev: dict) -> None:
    """Publish this bank's view to the others once it is solid enough (docs/06, section 13)."""
    tenant, vpa = ev["tenant_id"], ev["vpa"]
    if not (ev.get("bad_now", 0) >= 2 and (ev.get("confirmed") or ev.get("new_band") in ("CAUTION", "REPORTED"))):
        return
    token = vpa_token(vpa)
    await db.execute(
        """INSERT INTO consortium_signals(vpa_token,source_tenant,bad_decayed,recv_decayed,p_bad,band,confirmed,updated_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8)
           ON CONFLICT (vpa_token,source_tenant) DO UPDATE SET bad_decayed=EXCLUDED.bad_decayed,
             recv_decayed=EXCLUDED.recv_decayed, p_bad=EXCLUDED.p_bad, band=EXCLUDED.band, confirmed=EXCLUDED.confirmed,
             updated_at=EXCLUDED.updated_at""",
        token, tenant, ev["bad_now"], ev.get("recv_now", 0.0), ev["p_bad"], ev["new_band"], bool(ev.get("confirmed")),
        dt(ev.get("at") or time.time()))
    for other in [t["tenant_id"] for t in await db.fetch("SELECT tenant_id FROM tenants WHERE tenant_id<>$1", tenant)]:
        await rt.store.put_consortium(other, token, p_bad=ev["p_bad"], source_tenant=tenant,
                                      confirmed=bool(ev.get("confirmed")))


async def notify_reputation(ev: dict) -> None:
    await emit_analyst(ev["tenant_id"], "reputation.updated", {
        "vpa": ev["vpa"], "old_band": ev["old_band"], "new_band": ev["new_band"], "old_score": ev["old_score"],
        "new_score": ev["new_score"], "cause": ev["cause"]})
