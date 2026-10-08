"""Dashboard reads and case actions (F-13), payees, the network graph (F-20), consortium signals (F-24)."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from .. import db
from ..bus import bus
from ..common import audit, clean, staff_scope, to_uuid
from ..core import warm
from ..runtime import rt
from ..security import ApiError, Principal, vpa_token
from ..workers import jobs, summariser

router = APIRouter(prefix="/v1", tags=["analyst"])
WRITE = ("admin", "analyst")


def _mask(name: str | None) -> str:
    if not name:
        return ""
    parts = name.split()
    return " ".join(w[0] + "•" * max(1, len(w) - 1) for w in parts)


@router.get("/decisions")
async def decisions(p: Principal = Depends(staff_scope()), tier: str | None = None, action: str | None = None,
                    family: str | None = None, linked: str | None = None, enforced: bool | None = None,
                    live: bool | None = None, q: str | None = None, hours: float | None = None,
                    before: str | None = None, limit: int = Query(default=50, le=200)) -> dict:
    cond, args = ["d.tenant_id=$1"], [p.tenant_id]

    def add(sql: str, v) -> None:
        args.append(v)
        cond.append(sql.replace("?", f"${len(args)}"))

    if tier:
        add("d.tier = ANY(?::text[])", tier.split(","))
    if action:
        add("d.action = ANY(?::text[])", action.split(","))
    if family:
        add("d.scam_family = ?", family)
    if enforced is not None:
        add("d.enforced = ?", enforced)
    if live is not None:
        add("d.is_synthetic = ?", not live)
    if q:
        add("(d.payee_vpa ILIKE ? OR d.txn_id ILIKE ?)".replace("?", "$X"), f"%{q.strip()}%")
        cond[-1] = cond[-1].replace("$X", f"${len(args)}")
    if hours:
        add("d.created_at > ?", datetime.now(timezone.utc) - timedelta(hours=hours))
    if before:
        add("d.created_at < ?", datetime.fromisoformat(before))
    if linked in ("call", "message"):
        add("EXISTS (SELECT 1 FROM signal_links s WHERE s.decision_id=d.decision_id AND s.signal_type=?)", linked)
    args.append(limit)
    rows = await db.fetch(
        f"""SELECT d.decision_id, d.txn_id, d.created_at, d.amount, d.payee_vpa, d.score, d.tier, d.action, d.enforced,
                   d.scam_family, d.degraded, d.latency_ms, d.is_synthetic, d.reasons, d.detail->'reasons_en' AS reasons_en,
                   d.detail->'payee'->>'registered_name' AS payee_name, d.detail->>'family_en' AS family_name,
                   d.detail->'guardian' IS NOT NULL AS has_guardian, u.display_name AS payer_name,
                   o.user_action, o.is_near_miss, l.label,
                   EXISTS (SELECT 1 FROM signal_links s WHERE s.decision_id=d.decision_id AND s.signal_type='call') AS call_linked,
                   EXISTS (SELECT 1 FROM signal_links s WHERE s.decision_id=d.decision_id AND s.signal_type='message') AS message_linked
            FROM risk_decisions d LEFT JOIN users u ON u.user_id=d.user_id
            LEFT JOIN outcomes o ON o.decision_id=d.decision_id LEFT JOIN decision_labels l ON l.decision_id=d.decision_id
            WHERE {' AND '.join(cond)} ORDER BY d.created_at DESC LIMIT ${len(args)}""", *args)
    out = []
    for r in rows:
        d = clean(r)
        reasons = d.pop("reasons_en") or d["reasons"] or []
        d.pop("reasons")
        d["top_reason"] = reasons[0]["message"] if reasons else None
        d["payer"] = _mask(d.pop("payer_name"))
        out.append(d)
    return {"decisions": out, "next_before": out[-1]["created_at"] if len(out) == limit else None}


@router.get("/decisions/{decision_id}")
async def decision_detail(decision_id: str, p: Principal = Depends(staff_scope())) -> dict:
    did = to_uuid(decision_id)
    d = await db.fetchrow("SELECT * FROM risk_decisions WHERE decision_id=$1 AND tenant_id=$2", did, p.tenant_id)
    if not d:
        raise ApiError(404, "NOT_FOUND", "No such decision")
    t = await db.fetchrow("SELECT * FROM transactions WHERE txn_id=$1", d["txn_id"])
    u = await db.fetchrow("SELECT display_name, primary_vpa, segment, age_band, locale, phone_hash FROM users WHERE user_id=$1",
                          d["user_id"])
    o = await db.fetchrow("SELECT * FROM outcomes WHERE decision_id=$1", did)
    label = await db.fetchrow(
        "SELECT l.*, s.display_name AS by_name FROM decision_labels l LEFT JOIN staff_users s ON s.staff_id=l.labelled_by "
        "WHERE l.decision_id=$1", did)
    links = await db.fetch("SELECT signal_type, signal_id FROM signal_links WHERE decision_id=$1", did)
    linked = {ln["signal_id"] for ln in links}
    at = d["created_at"]
    timeline = []
    if u:
        for c in await db.fetch(
                "SELECT * FROM call_events WHERE callee_hash=$1 AND started_at BETWEEN $2 AND $3 ORDER BY started_at",
                u["phone_hash"], at - timedelta(hours=6), at + timedelta(minutes=10)):
            timeline.append({"type": "call", "id": str(c["call_id"]), "at": clean(c["started_at"]),
                             "ended_at": clean(c["ended_at"]), "caller_risk": c["caller_risk"],
                             "caller_known": c["caller_known"], "linked": c["call_id"] in linked, "source": c["source"]})
        for m in await db.fetch(
                "SELECT * FROM message_checks WHERE user_id=$1 AND created_at BETWEEN $2 AND $3 ORDER BY created_at",
                d["user_id"], at - timedelta(days=7), at + timedelta(minutes=10)):
            if m["verdict"] == "FINE" and m["check_id"] not in linked:
                continue
            timeline.append({"type": "message", "id": str(m["check_id"]), "at": clean(m["created_at"]),
                             "verdict": m["verdict"], "label": m["label"], "text": m["text"][:500],
                             "entities": m["entities"], "linked": m["check_id"] in linked})
    timeline.append({"type": "payment", "id": d["txn_id"], "at": clean(at), "amount": float(d["amount"]),
                     "tier": d["tier"], "action": d["action"], "linked": True})
    if o:
        timeline.append({"type": "outcome", "id": str(did), "at": clean(o["created_at"]), "user_action": o["user_action"],
                         "final_status": o["final_status"], "linked": True})
    timeline.sort(key=lambda e: e["at"])
    rep = await db.fetchrow("SELECT * FROM vpa_reputation WHERE tenant_id=$1 AND vpa_id=$2", p.tenant_id, d["payee_vpa"])
    graph = await db.fetchrow("SELECT * FROM graph_features WHERE vpa_id=$1", d["payee_vpa"])
    greq = await db.fetchrow("SELECT request_id, kind, status, expires_at, responded_at FROM guardian_requests "
                             "WHERE decision_id=$1", did)
    hold = await db.fetchrow("SELECT hold_id, status, release_at, confirm_by FROM held_payments WHERE decision_id=$1", did)
    chal = await db.fetchrow("SELECT status, auth_method, cooloff_until FROM stepup_challenges WHERE decision_id=$1", did)
    summ = await db.fetchrow("SELECT * FROM case_summaries WHERE decision_id=$1", did)
    det = d["detail"] or {}
    return clean({
        "decision": {k: d[k] for k in ("decision_id", "txn_id", "created_at", "amount", "payee_vpa", "score", "tier",
                                       "action", "enforced", "reason_codes", "reasons", "rules_fired", "contributions",
                                       "scam_family", "family_sim", "model_version", "rules_version", "degraded",
                                       "latency_ms", "stage_ms", "is_synthetic")},
        "reasons_en": det.get("reasons_en") or d["reasons"], "family_name": det.get("family_en"),
        "score_parts": {k: det.get(k) for k in ("p_xgb", "p_rf", "s_anom", "s_rule", "raw")},
        "rule_effects": det.get("rule_effects", []),
        "payer": {"name": _mask(u["display_name"]) if u else "", "vpa": u["primary_vpa"] if u else "",
                  "segment": u["segment"] if u else "", "age_band": u["age_band"] if u else "",
                  "locale": u["locale"] if u else ""},
        "payee": {**(det.get("payee") or {}), "claimed_name": det.get("claimed_name"),
                  "reputation": rep, "graph": graph, "consortium_source": det.get("consortium_source")},
        "transaction": {"note": t["note"] if t else None, "session": t["session"] if t else {},
                        "status": t["status"] if t else None,
                        "truth_is_fraud": t["truth_is_fraud"] if t else None,
                        "truth_family": t["truth_family"] if t else None,
                        "parent_decision_id": t["parent_decision_id"] if t else None},
        "features": d["features"], "timeline": timeline, "outcome": o, "label": label,
        "guardian": greq, "hold": hold, "step_up": chal, "summary": summ,
    })


@router.post("/decisions/{decision_id}/label")
async def label_decision(decision_id: str, body: dict, p: Principal = Depends(staff_scope(*WRITE))) -> dict:
    lab = str(body.get("label", "")).upper()
    if lab not in ("FRAUD", "LEGITIMATE"):
        raise ApiError(400, "VALIDATION_ERROR", "label must be FRAUD or LEGITIMATE")
    did = to_uuid(decision_id)
    d = await db.fetchrow("SELECT payee_vpa, user_id, tenant_id FROM risk_decisions WHERE decision_id=$1 AND tenant_id=$2",
                          did, p.tenant_id)
    if not d:
        raise ApiError(404, "NOT_FOUND", "No such decision")
    prev = await db.fetchval("SELECT label FROM decision_labels WHERE decision_id=$1", did)
    await db.execute(
        """INSERT INTO decision_labels(decision_id,label,family,note,labelled_by) VALUES($1,$2,$3,$4,$5)
           ON CONFLICT (decision_id) DO UPDATE SET label=EXCLUDED.label, family=EXCLUDED.family, note=EXCLUDED.note,
             labelled_by=EXCLUDED.labelled_by, created_at=now()""",
        did, lab, body.get("family"), body.get("note"), to_uuid(p.sub))
    await audit(p, "decision.label", decision_id, {"label": lab, "family": body.get("family")})
    if lab == "FRAUD" and prev != "FRAUD":
        await bus().publish("fraud.reports", {"tenant_id": p.tenant_id, "type": "confirmed", "payee_vpa": d["payee_vpa"],
                                              "ref": f"label-{decision_id}", "at": time.time()})
    if lab == "LEGITIMATE":            # a wrongly stopped payment may be tried again
        await db.execute("DELETE FROM pair_blocks WHERE user_id=$1 AND payee_vpa=$2", d["user_id"], d["payee_vpa"])
        await rt.store.clear_pair_block(str(d["user_id"]), d["payee_vpa"])
    return {"decision_id": decision_id, "label": lab}


@router.get("/decisions/{decision_id}/summary")
async def get_summary(decision_id: str, p: Principal = Depends(staff_scope())) -> dict:
    s = await db.fetchrow("SELECT * FROM case_summaries WHERE decision_id=$1", to_uuid(decision_id))
    if not s:
        raise ApiError(404, "NOT_FOUND", "No summary yet")
    return clean(s)


@router.post("/decisions/{decision_id}/summary/regenerate", status_code=202)
async def regenerate_summary(decision_id: str, p: Principal = Depends(staff_scope(*WRITE))) -> dict:
    res = await summariser.generate(decision_id)
    if res is None:
        raise ApiError(404, "NOT_FOUND", "No such decision")
    return res


@router.get("/near-misses")
async def near_misses(p: Principal = Depends(staff_scope()), group_by: str | None = None,
                      limit: int = Query(default=100, le=300)) -> dict:
    if group_by == "payee":
        rows = await db.fetch(
            """SELECT d.payee_vpa, d.detail->'payee'->>'registered_name' AS payee_name, count(*) AS near_misses,
                      sum(d.amount) AS amount, max(o.created_at) AS last_at, max(r.band) AS band,
                      max(r.trust_score) AS trust_score
               FROM outcomes o JOIN risk_decisions d ON d.decision_id=o.decision_id
               LEFT JOIN vpa_reputation r ON r.vpa_id=d.payee_vpa AND r.tenant_id=d.tenant_id
               WHERE o.is_near_miss AND d.tenant_id=$1 GROUP BY 1,2 ORDER BY count(*) DESC, max(o.created_at) DESC LIMIT $2""",
            p.tenant_id, limit)
        return {"payees": clean(rows)}
    rows = await db.fetch(
        """SELECT d.decision_id, o.created_at, d.amount, d.payee_vpa, d.detail->'payee'->>'registered_name' AS payee_name,
                  d.tier, d.action, d.scam_family, d.detail->>'family_en' AS family_name, o.user_action, o.near_miss_weight
           FROM outcomes o JOIN risk_decisions d ON d.decision_id=o.decision_id
           WHERE o.is_near_miss AND d.tenant_id=$1 ORDER BY o.created_at DESC LIMIT $2""", p.tenant_id, limit)
    return {"near_misses": clean(rows)}


@router.get("/reviews")
async def reviews(p: Principal = Depends(staff_scope()), status: str = "OPEN") -> dict:
    rows = await db.fetch(
        """SELECT f.*, u.display_name AS reporter_name, d.tier, d.action, d.amount, d.scam_family
           FROM fraud_reports f LEFT JOIN users u ON u.user_id=f.reporter_id
           LEFT JOIN risk_decisions d ON d.decision_id=f.decision_id
           WHERE f.tenant_id=$1 AND ($2='ALL' OR f.status=$2) ORDER BY f.created_at LIMIT 200""", p.tenant_id, status)
    out = []
    for r in rows:
        d = clean(r)
        d["reporter_name"] = _mask(d.get("reporter_name"))
        out.append(d)
    return {"reviews": out}


@router.post("/reviews/{report_id}/resolve")
async def resolve_review(report_id: str, body: dict, p: Principal = Depends(staff_scope(*WRITE))) -> dict:
    res = str(body.get("resolution", "")).upper()
    r = await db.fetchrow("SELECT * FROM fraud_reports WHERE report_id=$1 AND tenant_id=$2", to_uuid(report_id), p.tenant_id)
    if not r:
        raise ApiError(404, "NOT_FOUND", "No such report")
    allowed = ("UPHELD", "OVERTURNED") if r["kind"] == "FALSE_POSITIVE" else ("CONFIRMED", "REJECTED")
    if res not in allowed:
        raise ApiError(400, "VALIDATION_ERROR", f"resolution must be one of {allowed}")
    if r["status"] != "OPEN":
        raise ApiError(409, "CONFLICT", "This item is already resolved")
    await db.execute("UPDATE fraud_reports SET status=$2, resolved_by=$3, resolved_at=now() WHERE report_id=$1",
                     r["report_id"], res, to_uuid(p.sub))
    await audit(p, "review.resolve", report_id, {"resolution": res, "note": body.get("note")})
    if res == "OVERTURNED" and r["decision_id"]:
        d = await db.fetchrow("SELECT user_id, payee_vpa FROM risk_decisions WHERE decision_id=$1", r["decision_id"])
        if d:
            await db.execute("DELETE FROM pair_blocks WHERE user_id=$1 AND payee_vpa=$2", d["user_id"], d["payee_vpa"])
            await rt.store.clear_pair_block(str(d["user_id"]), d["payee_vpa"])
            await db.execute(
                """INSERT INTO decision_labels(decision_id,label,note,labelled_by) VALUES($1,'LEGITIMATE',$2,$3)
                   ON CONFLICT (decision_id) DO UPDATE SET label='LEGITIMATE', labelled_by=EXCLUDED.labelled_by""",
                r["decision_id"], "Overturned on review", to_uuid(p.sub))
    if res == "CONFIRMED" and r["payee_vpa"]:
        await bus().publish("fraud.reports", {"tenant_id": p.tenant_id, "type": "confirmed", "payee_vpa": r["payee_vpa"],
                                              "ref": f"report-{report_id}", "at": time.time()})
    return {"report_id": report_id, "status": res}


@router.get("/payees/{vpa}")
async def payee_detail(vpa: str, p: Principal = Depends(staff_scope())) -> dict:
    vpa = vpa.strip().lower()
    v = await db.fetchrow("SELECT * FROM vpas WHERE vpa_id=$1", vpa)
    if not v:
        raise ApiError(404, "NOT_FOUND", "No such UPI ID")
    live = await rt.store.rep_snapshot(p.tenant_id, vpa, time.time())
    if not live["exists"] and await warm.load_payee(p.tenant_id, vpa):
        live = await rt.store.rep_snapshot(p.tenant_id, vpa, time.time())
    hist = await db.fetch("SELECT at, trust_score, band, cause FROM vpa_reputation_history WHERE tenant_id=$1 AND vpa_id=$2 "
                          "ORDER BY at DESC LIMIT 60", p.tenant_id, vpa)
    events = await db.fetch("SELECT kind, weight, at FROM vpa_events WHERE tenant_id=$1 AND vpa_id=$2 ORDER BY at DESC LIMIT 40",
                            p.tenant_id, vpa)
    graph = await db.fetchrow("SELECT * FROM graph_features WHERE vpa_id=$1", vpa)
    cons = await db.fetch("SELECT source_tenant, band, p_bad, confirmed, updated_at FROM consortium_signals "
                          "WHERE vpa_token=$1 AND source_tenant<>$2", vpa_token(vpa), p.tenant_id)
    stats = await db.fetchrow(
        """SELECT count(*) AS payments, count(DISTINCT user_id) AS payers, COALESCE(sum(amount),0) AS amount,
                  min(created_at) AS first_at, max(created_at) AS last_at
           FROM transactions WHERE payee_vpa=$1 AND tenant_id=$2 AND status='SUCCESS' AND created_at > now() - interval '30 days'""",
        vpa, p.tenant_id)
    recent = await db.fetch(
        """SELECT d.decision_id, d.created_at, d.amount, d.tier, d.action, d.score, o.user_action
           FROM risk_decisions d LEFT JOIN outcomes o ON o.decision_id=d.decision_id
           WHERE d.payee_vpa=$1 AND d.tenant_id=$2 ORDER BY d.created_at DESC LIMIT 15""", vpa, p.tenant_id)
    reports = await db.fetch("SELECT report_id, kind, status, comment, created_at FROM fraud_reports "
                             "WHERE payee_vpa=$1 AND tenant_id=$2 ORDER BY created_at DESC LIMIT 20", vpa, p.tenant_id)
    return clean({
        "vpa": vpa, "registered_name": v["registered_name"], "kind": v["kind"], "verified": v["verified"],
        "on_denylist": v["on_denylist"], "home_tenant": v["tenant_id"], "first_seen": v["first_seen"],
        "reputation": {k: live[k] for k in ("band", "trust_score", "band_reason", "p_bad", "maturity", "recv_now",
                                            "bad_now", "lifetime_recv", "fan_in_1h", "fan_in_24h",
                                            "first_time_payer_ratio_24h", "reports_30d", "confirmed_90d", "near_miss_14d")},
        "history": list(reversed(hist)), "events": events, "graph": graph, "consortium": cons, "stats_30d": stats,
        "recent_decisions": recent, "reports": reports,
    })


@router.get("/payees")
async def payee_search(p: Principal = Depends(staff_scope()), q: str = "", band: str | None = None,
                       limit: int = Query(default=30, le=100)) -> dict:
    rows = await db.fetch(
        """SELECT v.vpa_id AS vpa, v.registered_name, v.kind, v.verified, COALESCE(r.band,'NO_HISTORY') AS band,
                  COALESCE(r.trust_score,45) AS trust_score, r.updated_at
           FROM vpas v LEFT JOIN vpa_reputation r ON r.vpa_id=v.vpa_id AND r.tenant_id=$1
           WHERE ($2='' OR v.vpa_id ILIKE $3 OR v.registered_name ILIKE $3) AND ($4::text IS NULL OR r.band=$4)
           ORDER BY (r.band IN ('REPORTED','CAUTION')) DESC NULLS LAST, r.updated_at DESC NULLS LAST LIMIT $5""",
        p.tenant_id, q.strip(), f"%{q.strip()}%", band, limit)
    return {"payees": clean(rows)}


@router.get("/graph/neighbourhood")
async def neighbourhood(vpa: str, hops: int = Query(default=2, ge=1, le=3), days: int = Query(default=30, ge=1, le=90),
                        min_amount: float = 0, p: Principal = Depends(staff_scope())) -> dict:
    centre = vpa.strip().lower()
    if not await db.fetchval("SELECT 1 FROM vpas WHERE vpa_id=$1", centre):
        raise ApiError(404, "NOT_FOUND", "No such UPI ID")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    nodes, frontier = {centre}, {centre}
    edges: dict[tuple, dict] = {}
    cap = 300
    for _ in range(hops):
        if not frontier or len(nodes) >= cap:
            break
        rows = await db.fetch(
            """SELECT payer_vpa, payee_vpa, sum(amount) AS amount, count(*) AS n FROM transactions
               WHERE status='SUCCESS' AND created_at > $2 AND (payer_vpa = ANY($1::text[]) OR payee_vpa = ANY($1::text[]))
               GROUP BY 1,2 HAVING sum(amount) >= $3 ORDER BY sum(amount) DESC LIMIT 600""",
            list(frontier), since, min_amount)
        nxt: set[str] = set()
        for r in rows:
            edges[(r["payer_vpa"], r["payee_vpa"], "payment")] = {
                "source": r["payer_vpa"], "target": r["payee_vpa"], "type": "payment", "amount": float(r["amount"]),
                "count": r["n"]}
            for v in (r["payer_vpa"], r["payee_vpa"]):
                if v not in nodes and len(nodes) + len(nxt) < cap:
                    nxt.add(v)
        dev = await db.fetch(
            """SELECT a.vpa_id AS a, b.vpa_id AS b FROM vpa_devices a JOIN vpa_devices b ON a.fingerprint=b.fingerprint
               AND a.vpa_id < b.vpa_id WHERE a.vpa_id = ANY($1::text[]) OR b.vpa_id = ANY($1::text[]) LIMIT 200""",
            list(frontier))
        for r in dev:
            edges[(r["a"], r["b"], "device")] = {"source": r["a"], "target": r["b"], "type": "device"}
            for v in (r["a"], r["b"]):
                if v not in nodes and len(nodes) + len(nxt) < cap:
                    nxt.add(v)
        nodes |= nxt
        frontier = nxt
    info = await db.fetch(
        """SELECT v.vpa_id, v.registered_name, v.kind, COALESCE(r.band,'NO_HISTORY') AS band, g.community_id,
                  g.downstream_hop, g.passthrough_ratio
           FROM vpas v LEFT JOIN vpa_reputation r ON r.vpa_id=v.vpa_id AND r.tenant_id=$2
           LEFT JOIN graph_features g ON g.vpa_id=v.vpa_id WHERE v.vpa_id = ANY($1::text[])""", list(nodes), p.tenant_id)
    worst = {r["vpa_id"]: r["band"] for r in await db.fetch(
        "SELECT vpa_id, band FROM vpa_reputation WHERE vpa_id = ANY($1::text[]) AND band='REPORTED'", list(nodes))}
    node_list = [{"id": r["vpa_id"], "registered_name": r["registered_name"], "kind": r["kind"],
                  "band": worst.get(r["vpa_id"], r["band"]), "community_id": r["community_id"],
                  "downstream_hop": r["downstream_hop"], "passthrough_ratio": r["passthrough_ratio"],
                  "centre": r["vpa_id"] == centre} for r in info]
    keep = {n["id"] for n in node_list}
    return {"centre": centre, "nodes": node_list,
            "edges": [e for e in edges.values() if e["source"] in keep and e["target"] in keep],
            "truncated": len(nodes) >= cap, "graph_job": jobs.graph_status()}


@router.get("/consortium/signals")
async def consortium_signals(p: Principal = Depends(staff_scope()), vpa: str | None = None) -> dict:
    if vpa:
        rows = await db.fetch("SELECT * FROM consortium_signals WHERE vpa_token=$1 AND source_tenant<>$2",
                              vpa_token(vpa.strip().lower()), p.tenant_id)
    else:
        rows = await db.fetch("SELECT * FROM consortium_signals WHERE source_tenant<>$1 ORDER BY updated_at DESC LIMIT 100",
                              p.tenant_id)
    sent = await db.fetchval("SELECT count(*) FROM consortium_signals WHERE source_tenant=$1", p.tenant_id)
    return {"received": clean(rows), "published_by_us": sent,
            "note": "Signals carry a keyed hash of the UPI ID and aggregate counters. No payer or transaction data is shared."}
