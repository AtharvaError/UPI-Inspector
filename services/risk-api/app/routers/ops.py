"""Policy lab (F-19), model and system health (F-15, F-26), audit log, and account administration (F-30)."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from .. import db
from ..bus import bus
from ..common import audit, clean, staff_scope, to_uuid
from ..core import warm
from ..core.scoring import ModelBundle, active_model_dir
from ..runtime import rt
from ..posture import checks as posture_checks
from ..realtime import drop_account
from ..security import (ApiError, Principal, client_ip, forwarded, hash_secret_async, ip_class, phone_hash,
                        service_name, weak_password)
from ..sessions import bump_generation
from ..workers import jobs

router = APIRouter(tags=["ops"])


# ------------------------------------------------------------------- policy lab --
class Thresholds(BaseModel):
    t_warning: float = Field(gt=0, lt=1)
    t_stepup: float = Field(gt=0, lt=1)
    t_block: float = Field(gt=0, lt=1)


class Simulate(Thresholds):
    hours: float = Field(default=15 * 24, gt=0, le=24 * 120)
    fraud_rate: float = Field(default=0.0005, gt=0, lt=0.5)


def _check(t: Thresholds, t_decline: float) -> None:
    if not (0 < t.t_warning < t.t_stepup < t.t_block < t_decline <= 1):
        raise ApiError(400, "VALIDATION_ERROR",
                       f"Thresholds must satisfy 0 < warning < step-up < block < decline ({t_decline})")


def policy_metrics(score: np.ndarray, y: np.ndarray, amount: np.ndarray, tw: float, ts: float, tb: float,
                   pi: float) -> dict:
    """Docs/06, section 18. Re-buckets stored final scores; rule floors and caps are already inside them."""
    fraud, legit = score[y == 1], score[y == 0]
    tpr = float((fraud >= ts).mean()) if len(fraud) else 0.0
    fpr = float((legit >= ts).mean()) if len(legit) else 0.0
    any_fraud = float((fraud >= tw).mean()) if len(fraud) else 0.0
    any_legit = float((legit >= tw).mean()) if len(legit) else 0.0
    return {
        "scams_caught": tpr,
        "scams_warned_only": float(((fraud >= tw) & (fraud < ts)).mean()) if len(fraud) else 0.0,
        "scams_missed": float((fraud < tw).mean()) if len(fraud) else 0.0,
        "legit_warning": float(((legit >= tw) & (legit < ts)).mean()) if len(legit) else 0.0,
        "legit_stepup": float(((legit >= ts) & (legit < tb)).mean()) if len(legit) else 0.0,
        "legit_block": float((legit >= tb).mean()) if len(legit) else 0.0,
        "interruptions_per_1000": 1000.0 * (pi * any_fraud + (1 - pi) * any_legit),
        "rupees_protected": float(amount[(y == 1) & (score >= ts)].sum()),
        "rupees_at_risk": float(amount[y == 1].sum()),
        "precision_stepup_plus": (tpr * pi) / max(1e-12, tpr * pi + fpr * (1 - pi)),
    }


async def _policy(tenant: str) -> dict:
    row = await db.fetchrow("SELECT * FROM policy_config WHERE tenant_id=$1", tenant)
    if not row:
        raise ApiError(404, "NOT_FOUND", "No policy for this bank")
    return dict(row)


@router.get("/v1/policy")
async def get_policy(p: Principal = Depends(staff_scope())) -> dict:
    d = clean(await _policy(p.tenant_id))
    d["demo_mode"] = rt.settings.demo_mode
    d["effective"] = {"hold_seconds": rt.demo_seconds("hold_seconds", d),
                      "cooloff_seconds": rt.demo_seconds("cooloff_seconds", d),
                      "guardian_timeout_s": rt.demo_seconds("guardian_timeout_s", d)}
    return d


@router.put("/v1/policy/mode")
async def set_mode(body: dict, p: Principal = Depends(staff_scope("admin"))) -> dict:
    mode = str(body.get("mode", ""))
    if mode not in ("enforce", "shadow"):
        raise ApiError(400, "VALIDATION_ERROR", "mode must be enforce or shadow")
    old = (await _policy(p.tenant_id))["mode"]
    await db.execute("UPDATE policy_config SET mode=$2, updated_by=$3, updated_at=now() WHERE tenant_id=$1",
                     p.tenant_id, mode, to_uuid(p.sub))
    await warm.load_policy(p.tenant_id)
    await audit(p, "policy.mode.update", p.tenant_id, {"from": old, "to": mode})
    return await get_policy(p)


@router.put("/v1/policy/thresholds")
async def set_thresholds(body: Thresholds, p: Principal = Depends(staff_scope("admin"))) -> dict:
    cur = await _policy(p.tenant_id)
    _check(body, cur["t_decline"])
    await db.execute(
        "UPDATE policy_config SET t_warning=$2, t_stepup=$3, t_block=$4, updated_by=$5, updated_at=now() WHERE tenant_id=$1",
        p.tenant_id, body.t_warning, body.t_stepup, body.t_block, to_uuid(p.sub))
    await warm.load_policy(p.tenant_id)
    await audit(p, "policy.thresholds.update", p.tenant_id, {
        "from": {k: cur[k] for k in ("t_warning", "t_stepup", "t_block")}, "to": body.model_dump()})
    return await get_policy(p)


@router.put("/v1/policy/block-mode")
async def set_block_mode(body: dict, p: Principal = Depends(staff_scope("admin"))) -> dict:
    mode = str(body.get("block_mode", ""))
    if mode not in ("hold", "decline"):
        raise ApiError(400, "VALIDATION_ERROR", "block_mode must be hold or decline")
    await db.execute("UPDATE policy_config SET block_mode=$2, updated_by=$3, updated_at=now() WHERE tenant_id=$1",
                     p.tenant_id, mode, to_uuid(p.sub))
    await warm.load_policy(p.tenant_id)
    await audit(p, "policy.block_mode.update", p.tenant_id, {"to": mode})
    return await get_policy(p)


@router.post("/v1/policy/simulate")
async def simulate(body: Simulate, p: Principal = Depends(staff_scope())) -> dict:
    cur = await _policy(p.tenant_id)
    _check(body, cur["t_decline"])
    rows = await db.fetch(
        """SELECT d.score, d.amount, COALESCE(l.label = 'FRAUD', t.truth_is_fraud) AS fraud
           FROM risk_decisions d JOIN transactions t ON t.txn_id=d.txn_id
           LEFT JOIN decision_labels l ON l.decision_id=d.decision_id
           WHERE d.tenant_id=$1 AND d.created_at > $2 AND COALESCE(l.label = 'FRAUD', t.truth_is_fraud) IS NOT NULL""",
        p.tenant_id, datetime.now(timezone.utc) - timedelta(hours=body.hours))
    if len(rows) < 50:
        raise ApiError(409, "CONFLICT", "Not enough labelled decisions in this window to simulate")
    score = np.array([r["score"] for r in rows], dtype=float)
    amount = np.array([float(r["amount"]) for r in rows], dtype=float)
    y = np.array([1 if r["fraud"] else 0 for r in rows])
    pi = body.fraud_rate
    proposed = policy_metrics(score, y, amount, body.t_warning, body.t_stepup, body.t_block, pi)
    current = policy_metrics(score, y, amount, cur["t_warning"], cur["t_stepup"], cur["t_block"], pi)
    fraud, legit = score[y == 1], score[y == 0]
    curve = []
    for t2 in np.round(np.arange(0.05, 0.96, 0.05), 2):
        curve.append({"t_stepup": float(t2), "scams_caught": float((fraud >= t2).mean()) if len(fraud) else 0.0,
                      "legit_interrupted": float((legit >= t2).mean()) if len(legit) else 0.0})
    hist, edges = np.histogram(score, bins=20, range=(0, 1))
    hist_f, _ = np.histogram(fraud, bins=20, range=(0, 1))
    return {"n_decisions": int(len(rows)), "n_fraud": int(y.sum()), "fraud_rate": pi, "proposed": proposed,
            "current": current, "current_thresholds": {k: cur[k] for k in ("t_warning", "t_stepup", "t_block", "t_decline")},
            "curve": curve,
            "histogram": [{"lo": float(edges[i]), "hi": float(edges[i + 1]), "legit": int(hist[i] - hist_f[i]),
                           "fraud": int(hist_f[i])} for i in range(20)]}


# ------------------------------------------------------------------- health --
@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "uptime_s": round(time.time() - rt.metrics.started)}


@router.get("/readyz")
async def readyz(response: Response) -> dict:
    problems = []
    if not rt.ready:
        problems.append("starting")
    if not (rt.bundle and rt.bundle.available):
        problems.append("no scoring model: rules-only decisions")
    if rt.text is None:
        problems.append("no text model")
    if rt.chaos["cache_down"]:
        problems.append("cache unavailable")
    if problems:
        response.status_code = 503 if not rt.ready else 200
    return {"ready": rt.ready, "degraded": problems, "model_version": rt.model_version,
            "rules_version": rt.engine.version if rt.engine else None}


@router.get("/metrics")
async def prometheus(request: Request) -> Response:
    # Counters for a monitoring system on the same machine or network. They say how busy the service is and
    # how often it stops payments, which is nobody else's business: other callers need a service key.
    key = request.headers.get("x-api-key")
    near = ip_class(client_ip(request)) in ("loopback", "private") and not forwarded(request.headers)
    if not (near or (key and service_name(key))):
        raise ApiError(403, "FORBIDDEN", "Metrics are available on the local network or with a service key")
    m = rt.metrics
    lines = ["# TYPE upi_assess_total counter", f"upi_assess_total {m.total}",
             "# TYPE upi_assess_degraded_total counter", f"upi_assess_degraded_total {m.degraded}",
             "# TYPE upi_assess_timeouts_total counter", f"upi_assess_timeouts_total {m.timeouts}",
             "# TYPE upi_assess_shed_total counter", f"upi_assess_shed_total {m.shed}",
             "# TYPE upi_cache_fallback_total counter", f"upi_cache_fallback_total {m.fallbacks}",
             "# TYPE upi_event_backlog gauge", f"upi_event_backlog {bus().backlog()}"]
    for tier, n in m.tiers.items():
        lines.append(f'upi_decisions_total{{tier="{tier}"}} {n}')
    for action, n in m.actions.items():
        lines.append(f'upi_actions_total{{action="{action}"}} {n}')
    for stage, p in m.percentiles().items():
        for q in ("p50", "p95", "p99"):
            lines.append(f'upi_stage_latency_ms{{stage="{stage}",quantile="{q}"}} {p[q]}')
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/v1/metrics/summary")
async def metrics_summary(p: Principal = Depends(staff_scope())) -> dict:
    m = rt.metrics
    hour = await db.fetchrow(
        """SELECT count(*) AS n, count(*) FILTER (WHERE tier='SAFE') AS safe, count(*) FILTER (WHERE tier='WARNING') AS warning,
                  count(*) FILTER (WHERE tier='STEP_UP') AS step_up, count(*) FILTER (WHERE tier='BLOCK') AS block,
                  count(*) FILTER (WHERE degraded) AS degraded
           FROM risk_decisions WHERE tenant_id=$1 AND created_at > now() - interval '1 hour'""", p.tenant_id)
    day = await db.fetch(
        """SELECT date_trunc('hour', created_at) AS h, tier, count(*) AS n FROM risk_decisions
           WHERE tenant_id=$1 AND created_at > now() - interval '24 hours' GROUP BY 1,2 ORDER BY 1""", p.tenant_id)
    b = bus()
    return clean({
        "uptime_s": round(time.time() - m.started), "latency_ms": m.percentiles(), "rps_1m": m.rps(60),
        "total": m.total, "tiers": dict(m.tiers), "actions": dict(m.actions), "degraded": m.degraded,
        "timeouts": m.timeouts, "shed": m.shed, "cache_fallbacks": m.fallbacks,
        "fallback_rate": (m.fallbacks / m.total) if m.total else 0.0, "event_backlog": b.backlog(),
        "events_published": b.published, "event_handler_failures": b.failed, "active_model": rt.model_version,
        "rules_version": rt.engine.version, "text_model": rt.text.version if rt.text else None,
        "last_hour": hour, "by_hour": day, "graph_job": jobs.graph_status(), "cache_warm_seconds": rt.warm_seconds,
        "chaos": rt.chaos, "environment": {"event_bus": rt.settings.event_bus,
                                           "cache": "in-process" if rt.settings.redis_url.startswith("fakeredis") else "redis",
                                           "database": "embedded PostgreSQL" if rt.settings.database_url.startswith("pgserver") else "PostgreSQL",
                                           "demo_mode": rt.settings.demo_mode},
    })


@router.get("/v1/model-health/models")
async def models(p: Principal = Depends(staff_scope())) -> dict:
    rows = await db.fetch("SELECT * FROM model_registry ORDER BY created_at DESC LIMIT 20")
    return {"active": rt.model_version, "models": clean(rows),
            "baseline_features": list((rt.bundle.baseline or {}).keys()) if rt.bundle else []}


@router.get("/v1/model-health/drift")
async def drift(p: Principal = Depends(staff_scope()), refresh: bool = False) -> dict:
    if refresh:
        return await jobs.run_drift()
    rid = await db.fetchval("SELECT report_id FROM drift_reports ORDER BY created_at DESC LIMIT 1")
    if not rid:
        return await jobs.run_drift()
    rows = await db.fetch("SELECT feature, psi, status, window_start, window_end, created_at FROM drift_reports "
                          "WHERE report_id=$1 ORDER BY psi DESC", rid)
    mix = await db.fetch(
        """SELECT date_trunc('hour', created_at) AS h, count(*) AS n,
                  count(*) FILTER (WHERE tier<>'SAFE')::float / GREATEST(count(*),1) AS flagged_share,
                  count(*) FILTER (WHERE degraded)::float / GREATEST(count(*),1) AS degraded_share
           FROM risk_decisions WHERE created_at > now() - interval '48 hours' GROUP BY 1 ORDER BY 1""")
    return {"status": "ok", "report_id": str(rid), "created_at": clean(rows[0]["created_at"]) if rows else None,
            "features": clean([{"feature": r["feature"], "psi": r["psi"], "status": r["status"]} for r in rows]),
            "tier_mix": clean(mix)}


@router.get("/v1/model-health/fairness")
async def fairness(p: Principal = Depends(staff_scope()), refresh: bool = False) -> dict:
    if not refresh:
        rid = await db.fetchval("SELECT report_id FROM fairness_reports ORDER BY created_at DESC LIMIT 1")
        if rid:
            rows = await db.fetch("SELECT * FROM fairness_reports WHERE report_id=$1 ORDER BY dimension, segment", rid)
            dims: dict[str, list] = {}
            for r in rows:
                dims.setdefault(r["dimension"], []).append(clean({k: r[k] for k in (
                    "segment", "n_legit", "share_warning", "share_stepup", "share_block", "fpr_stepup_plus")}))
            out = []
            for dim, segs in dims.items():
                usable = [s["fpr_stepup_plus"] for s in segs if s["n_legit"] >= 200]
                ratio = (max(usable) / max(min(usable), 1e-4)) if len(usable) >= 2 and max(usable) > 0 else 1.0
                out.append({"dimension": dim, "segments": segs, "disparity_ratio": round(float(ratio), 2),
                            "flagged": bool(ratio > 1.5 and max(usable or [0]) >= 0.002)})
            return {"status": "ok", "report_id": str(rid), "created_at": clean(rows[0]["created_at"]), "dimensions": out}
    return await jobs.run_fairness()


@router.post("/v1/model-health/reload")
async def reload_model(p: Principal = Depends(staff_scope("admin"))) -> dict:
    """Pick up a newly promoted model and an edited rules file without a restart."""
    d = active_model_dir(rt.settings.model_path)
    before = rt.model_version
    if d is not None:
        rt.bundle = ModelBundle.load(d).for_serving()
    rules_changed = rt.engine.maybe_reload()
    await audit(p, "model.reload", rt.model_version, {"from": before, "rules_changed": rules_changed})
    return {"model_version": rt.model_version, "previous": before, "rules_version": rt.engine.version}


@router.get("/v1/audit")
async def audit_log(p: Principal = Depends(staff_scope("admin", "auditor")), limit: int = Query(default=100, le=500),
                    action: str | None = None) -> dict:
    rows = await db.fetch(
        "SELECT * FROM audit_log WHERE (tenant_id=$1 OR tenant_id IS NULL) AND ($2::text IS NULL OR action ILIKE $2) "
        "ORDER BY id DESC LIMIT $3", p.tenant_id, f"%{action}%" if action else None, limit)
    return {"entries": clean(rows)}


# ------------------------------------------------------------------- security --
SECURITY_ACTIONS = ("auth.%", "staff.login", "staff.demo_login", "staff.password_changed", "staff.reset_password",
                    "user.register", "caller.report", "block.%")


@router.get("/v1/security/posture")
async def security_posture(p: Principal = Depends(staff_scope("admin", "auditor"))) -> dict:
    """How this deployment is set up, and what has been happening at the front door (F-35)."""
    rows = await db.fetch(
        """SELECT action, count(*) AS n FROM audit_log
           WHERE created_at > now() - interval '24 hours' AND (tenant_id=$1 OR tenant_id IS NULL)
             AND action LIKE ANY($2::text[]) GROUP BY 1 ORDER BY 2 DESC""", p.tenant_id, list(SECURITY_ACTIONS))
    recent = await db.fetch(
        """SELECT action, actor_name, subject, detail, created_at FROM audit_log
           WHERE (tenant_id=$1 OR tenant_id IS NULL) AND action LIKE ANY($2::text[])
             AND action NOT IN ('auth.login','auth.demo_login','staff.login','staff.demo_login')
           ORDER BY id DESC LIMIT 12""", p.tenant_id, list(SECURITY_ACTIONS))
    locked_users = await db.fetchval("SELECT count(*) FROM users WHERE tenant_id=$1 AND locked_until > now()", p.tenant_id)
    locked_staff = await db.fetchval("SELECT count(*) FROM staff_users WHERE tenant_id=$1 AND locked_until > now()", p.tenant_id)
    signed_out = await db.fetchval("SELECT count(*) FROM revoked_sessions WHERE expires_at > now()")
    enrolled = await db.fetchval(
        "SELECT count(DISTINCT k.user_id) FROM device_keys k JOIN users u ON u.user_id=k.user_id WHERE u.tenant_id=$1",
        p.tenant_id)
    reports = await db.fetchval("SELECT count(*) FROM caller_reports WHERE tenant_id=$1", p.tenant_id)
    blocks = await db.fetchval(
        "SELECT count(*) FROM user_blocks b JOIN users u ON u.user_id=b.user_id WHERE u.tenant_id=$1", p.tenant_id)
    rows_ = posture_checks(rt.settings)
    worst = "fail" if any(r["status"] == "fail" for r in rows_) else "warn" if any(r["status"] == "warn" for r in rows_) else "ok"
    return {"overall": worst, "checks": rows_, "events_24h": {r["action"]: int(r["n"]) for r in rows},
            "recent": clean(recent),
            "counts": {"locked_users": int(locked_users), "locked_staff": int(locked_staff),
                       "revoked_sessions": int(signed_out), "users_with_enrolled_device": int(enrolled),
                       "caller_reports": int(reports), "never_pay_entries": int(blocks)}}


# ------------------------------------------------------------------- administration --
ROLES = ("admin", "analyst", "auditor")


class StaffCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str = Field(max_length=16)
    password: str = Field(min_length=1, max_length=200)


class StaffPatch(BaseModel):
    role: str | None = None
    active: bool | None = None
    name: str | None = Field(default=None, min_length=2, max_length=80)


def _staff(s) -> dict:
    return clean({k: s[k] for k in ("staff_id", "display_name", "email", "role", "active", "last_login_at", "created_at",
                                    "tenant_id")})


@router.get("/v1/admin/staff")
async def list_staff(p: Principal = Depends(staff_scope("admin"))) -> dict:
    rows = await db.fetch("SELECT * FROM staff_users WHERE tenant_id=$1 ORDER BY created_at", p.tenant_id)
    return {"staff": [_staff(s) for s in rows], "roles": ROLES}


@router.post("/v1/admin/staff")
async def create_staff(body: StaffCreate, p: Principal = Depends(staff_scope("admin"))) -> dict:
    if body.role not in ROLES:
        raise ApiError(400, "VALIDATION_ERROR", f"role must be one of {ROLES}")
    why = weak_password(body.password, body.email)
    if why:
        raise ApiError(400, "WEAK_PASSWORD", why)
    if await db.fetchval("SELECT 1 FROM staff_users WHERE lower(email)=lower($1)", body.email):
        raise ApiError(409, "CONFLICT", "That email address is already in use")
    sid = uuid.uuid4()
    await db.execute(
        "INSERT INTO staff_users(staff_id,tenant_id,display_name,email,password_hash,role,password_changed_at) "
        "VALUES($1,$2,$3,$4,$5,$6,now())",
        sid, p.tenant_id, body.name.strip(), body.email.strip().lower(), await hash_secret_async(body.password), body.role)
    await audit(p, "staff.create", str(sid), {"email": body.email.lower(), "role": body.role})
    return _staff(await db.fetchrow("SELECT * FROM staff_users WHERE staff_id=$1", sid))


@router.patch("/v1/admin/staff/{staff_id}")
async def patch_staff(staff_id: str, body: StaffPatch, p: Principal = Depends(staff_scope("admin"))) -> dict:
    sid = to_uuid(staff_id)
    s = await db.fetchrow("SELECT * FROM staff_users WHERE staff_id=$1 AND tenant_id=$2", sid, p.tenant_id)
    if not s:
        raise ApiError(404, "NOT_FOUND", "No such staff member")
    if staff_id == p.sub and (body.active is False or (body.role and body.role != "admin")):
        raise ApiError(409, "CONFLICT", "You cannot deactivate or demote your own account")
    if body.role is not None and body.role not in ROLES:
        raise ApiError(400, "VALIDATION_ERROR", f"role must be one of {ROLES}")
    await db.execute(
        "UPDATE staff_users SET role=COALESCE($2,role), active=COALESCE($3,active), display_name=COALESCE($4,display_name) "
        "WHERE staff_id=$1", sid, body.role, body.active, body.name)
    if body.active is False:
        await rt.store.r.set(f"deactivated:staff:{staff_id}", "1")
        await drop_account("staff", staff_id)
    elif body.active is True:
        await rt.store.r.delete(f"deactivated:staff:{staff_id}")
    if body.role is not None and body.role != s["role"]:
        # A token carries its holder's role. Refusing the old ones makes the change take effect now,
        # not when the token would have expired.
        await bump_generation("staff", staff_id)
    await audit(p, "staff.update", staff_id, body.model_dump(exclude_none=True))
    return _staff(await db.fetchrow("SELECT * FROM staff_users WHERE staff_id=$1", sid))


@router.post("/v1/admin/staff/{staff_id}/reset-password")
async def reset_password(staff_id: str, body: dict, p: Principal = Depends(staff_scope("admin"))) -> dict:
    pw = str(body.get("password", ""))
    s = await db.fetchrow("SELECT email FROM staff_users WHERE staff_id=$1 AND tenant_id=$2", to_uuid(staff_id), p.tenant_id)
    if not s:
        raise ApiError(404, "NOT_FOUND", "No such staff member")
    why = weak_password(pw, s["email"])
    if why:
        raise ApiError(400, "WEAK_PASSWORD", why)
    await db.execute("UPDATE staff_users SET password_hash=$2, failed_attempts=0, lockouts=0, locked_until=NULL, "
                     "password_changed_at=now() WHERE staff_id=$1", to_uuid(staff_id), await hash_secret_async(pw))
    await bump_generation("staff", staff_id)              # whoever was signed in with the old password is signed out
    await audit(p, "staff.reset_password", staff_id)
    return {"staff_id": staff_id, "reset": True}


@router.get("/v1/admin/users")
async def list_users(p: Principal = Depends(staff_scope("admin")), q: str = "", demo_only: bool = False,
                     limit: int = Query(default=60, le=200)) -> dict:
    rows = await db.fetch(
        """SELECT user_id, display_name, phone_last4, primary_vpa, segment, age_band, locale, active, is_demo,
                  is_synthetic, created_at FROM users
           WHERE tenant_id=$1 AND ($2='' OR display_name ILIKE $3 OR primary_vpa ILIKE $3) AND (NOT $4 OR is_demo)
           ORDER BY is_demo DESC, is_synthetic, created_at DESC LIMIT $5""",
        p.tenant_id, q.strip(), f"%{q.strip()}%", demo_only, limit)
    total = await db.fetchval("SELECT count(*) FROM users WHERE tenant_id=$1", p.tenant_id)
    return {"users": [dict(clean(r), phone=f"••••••{r['phone_last4']}") for r in rows], "total": total}


@router.patch("/v1/admin/users/{user_id}")
async def patch_user(user_id: str, body: dict, p: Principal = Depends(staff_scope("admin"))) -> dict:
    if not isinstance(body.get("active"), bool):
        raise ApiError(400, "VALIDATION_ERROR", "active must be true or false")
    n = await db.execute("UPDATE users SET active=$2 WHERE user_id=$1 AND tenant_id=$3", to_uuid(user_id),
                         body["active"], p.tenant_id)
    if n.endswith("0"):
        raise ApiError(404, "NOT_FOUND", "No such user")
    if body["active"]:
        await rt.store.r.delete(f"deactivated:user:{user_id}")
    else:
        await rt.store.r.set(f"deactivated:user:{user_id}", "1")
        await drop_account("user", user_id)
    await audit(p, "user.update", user_id, {"active": body["active"]})
    return {"user_id": user_id, "active": body["active"]}
