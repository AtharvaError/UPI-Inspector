"""Payer-facing endpoints: profile, guardians (F-17), holds (F-23), rehearsals (F-28)."""
from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db, i18n
from ..bus import bus
from ..common import audit, clean, now_utc, to_uuid
from ..core import warm
from ..orchestrator import get_snapshot, publish_outcome, save_snapshot
from ..realtime import emit_user
from ..runtime import rt
from ..security import ApiError, Principal, phone_hash, require_user

router = APIRouter(prefix="/v1", tags=["payer"])
DELAY = timedelta(hours=24)


# ------------------------------------------------------------------- profile --
class ProfilePatch(BaseModel):
    locale: str | None = None
    voice_warnings: bool | None = None
    large_text: bool | None = None


@router.get("/users/me")
async def get_me(p: Principal = Depends(require_user)) -> dict:
    u = await db.fetchrow("SELECT * FROM users WHERE user_id=$1", to_uuid(p.sub))
    if not u:
        raise ApiError(404, "NOT_FOUND", "Account not found")
    g = await db.fetchrow(
        """SELECT g.mode, g.status, g.amount_threshold, u.display_name FROM guardians g
           JOIN users u ON u.user_id=g.guardian_user_id
           WHERE g.user_id=$1 AND g.status IN ('ACTIVE','REMOVAL_PENDING','PENDING') ORDER BY g.created_at DESC LIMIT 1""",
        u["user_id"])
    wards = await db.fetchval("SELECT count(*) FROM guardians WHERE guardian_user_id=$1 AND status IN "
                              "('ACTIVE','REMOVAL_PENDING','PENDING')", u["user_id"])
    pending = await db.fetchval("SELECT count(*) FROM guardian_requests WHERE guardian_user_id=$1 AND status='PENDING'",
                                u["user_id"])
    holds = await db.fetchval("SELECT count(*) FROM held_payments WHERE user_id=$1 AND status IN ('HELD','READY')",
                              u["user_id"])
    dev = await db.fetchval("SELECT device_id FROM devices WHERE user_id=$1 ORDER BY last_seen DESC LIMIT 1", u["user_id"])
    return {"user_id": str(u["user_id"]), "name": u["display_name"], "vpa": u["primary_vpa"], "tenant_id": u["tenant_id"],
            "locale": u["locale"], "voice_warnings": u["voice_warnings"], "large_text": u["large_text"],
            "balance": float(u["balance"]), "phone_last4": u["phone_last4"], "segment": u["segment"],
            "known_device_id": str(dev) if dev else None,
            "guardian": clean(g) if g else None, "is_guardian": wards > 0, "pending_guardian_requests": pending,
            "active_holds": holds, "demo_mode": rt.settings.demo_mode}


@router.patch("/users/me")
async def patch_me(body: ProfilePatch, p: Principal = Depends(require_user)) -> dict:
    uid_ = to_uuid(p.sub)
    if body.locale is not None:
        loc = i18n.norm_locale(body.locale)
        await db.execute("UPDATE users SET locale=$2 WHERE user_id=$1", uid_, loc)
        await rt.store.r.hset(f"user_prof:{p.sub}", "locale", loc)
        if p.sub in rt.user_index:
            rt.user_index[p.sub]["locale"] = loc
    if body.voice_warnings is not None:
        await db.execute("UPDATE users SET voice_warnings=$2 WHERE user_id=$1", uid_, body.voice_warnings)
    if body.large_text is not None:
        await db.execute("UPDATE users SET large_text=$2 WHERE user_id=$1", uid_, body.large_text)
    return await get_me(p)


@router.get("/users/me/payees")
async def my_payees(p: Principal = Depends(require_user)) -> dict:
    rows = await db.fetch(
        """SELECT t.payee_vpa AS vpa, v.registered_name, v.kind, v.verified, count(*) AS payments,
                  max(t.created_at) AS last_paid, COALESCE(r.band,'NO_HISTORY') AS band
           FROM transactions t JOIN vpas v ON v.vpa_id=t.payee_vpa
           LEFT JOIN vpa_reputation r ON r.vpa_id=t.payee_vpa AND r.tenant_id=$2
           WHERE t.user_id=$1 AND t.status='SUCCESS' GROUP BY 1,2,3,4,r.band ORDER BY max(t.created_at) DESC LIMIT 30""",
        to_uuid(p.sub), p.tenant_id)
    return {"payees": clean(rows)}


