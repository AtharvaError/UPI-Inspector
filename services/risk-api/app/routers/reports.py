"""Reports, complaint packs (F-25) and the mock PSP that stands in for a bank's switch."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import db
from ..bus import bus
from ..common import audit, clean, to_uuid
from ..core import warm
from ..orchestrator import get_snapshot, publish_outcome
from ..runtime import rt
from ..security import ApiError, Principal, get_principal, limit_user, require_user
from ..services import packs
from .risk import clearance, mark_paid

router = APIRouter(tags=["reports"])


class Report(BaseModel):
    kind: str = Field(max_length=20)
    decision_id: str | None = Field(default=None, max_length=64)
    txn_id: str | None = Field(default=None, max_length=64)
    payee_vpa: str | None = Field(default=None, max_length=120)
    comment: str | None = Field(default=None, max_length=1000)


async def file_report(*, tenant: str, kind: str, reporter_id: str | None, decision_id: str | None, txn_id: str | None,
                      payee_vpa: str | None, comment: str | None) -> dict:
    d = None
    if decision_id:
        d = await db.fetchrow("SELECT decision_id, txn_id, payee_vpa, user_id FROM risk_decisions WHERE decision_id=$1",
                              to_uuid(decision_id))
    elif txn_id:
        d = await db.fetchrow("SELECT decision_id, txn_id, payee_vpa, user_id FROM risk_decisions WHERE txn_id=$1", txn_id)
    if d and reporter_id and str(d["user_id"]) != reporter_id:
        raise ApiError(403, "FORBIDDEN", "Not your payment")
    vpa = (d["payee_vpa"] if d else payee_vpa or "").strip().lower() or None
    if reporter_id and vpa:
        # One open report per person per UPI ID. Saying it again returns the report already filed, so the
        # review queue cannot be flooded and the app can be tapped twice without harm.
        prior = await db.fetchrow(
            "SELECT report_id, status FROM fraud_reports WHERE reporter_id=$1 AND payee_vpa=$2 AND kind=$3 "
            "AND created_at > now() - interval '30 days' ORDER BY created_at DESC LIMIT 1",
            to_uuid(reporter_id), vpa, kind)
        if prior:
            return {"report_id": str(prior["report_id"]), "status": prior["status"], "duplicate": True}
    report_id = uuid.uuid4()
    await db.execute(
        "INSERT INTO fraud_reports(report_id,tenant_id,kind,decision_id,txn_id,payee_vpa,reporter_id,comment) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
        report_id, tenant, kind, d["decision_id"] if d else None, d["txn_id"] if d else txn_id, vpa,
        to_uuid(reporter_id) if reporter_id else None, comment)
    if kind == "FRAUD" and vpa:
        await bus().publish("fraud.reports", {"tenant_id": tenant, "type": "user_report", "payee_vpa": vpa,
                                              "reporter_id": reporter_id, "ref": str(report_id), "at": time.time()})
    return {"report_id": str(report_id), "status": "OPEN"}


@router.post("/v1/reports", dependencies=[Depends(limit_user("report", 10, 86400, "You have filed many reports today. "
                                                           "For anything urgent call 1930."))])
async def create_report(body: Report, p: Principal = Depends(require_user)) -> dict:
    if body.kind not in ("FRAUD", "FALSE_POSITIVE"):
        raise ApiError(400, "VALIDATION_ERROR", "kind must be FRAUD or FALSE_POSITIVE")
    return await file_report(tenant=p.tenant_id, kind=body.kind, reporter_id=p.sub, decision_id=body.decision_id,
                             txn_id=body.txn_id, payee_vpa=body.payee_vpa, comment=body.comment)


class PackRequest(BaseModel):
    decision_id: str | None = Field(default=None, max_length=64)
    txn_id: str | None = Field(default=None, max_length=64)
    check_id: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1000)


@router.post("/v1/complaint-packs", dependencies=[Depends(limit_user("pack", 10, 3600))])
async def create_pack(body: PackRequest, p: Principal = Depends(get_principal)) -> dict:
    if p.kind == "staff" and p.role == "auditor":
        raise ApiError(403, "FORBIDDEN", "Auditors have read-only access")
    if p.kind not in ("user", "staff"):
        raise ApiError(403, "FORBIDDEN", "Sign in to create a pack")
    res = await packs.create_pack(decision_id=body.decision_id, txn_id=body.txn_id, check_id=body.check_id,
                                  description=body.description, requested_by=p.actor,
                                  owner_user_id=p.sub if p.kind == "user" else None)
    refs, pack_id = res["refs"], res["payload"]["pack_id"]
    if p.kind == "staff" and p.role != "admin" and refs.get("tenant_id") not in (None, p.tenant_id):
        # a pack is about one bank's customer; staff of another bank have no business making one
        await db.execute("DELETE FROM complaint_packs WHERE pack_id=$1", to_uuid(pack_id))
        raise ApiError(403, "FORBIDDEN", "That payment belongs to another bank")
    if p.kind == "user":            # creating a pack is also a fraud report
        vpa = (res["payload"].get("transaction") or {}).get("payee_upi_id")
        try:
            await file_report(tenant=p.tenant_id, kind="FRAUD", reporter_id=p.sub, decision_id=refs["decision_id"],
                              txn_id=refs["txn_id"], payee_vpa=vpa, comment=body.description or "Complaint pack created")
        except ApiError:
            pass
    else:
        await audit(p, "pack.create", pack_id, {"decision_id": refs["decision_id"]})
    base = rt.settings.public_base_url.rstrip("/")
    return {"pack_id": pack_id, "pdf_url": f"/v1/complaint-packs/{pack_id}.pdf",
            "json_url": f"/v1/complaint-packs/{pack_id}.json", "base_url": base, "payload": res["payload"]}


async def _pack(pack_id: str, p: Principal):
    row = await db.fetchrow("SELECT * FROM complaint_packs WHERE pack_id=$1", to_uuid(pack_id))
    if not row:
        raise ApiError(404, "NOT_FOUND", "No such pack")
    if p.kind == "user" and row["requested_by"] != p.actor:
        raise ApiError(403, "FORBIDDEN", "Not your pack")
    if p.kind == "staff" and p.role != "admin":
        tenant = await db.fetchval(
            """SELECT COALESCE((SELECT tenant_id FROM risk_decisions WHERE decision_id=$1),
                               (SELECT tenant_id FROM transactions WHERE txn_id=$2),
                               (SELECT tenant_id FROM message_checks WHERE check_id=$3))""",
            row["decision_id"], row["txn_id"], row["check_id"])
        if tenant and tenant != p.tenant_id:
            raise ApiError(403, "FORBIDDEN", "That pack belongs to another bank")
    return row


@router.get("/v1/complaint-packs/{pack_id}.pdf")
async def pack_pdf(pack_id: str, p: Principal = Depends(get_principal)):
    row = await _pack(pack_id, p)
    return FileResponse(row["pdf_path"], media_type="application/pdf", filename=f"complaint-pack-{pack_id[:8]}.pdf")


@router.get("/v1/complaint-packs/{pack_id}.json")
async def pack_json(pack_id: str, p: Principal = Depends(get_principal)):
    row = await _pack(pack_id, p)
    return JSONResponse(clean(row["payload"]))


# ------------------------------------------------------------------- mock PSP --
class Pay(BaseModel):
    txn_id: str = Field(min_length=6, max_length=64)
    decision_id: str = Field(min_length=8, max_length=64)
    # Stands in for the UPI PIN. A real PIN is typed into the bank's own keypad and never reaches this
    # service; the app sends placeholder digits, and nothing here stores or logs the field.
    pin: str = Field(min_length=4, max_length=6, pattern=r"^\d+$")


@router.get("/mock-psp/v1/resolve/{vpa}", dependencies=[Depends(limit_user("resolve", 240, 60))])
async def resolve(vpa: str, p: Principal = Depends(get_principal)) -> dict:
    vpa = vpa.strip().lower()
    d = await rt.store.r.hgetall(f"vpa_dir:{vpa}")
    if not d and await warm.load_payee(p.tenant_id, vpa):
        d = await rt.store.r.hgetall(f"vpa_dir:{vpa}")
    if not d:
        return {"vpa": vpa, "exists": False}
    return {"vpa": vpa, "exists": True, "registered_name": d["registered_name"], "kind": d["kind"],
            "verified": d.get("verified") == "1"}


@router.post("/mock-psp/v1/pay", dependencies=[Depends(limit_user("pay", 30, 60))])
async def pay(body: Pay, p: Principal = Depends(require_user)) -> dict:
    """Pretends to execute the payment. It refuses unless the risk decision clears it:
    the integration pattern a real PSP would follow. The amount and the payee come from the decision,
    never from this request, so what is paid is exactly what was checked."""
    snap = await get_snapshot(body.decision_id)
    if not snap or snap["txn_id"] != body.txn_id:
        raise ApiError(404, "NOT_FOUND", "Unknown payment")
    if snap["user_id"] != p.sub:
        raise ApiError(403, "FORBIDDEN", "Not your payment")
    r = rt.store.r
    # One payment per decision, even if the request arrives twice at the same moment.
    lock = f"paying:{body.decision_id}"
    if not await r.set(lock, "1", nx=True, ex=30):
        return {"status": "REFUSED", "reason": "already being processed"}
    try:
        c = await clearance(body.decision_id)
        if not c["cleared"]:
            return {"status": "REFUSED", "reason": c["why"], "expired": bool(c.get("expired"))}
        # Take the money and check the result in one step, so two payments cannot both spend the same balance.
        amount = float(snap["amount"])
        left = float(await r.hincrbyfloat(f"user_prof:{p.sub}", "balance", -amount))
        if left < 0:
            await r.hincrbyfloat(f"user_prof:{p.sub}", "balance", amount)
            return {"status": "REFUSED", "reason": "insufficient balance"}
        await mark_paid(body.decision_id)
    finally:
        await r.delete(lock)
    action = ("SHADOW_ALLOWED" if not snap["enforced"] else "STEPUP_PASSED" if snap["action"] == "STEP_UP"
              else "PROCEEDED")
    await publish_outcome(snap, action, "SUCCESS", debited=True)
    return {"status": "SUCCESS", "reference": f"UPI{int(time.time() * 1000) % 10 ** 12:012d}",
            "amount": snap["amount"], "payee_vpa": snap["payee_vpa"], "payee_name": snap.get("payee_name")}
