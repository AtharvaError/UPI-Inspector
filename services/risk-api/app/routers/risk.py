"""Hot-path endpoints: assess, outcome, clearance, and the pre-payment reputation chip."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import i18n
from ..core import warm
from ..core.features import AssessInput
from ..core.textcheck import is_institutional, name_match
from ..orchestrator import assess, get_snapshot, publish_outcome, save_snapshot
from ..runtime import rt
from ..security import (ApiError, Principal, get_principal, limit_user, require_service, require_user_or_service)

router = APIRouter(prefix="/v1", tags=["risk"])


class Payer(BaseModel):
    user_id: str = Field(min_length=8, max_length=64)
    vpa: str = Field(min_length=3, max_length=120)
    device_id: str | None = Field(default=None, max_length=64)


class Payee(BaseModel):
    vpa: str = Field(min_length=3, max_length=120)
    claimed_name: str | None = Field(default=None, max_length=120)


class Session(BaseModel):
    """What the app observed while the payment was being set up. It comes from the payer's own phone, so each
    value is bounded: a client cannot send something absurd to move the score."""
    vpa_entry_method: str = Field(default="typed", max_length=24)
    app_switch_count: float = Field(default=0, ge=0, le=200)
    time_on_pay_screen_s: float = Field(default=30, ge=0, le=86400)
    amount_edit_count: float = Field(default=0, ge=0, le=200)
    flow_started_at: str | None = Field(default=None, max_length=40)


class AssessRequest(BaseModel):
    txn_id: str = Field(min_length=6, max_length=64)
    payer: Payer
    payee: Payee
    amount: float = Field(gt=0, le=1e7)
    currency: str = "INR"
    note: str | None = Field(default=None, max_length=200)
    session: Session | None = None
    parent_decision_id: str | None = Field(default=None, max_length=64)
    client_ts: str | None = Field(default=None, max_length=40)
    locale: str | None = Field(default=None, max_length=16)


class OutcomeRequest(BaseModel):
    decision_id: str = Field(min_length=8, max_length=64)
    user_action: str = Field(max_length=32)
    final_status: str | None = Field(default="NOT_ATTEMPTED", max_length=32)


CLIENT_ACTIONS = {"PROCEEDED", "ABORTED", "STEPUP_ABANDONED"}


@router.post("/risk/assess", dependencies=[Depends(limit_user("assess", 120, 60))])
async def risk_assess(body: AssessRequest, p: Principal = Depends(require_user_or_service)) -> dict:
    if p.kind == "user":
        if body.payer.user_id != p.sub:
            raise ApiError(403, "FORBIDDEN", "You can only pay from your own account")
        tenant = p.tenant_id
    else:
        ui = rt.user_index.get(body.payer.user_id)
        if ui is None and await warm.load_user(body.payer.user_id):
            ui = rt.user_index.get(body.payer.user_id)
        if ui is None:
            raise ApiError(404, "NOT_FOUND", "Unknown payer")
        tenant = ui["tenant"]
    payee = body.payee.vpa.strip().lower()
    if payee == body.payer.vpa.strip().lower():
        raise ApiError(400, "VALIDATION_ERROR", "You cannot pay your own UPI ID")
    inp = AssessInput(
        txn_id=body.txn_id, tenant=tenant, user_id=body.payer.user_id, payer_vpa=body.payer.vpa.strip().lower(),
        payee_vpa=payee, amount=float(body.amount), device_id=body.payer.device_id,
        claimed_name=(body.payee.claimed_name or "").strip() or None, note=(body.note or "").strip() or None,
        session=body.session.model_dump() if body.session else {}, parent_decision_id=body.parent_decision_id,
        locale=i18n.norm_locale(body.locale) if body.locale else "")
    return await assess(inp)


@router.post("/risk/outcome", status_code=202)
async def risk_outcome(body: OutcomeRequest, p: Principal = Depends(require_user_or_service)) -> dict:
    if body.user_action not in CLIENT_ACTIONS:
        raise ApiError(400, "VALIDATION_ERROR", f"user_action must be one of {sorted(CLIENT_ACTIONS)}")
    snap = await get_snapshot(body.decision_id)
    if not snap:
        raise ApiError(404, "NOT_FOUND", "Unknown decision")
    if p.kind == "user" and snap["user_id"] != p.sub:
        raise ApiError(403, "FORBIDDEN", "Not your payment")
    if snap.get("paid"):
        raise ApiError(409, "CONFLICT", "This payment has already completed")
    # The app reports what the payer did, never that money moved: only the PSP says a payment succeeded.
    status = body.final_status or "NOT_ATTEMPTED"
    if p.kind == "user" and status == "SUCCESS":
        raise ApiError(403, "FORBIDDEN", "Only the payment service can report a completed payment")
    await publish_outcome(snap, body.user_action, status)
    return {"accepted": True}


async def clearance(decision_id: str) -> dict:
    """May the PSP execute this payment? (docs/07, section 2.3)"""
    import json

    snap = await get_snapshot(decision_id)
    if not snap:
        return {"cleared": False, "action": None, "why": "unknown decision"}
    action = snap["action"]
    if snap.get("paid"):
        return {"cleared": False, "action": action, "why": "already paid"}
    if snap.get("user_block"):                  # the payer's own instruction; it holds in shadow mode too
        return {"cleared": False, "action": action, "why": "payee blocked by the payer"}
    # A decision describes a payment at one moment. It clears that payment for a limited time, counted from
    # the decision or from the last approval it needed; after that the payment must be checked again.
    ttl = rt.settings.clearance_ttl_s
    last = max(float(snap.get("created_at") or 0), float(snap.get("stepup_passed_at") or 0),
               float(snap.get("guardian_approved_at") or 0))
    stale = ttl > 0 and time.time() - last > ttl
    if not snap["enforced"]:
        return ({"cleared": False, "action": action, "why": "decision expired", "expired": True} if stale
                else {"cleared": True, "action": action, "why": "shadow mode"})
    if action in ("HOLD", "DECLINE"):
        return {"cleared": False, "action": action, "why": "payment was stopped"}
    why = []
    if action == "STEP_UP":
        raw = await rt.store.r.get(f"chal:{snap.get('challenge_id')}")
        chal = json.loads(raw) if raw else {}
        if chal.get("status") != "PASSED":
            return {"cleared": False, "action": action, "why": "step-up not passed"}
        why.append("step-up passed")
    if snap.get("guardian_required"):
        raw = await rt.store.r.get(f"greq:{snap.get('guardian_request_id')}")
        g = json.loads(raw) if raw else {}
        if g.get("status") != "APPROVED":
            return {"cleared": False, "action": action, "why": f"guardian approval {g.get('status', 'missing').lower()}"}
        why.append("guardian approved")
    if stale:
        return {"cleared": False, "action": action, "why": "decision expired", "expired": True}
    return {"cleared": True, "action": action, "why": "; ".join(why) or "allowed",
            "valid_for_s": int(ttl - (time.time() - last)) if ttl > 0 else None}


@router.get("/risk/clearance/{decision_id}")
async def risk_clearance(decision_id: str, p: Principal = Depends(require_service)) -> dict:
    return await clearance(decision_id)


@router.get("/payee/{vpa}/reputation", dependencies=[Depends(limit_user("reputation", 240, 60))])
async def payee_reputation(vpa: str, claimed_name: str | None = Query(default=None, max_length=120),
                           locale: str | None = None, p: Principal = Depends(get_principal)) -> dict:
    vpa = vpa.strip().lower()
    now = time.time()
    snap = await rt.store.rep_snapshot(p.tenant_id, vpa, now)
    if not snap["exists"] and await warm.load_payee(p.tenant_id, vpa):
        snap = await rt.store.rep_snapshot(p.tenant_id, vpa, now)
    loc = i18n.norm_locale(locale or (rt.user_index.get(p.sub) or {}).get("locale"))
    if not snap["exists"]:
        return {"vpa": vpa, "exists": False}
    out = {"vpa": vpa, "exists": True, "registered_name": snap["registered_name"], "kind": snap["kind"],
           "verified": snap["verified"], "band": snap["band"], "trust_score": snap["trust_score"],
           "band_reason": i18n.band_reason(loc, snap["band_reason"]), "is_self": False}
    if claimed_name and claimed_name.strip():
        score = name_match(claimed_name, snap["registered_name"])
        inst = is_institutional(claimed_name)
        out["name_match"] = {"score": score, "mismatch": score < 0.5 or (inst and snap["kind"] == "person"),
                             "institutional_claim": inst}
    if p.kind == "user":
        known = await rt.store.r.hget(f"known_payee:{p.sub}", vpa)
        out["paid_before"] = int(known.split("|")[0]) if known else 0
        out["is_self"] = (rt.user_index.get(p.sub) or {}).get("vpa") == vpa
        out["blocked"] = bool(await rt.store.r.hexists(f"user_block:{p.sub}", vpa))
    return out


async def mark_paid(decision_id: str) -> None:
    snap = await get_snapshot(decision_id)
    if snap:
        snap["paid"] = True
        await save_snapshot(snap)
