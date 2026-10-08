"""Guardian requests and timed holds: persistence, delivery and timers (F-17, F-23)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from .. import db
from ..core import warm
from ..orchestrator import get_snapshot, publish_outcome
from ..realtime import emit_user, sweep_sockets
from ..runtime import rt
from .pipeline import dt

log = logging.getLogger("workers")


async def on_guardian_event(ev: dict) -> None:
    if ev.get("type") != "request_created":
        return
    await db.execute(
        """INSERT INTO guardian_requests(request_id,guardian_link_id,guardian_user_id,user_id,decision_id,kind,status,
                                         summary,expires_at)
           VALUES($1,$2,$3,$4,$5,$6,'PENDING',$7,$8) ON CONFLICT DO NOTHING""",
        uuid.UUID(ev["request_id"]), uuid.UUID(ev["guardian_link_id"]), uuid.UUID(ev["guardian_user_id"]),
        uuid.UUID(ev["user_id"]), uuid.UUID(ev["decision_id"]), ev["kind"], ev["summary"], dt(ev.get("expires_at")))
    await emit_user(ev["guardian_user_id"], "guardian.request", {
        "request_id": ev["request_id"], "kind": ev["kind"], "protected_name": ev["summary"]["protected_name"],
        "amount": ev["summary"]["amount"], "expires_at": ev.get("expires_at")})


async def on_hold_event(ev: dict) -> None:
    if ev.get("type") != "created":
        return
    await db.execute(
        """INSERT INTO held_payments(hold_id,decision_id,txn_id,user_id,payee_vpa,payee_name,claimed_name,amount,note,
                                     release_at,confirm_by,status)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'HELD') ON CONFLICT DO NOTHING""",
        uuid.UUID(ev["hold_id"]), uuid.UUID(ev["decision_id"]), ev["txn_id"], uuid.UUID(ev["user_id"]), ev["payee_vpa"],
        ev.get("payee_name"), ev.get("claimed_name"), ev["amount"], ev.get("note"), dt(ev["release_at"]),
        dt(ev["confirm_by"]))


async def _expire_guardian_requests() -> None:
    rows = await db.fetch(
        """UPDATE guardian_requests SET status='EXPIRED' WHERE status='PENDING' AND kind='APPROVAL'
             AND expires_at IS NOT NULL AND expires_at < now() RETURNING request_id, user_id, decision_id""")
    for r in rows:
        raw = await rt.store.r.get(f"greq:{r['request_id']}")
        if raw:
            g = json.loads(raw)
            g["status"] = "EXPIRED"
            await rt.store.r.set(f"greq:{r['request_id']}", json.dumps(g), ex=3600)
        await emit_user(str(r["user_id"]), "guardian.response", {"request_id": str(r["request_id"]), "status": "EXPIRED"})
        snap = await get_snapshot(str(r["decision_id"]))
        if snap and not snap.get("paid"):
            await publish_outcome(snap, "GUARDIAN_TIMEOUT", "NOT_ATTEMPTED")


async def _apply_guardian_changes() -> None:
    """Removals and loosenings take effect 24 hours after they were asked for (docs/02, F-17)."""
    rows = await db.fetch(
        "SELECT * FROM guardians WHERE change_effective_at IS NOT NULL AND change_effective_at <= now() "
        "AND status IN ('ACTIVE','REMOVAL_PENDING')")
    for g in rows:
        if g["status"] == "REMOVAL_PENDING":
            await db.execute("UPDATE guardians SET status='REMOVED', change_effective_at=NULL, pending_change=NULL, "
                             "updated_at=now() WHERE guardian_link_id=$1", g["guardian_link_id"])
        elif g["pending_change"]:
            ch = g["pending_change"]
            await db.execute(
                "UPDATE guardians SET mode=COALESCE($2,mode), amount_threshold=COALESCE($3,amount_threshold), "
                "pending_change=NULL, change_effective_at=NULL, updated_at=now() WHERE guardian_link_id=$1",
                g["guardian_link_id"], ch.get("mode"), ch.get("amount_threshold"))
        await warm.load_guardian(str(g["user_id"]))
        await emit_user(str(g["guardian_user_id"]), "guardian.link", {"guardian_link_id": str(g["guardian_link_id"])})


async def _tick_holds() -> None:
    ready = await db.fetch(
        "UPDATE held_payments SET status='READY', updated_at=now() WHERE status='HELD' AND release_at <= now() "
        "RETURNING hold_id, user_id, payee_name, payee_vpa, amount")
    for h in ready:
        await emit_user(str(h["user_id"]), "hold.ready", {
            "hold_id": str(h["hold_id"]), "payee_name": h["payee_name"] or h["payee_vpa"], "amount": float(h["amount"])})
    expired = await db.fetch(
        "UPDATE held_payments SET status='EXPIRED', updated_at=now() WHERE status='READY' AND confirm_by < now() "
        "RETURNING decision_id")
    for h in expired:
        snap = await get_snapshot(str(h["decision_id"]))
        if snap:
            await publish_outcome(snap, "HOLD_EXPIRED", "NOT_ATTEMPTED")


async def lift_due_blocks() -> int:
    """Take a UPI ID off a never-pay list once its waiting time is over, and tell the app (F-33)."""
    rows = await db.fetch("DELETE FROM user_blocks WHERE unblock_at IS NOT NULL AND unblock_at <= now() "
                          "RETURNING user_id, payee_vpa")
    for b in rows:
        await rt.store.clear_user_block(str(b["user_id"]), b["payee_vpa"])
        await emit_user(str(b["user_id"]), "block.lifted", {"vpa": b["payee_vpa"]})
    return len(rows)


async def scheduler_loop() -> None:
    last_guardian_changes = 0.0
    while True:
        try:
            await _tick_holds()
            await _expire_guardian_requests()
            await lift_due_blocks()
            if time.time() - last_guardian_changes > 30:
                await _apply_guardian_changes()
                await sweep_sockets(time.time())           # sockets of sessions that have reached their end
                await db.execute("DELETE FROM revoked_sessions WHERE expires_at < now()")
                last_guardian_changes = time.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(3)