@router.get("/users/me/payments")
async def my_payments(p: Principal = Depends(require_user), limit: int = 40) -> dict:
    rows = await db.fetch(
        """SELECT t.txn_id, t.payee_vpa, v.registered_name AS payee_name, t.amount, t.note, t.status, t.created_at,
                  d.decision_id, d.tier, d.action, d.scam_family, o.user_action
           FROM transactions t LEFT JOIN vpas v ON v.vpa_id=t.payee_vpa
           LEFT JOIN risk_decisions d ON d.txn_id=t.txn_id LEFT JOIN outcomes o ON o.decision_id=d.decision_id
           WHERE t.user_id=$1 ORDER BY t.created_at DESC LIMIT $2""", to_uuid(p.sub), min(max(limit, 1), 100))
    return {"payments": clean(rows)}


# ------------------------------------------------------------------- guardians --
class GuardianCreate(BaseModel):
    guardian: dict                              # {"phone": "..."} or {"vpa": "..."}
    mode: str = "notify"
    amount_threshold: float | None = Field(default=None, ge=0)


class GuardianPatch(BaseModel):
    mode: str | None = None
    amount_threshold: float | None = Field(default=None, ge=0)


def _link(g) -> dict:
    d = clean(g)
    d.pop("is_seed", None)
    return d


async def _my_link(link_id: str, p: Principal, as_guardian: bool = False) -> dict:
    g = await db.fetchrow("SELECT * FROM guardians WHERE guardian_link_id=$1", to_uuid(link_id))
    if not g:
        raise ApiError(404, "NOT_FOUND", "No such guardian link")
    owner = g["guardian_user_id"] if as_guardian else g["user_id"]
    if str(owner) != p.sub:
        raise ApiError(403, "FORBIDDEN", "Not your guardian link")
    return dict(g)


@router.get("/guardians")
async def guardians(p: Principal = Depends(require_user)) -> dict:
    me = to_uuid(p.sub)
    mine = await db.fetchrow(
        """SELECT g.*, u.display_name AS guardian_name, u.primary_vpa AS guardian_vpa FROM guardians g
           JOIN users u ON u.user_id=g.guardian_user_id
           WHERE g.user_id=$1 AND g.status IN ('ACTIVE','REMOVAL_PENDING','PENDING') ORDER BY g.created_at DESC LIMIT 1""", me)
    others = await db.fetch(
        """SELECT g.*, u.display_name AS protected_name, u.primary_vpa AS protected_vpa FROM guardians g
           JOIN users u ON u.user_id=g.user_id WHERE g.guardian_user_id=$1 AND g.status IN ('ACTIVE','REMOVAL_PENDING')
           ORDER BY g.created_at""", me)
    inv = await db.fetch(
        """SELECT g.*, u.display_name AS protected_name, u.primary_vpa AS protected_vpa FROM guardians g
           JOIN users u ON u.user_id=g.user_id WHERE g.guardian_user_id=$1 AND g.status='PENDING'""", me)
    return {"as_protected": _link(mine) if mine else None, "as_guardian": [_link(x) for x in others],
            "invitations": [_link(x) for x in inv]}


@router.post("/guardians")
async def nominate(body: GuardianCreate, p: Principal = Depends(require_user)) -> dict:
    if body.mode not in ("notify", "co_approve"):
        raise ApiError(400, "VALIDATION_ERROR", "mode must be notify or co_approve")
    me = to_uuid(p.sub)
    if await db.fetchval("SELECT 1 FROM guardians WHERE user_id=$1 AND status IN ('ACTIVE','REMOVAL_PENDING','PENDING')", me):
        raise ApiError(409, "CONFLICT", "You already have a guardian or a pending invitation")
    g = body.guardian or {}
    if g.get("phone"):
        target = await db.fetchrow("SELECT * FROM users WHERE phone_hash=$1 AND active", phone_hash(str(g["phone"])))
    elif g.get("vpa"):
        target = await db.fetchrow("SELECT * FROM users WHERE primary_vpa=$1 AND active", str(g["vpa"]).strip().lower())
    else:
        raise ApiError(400, "VALIDATION_ERROR", "Give the guardian's phone number or UPI ID")
    if not target:
        raise ApiError(404, "NOT_FOUND", "No UPI Inspector user found with those details")
    if target["user_id"] == me:
        raise ApiError(400, "VALIDATION_ERROR", "You cannot be your own guardian")
    link_id = uuid.uuid4()
    await db.execute(
        "INSERT INTO guardians(guardian_link_id,user_id,guardian_user_id,mode,amount_threshold,status) "
        "VALUES($1,$2,$3,$4,$5,'PENDING')", link_id, me, target["user_id"], body.mode,
        body.amount_threshold if body.mode == "co_approve" else None)
    await audit(p, "guardian.invite", str(link_id), {"guardian": str(target["user_id"]), "mode": body.mode})
    await emit_user(str(target["user_id"]), "guardian.link", {"guardian_link_id": str(link_id), "status": "PENDING"})
    return await guardians(p)


@router.post("/guardians/{link_id}/accept")
async def accept(link_id: str, p: Principal = Depends(require_user)) -> dict:
    g = await _my_link(link_id, p, as_guardian=True)
    if g["status"] != "PENDING":
        raise ApiError(409, "CONFLICT", "This invitation is no longer pending")
    await db.execute("UPDATE guardians SET status='ACTIVE', updated_at=now() WHERE guardian_link_id=$1", g["guardian_link_id"])
    await warm.load_guardian(str(g["user_id"]))
    await audit(p, "guardian.accept", link_id)
    await emit_user(str(g["user_id"]), "guardian.link", {"guardian_link_id": link_id, "status": "ACTIVE"})
    return await guardians(p)


@router.post("/guardians/{link_id}/decline")
async def decline(link_id: str, p: Principal = Depends(require_user)) -> dict:
    g = await _my_link(link_id, p, as_guardian=True)
    if g["status"] != "PENDING":
        raise ApiError(409, "CONFLICT", "This invitation is no longer pending")
    await db.execute("UPDATE guardians SET status='DECLINED', updated_at=now() WHERE guardian_link_id=$1", g["guardian_link_id"])
    await emit_user(str(g["user_id"]), "guardian.link", {"guardian_link_id": link_id, "status": "DECLINED"})
    return await guardians(p)


@router.patch("/guardians/{link_id}")
async def change(link_id: str, body: GuardianPatch, p: Principal = Depends(require_user)) -> dict:
    """Tightening applies at once. Loosening waits 24 hours and the guardian is told now."""
    g = await _my_link(link_id, p)
    if g["status"] != "ACTIVE":
        raise ApiError(409, "CONFLICT", "Only an active guardian link can be changed")
    if body.mode is not None and body.mode not in ("notify", "co_approve"):
        raise ApiError(400, "VALIDATION_ERROR", "mode must be notify or co_approve")
    new_mode = body.mode or g["mode"]
    old_thr = float(g["amount_threshold"] or 0)
    new_thr = body.amount_threshold if body.amount_threshold is not None else old_thr
    loosening = (g["mode"] == "co_approve" and new_mode == "notify") or (
        new_mode == "co_approve" and g["mode"] == "co_approve" and (new_thr > old_thr or (old_thr > 0 and new_thr == 0)))
    if loosening:
        await db.execute(
            "UPDATE guardians SET pending_change=$2, change_effective_at=$3, updated_at=now() WHERE guardian_link_id=$1",
            g["guardian_link_id"], {"mode": new_mode, "amount_threshold": new_thr}, now_utc() + DELAY)
        await emit_user(str(g["guardian_user_id"]), "guardian.link", {"guardian_link_id": link_id, "status": "CHANGE_PENDING"})
    else:
        await db.execute("UPDATE guardians SET mode=$2, amount_threshold=$3, updated_at=now() WHERE guardian_link_id=$1",
                         g["guardian_link_id"], new_mode, new_thr if new_mode == "co_approve" else None)
        await warm.load_guardian(p.sub)
    await audit(p, "guardian.change", link_id, {"mode": new_mode, "amount_threshold": new_thr, "delayed": loosening})
    return await guardians(p)


@router.delete("/guardians/{link_id}")
async def remove(link_id: str, p: Principal = Depends(require_user)) -> dict:
    """Removal takes 24 hours, so a scammer cannot have the protection switched off mid-call."""
    g = await _my_link(link_id, p)
    if g["status"] == "PENDING":
        await db.execute("UPDATE guardians SET status='REMOVED', updated_at=now() WHERE guardian_link_id=$1", g["guardian_link_id"])
    elif g["status"] == "ACTIVE":
        await db.execute(
            "UPDATE guardians SET status='REMOVAL_PENDING', change_effective_at=$2, updated_at=now() WHERE guardian_link_id=$1",
            g["guardian_link_id"], now_utc() + DELAY)
        await emit_user(str(g["guardian_user_id"]), "guardian.link", {"guardian_link_id": link_id, "status": "REMOVAL_PENDING"})
    else:
        raise ApiError(409, "CONFLICT", "Nothing to remove")
    await audit(p, "guardian.remove", link_id)
    return await guardians(p)


@router.post("/guardians/{link_id}/undo")
async def undo(link_id: str, p: Principal = Depends(require_user)) -> dict:
    g = await _my_link(link_id, p)
    if g["status"] not in ("REMOVAL_PENDING", "ACTIVE"):
        raise ApiError(409, "CONFLICT", "Nothing to undo")
    await db.execute("UPDATE guardians SET status='ACTIVE', change_effective_at=NULL, pending_change=NULL, updated_at=now() "
                     "WHERE guardian_link_id=$1", g["guardian_link_id"])
    await audit(p, "guardian.undo", link_id)
    return await guardians(p)


@router.get("/guardian/requests")
async def guardian_requests(p: Principal = Depends(require_user), status: str | None = None) -> dict:
    rows = await db.fetch(
        "SELECT request_id, kind, status, summary, expires_at, responded_at, created_at FROM guardian_requests "
        "WHERE guardian_user_id=$1 AND ($2::text IS NULL OR status=$2) ORDER BY created_at DESC LIMIT 50",
        to_uuid(p.sub), status)
    return {"requests": clean(rows)}


@router.get("/guardian/requests/{request_id}")
async def guardian_request(request_id: str, p: Principal = Depends(require_user)) -> dict:
    r = await db.fetchrow("SELECT * FROM guardian_requests WHERE request_id=$1", to_uuid(request_id))
    if not r:
        raw = await rt.store.r.get(f"greq:{request_id}")       # not persisted yet
        if not raw:
            raise ApiError(404, "NOT_FOUND", "No such request")
        g = json.loads(raw)
        r = {"request_id": request_id, "guardian_user_id": g["guardian_user_id"], "user_id": g["user_id"],
             "kind": g["kind"], "status": g["status"], "summary": g["summary"], "expires_at": None, "created_at": None}
    if str(r["user_id"]) == p.sub and str(r["guardian_user_id"]) != p.sub:
        return {"request_id": request_id, "status": r["status"], "expires_at": clean(r["expires_at"])}
    if str(r["guardian_user_id"]) != p.sub:
        raise ApiError(403, "FORBIDDEN", "Not your request")
    return clean({k: r[k] for k in ("request_id", "kind", "status", "summary", "expires_at", "created_at")})


@router.post("/guardian/requests/{request_id}/respond")
async def respond(request_id: str, body: dict, p: Principal = Depends(require_user)) -> dict:
    decision = str(body.get("decision", "")).lower()
    if decision not in ("approve", "deny", "dismiss"):
        raise ApiError(400, "VALIDATION_ERROR", "decision must be approve, deny or dismiss")
    r = await db.fetchrow("SELECT * FROM guardian_requests WHERE request_id=$1", to_uuid(request_id))
    if not r:
        raise ApiError(404, "NOT_FOUND", "No such request")
    if str(r["guardian_user_id"]) != p.sub:
        raise ApiError(403, "FORBIDDEN", "Not your request")
    if r["status"] != "PENDING":
        raise ApiError(409, "CONFLICT", f"This request is already {r['status'].lower()}")
    if r["kind"] == "NOTIFY" and decision != "dismiss":
        raise ApiError(400, "VALIDATION_ERROR", "This alert can only be dismissed")
    if r["kind"] == "APPROVAL" and decision == "dismiss":
        raise ApiError(400, "VALIDATION_ERROR", "Approve or deny this request")
    status = {"approve": "APPROVED", "deny": "DENIED", "dismiss": "DISMISSED"}[decision]
    await db.execute("UPDATE guardian_requests SET status=$2, responded_at=now() WHERE request_id=$1", r["request_id"], status)
    raw = await rt.store.r.get(f"greq:{request_id}")
    if raw:
        g = json.loads(raw)
        g["status"] = status
        await rt.store.r.set(f"greq:{request_id}", json.dumps(g), ex=24 * 3600)
    await audit(p, f"guardian.{decision}", request_id, {"decision_id": str(r["decision_id"])})
    if r["kind"] == "APPROVAL":
        await emit_user(str(r["user_id"]), "guardian.response", {"request_id": request_id, "status": status})
        snap = await get_snapshot(str(r["decision_id"]))
        if snap and not snap.get("paid"):
            if status == "DENIED":
                snap["action"] = "DECLINE"
                await save_snapshot(snap)
                await publish_outcome(snap, "GUARDIAN_DENIED", "NOT_ATTEMPTED")
            elif status == "APPROVED":
                snap["guardian_approved_at"] = time.time()     # the payment is cleared for a limited time from here
                await save_snapshot(snap)
    return {"request_id": request_id, "status": status}


# ------------------------------------------------------------------- holds --
@router.get("/holds")
async def holds(p: Principal = Depends(require_user)) -> dict:
    rows = await db.fetch(
        """SELECT h.*, d.reasons, d.scam_family, d.detail->>'family_variant' AS family_variant
           FROM held_payments h LEFT JOIN risk_decisions d ON d.decision_id=h.decision_id
           WHERE h.user_id=$1 ORDER BY h.created_at DESC LIMIT 30""", to_uuid(p.sub))
    out = []
    loc = i18n.norm_locale((rt.user_index.get(p.sub) or {}).get("locale"))
    for h in rows:
        d = clean(h)
        d.pop("family_variant", None)
        d["family"] = ({"code": h["scam_family"],
                        "headline": i18n.family(loc, h["scam_family"], h["family_variant"])["headline"]}
                       if h["scam_family"] else None)
        out.append(d)
    return {"holds": out, "server_time": time.time()}


async def _my_hold(hold_id: str, p: Principal) -> dict:
    h = await db.fetchrow("SELECT * FROM held_payments WHERE hold_id=$1", to_uuid(hold_id))
    if not h:                                   # the worker may not have written it yet
        raise ApiError(404, "NOT_FOUND", "No such hold")
    if str(h["user_id"]) != p.sub:
        raise ApiError(403, "FORBIDDEN", "Not your payment")
    return dict(h)


@router.post("/holds/{hold_id}/cancel")
async def cancel_hold(hold_id: str, p: Principal = Depends(require_user)) -> dict:
    h = await _my_hold(hold_id, p)
    if h["status"] not in ("HELD", "READY"):
        raise ApiError(409, "CONFLICT", f"This payment is already {h['status'].lower()}")
    await db.execute("UPDATE held_payments SET status='CANCELLED', updated_at=now() WHERE hold_id=$1", h["hold_id"])
    snap = await get_snapshot(str(h["decision_id"]))
    if snap:
        await publish_outcome(snap, "HOLD_CANCELLED", "NOT_ATTEMPTED")
    return {"hold_id": hold_id, "status": "CANCELLED"}


@router.post("/holds/{hold_id}/confirm")
async def confirm_hold(hold_id: str, p: Principal = Depends(require_user)) -> dict:
    """Nothing is sent here. The app assesses the payment again with a new txn_id (docs/02, F-23)."""
    h = await _my_hold(hold_id, p)
    if h["status"] != "READY":
        raise ApiError(409, "CONFLICT", "This payment is not ready yet" if h["status"] == "HELD"
                       else f"This payment is already {h['status'].lower()}")
    await db.execute("UPDATE held_payments SET status='CONFIRMED', updated_at=now() WHERE hold_id=$1", h["hold_id"])
    snap = await get_snapshot(str(h["decision_id"]))
    if snap:
        await publish_outcome(snap, "HOLD_CONFIRMED", "NOT_ATTEMPTED")
    return {"hold_id": hold_id, "status": "CONFIRMED", "parent_decision_id": str(h["decision_id"]),
            "payee": {"vpa": h["payee_vpa"], "registered_name": h["payee_name"], "claimed_name": h["claimed_name"]},
            "amount": float(h["amount"]), "note": h["note"]}


# ------------------------------------------------------------------- rehearsals --
@router.get("/rehearsals")
async def rehearsals(p: Principal = Depends(require_user)) -> dict:
    loc = i18n.norm_locale((rt.user_index.get(p.sub) or {}).get("locale"))
    rows = await db.fetch(
        """SELECT DISTINCT ON (code) code, locale, family, title, minutes FROM rehearsal_scripts
           WHERE locale IN ($1,'en') ORDER BY code, (locale=$1) DESC""", loc)
    done = {r["script_code"]: r for r in await db.fetch(
        "SELECT script_code, max(safe_choices) AS best, max(total_choices) AS total FROM rehearsal_runs "
        "WHERE user_id=$1 GROUP BY 1", to_uuid(p.sub))}
    return {"scripts": [{**clean(r), "family_name": i18n.family(loc, r["family"])["name"],
                         "completed": r["code"] in done,
                         "best": done[r["code"]]["best"] if r["code"] in done else None,
                         "total": done[r["code"]]["total"] if r["code"] in done else None} for r in rows]}


@router.get("/rehearsals/{code}")
async def rehearsal(code: str, p: Principal = Depends(require_user)) -> dict:
    loc = i18n.norm_locale((rt.user_index.get(p.sub) or {}).get("locale"))
    r = await db.fetchrow("SELECT * FROM rehearsal_scripts WHERE code=$1 AND locale IN ($2,'en') "
                          "ORDER BY (locale=$2) DESC LIMIT 1", code, loc)
    if not r:
        raise ApiError(404, "NOT_FOUND", "No such script")
    return clean(r)


@router.post("/rehearsals/{code}/runs")
async def rehearsal_run(code: str, body: dict, p: Principal = Depends(require_user)) -> dict:
    r = await db.fetchrow("SELECT script FROM rehearsal_scripts WHERE code=$1 LIMIT 1", code)
    if not r:
        raise ApiError(404, "NOT_FOUND", "No such script")
    nodes = r["script"]["nodes"]
    choices = body.get("choices") or []
    safe = total = 0
    for c in choices:
        node = nodes.get(str(c.get("node")))
        try:
            opt = node["options"][int(c.get("option"))]
        except (TypeError, KeyError, IndexError, ValueError):
            continue
        total += 1
        safe += int(opt.get("verdict") == "safe")
    await db.execute(
        "INSERT INTO rehearsal_runs(run_id,user_id,script_code,choices,safe_choices,total_choices) VALUES($1,$2,$3,$4,$5,$6)",
        uuid.uuid4(), to_uuid(p.sub), code, choices, safe, total)
    return {"safe_choices": safe, "total_choices": total}
