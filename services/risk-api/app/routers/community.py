"""What other people's experience tells one payer: the caller warning and caller reports (F-31), looking
up a UPI ID or a number (F-32), the personal never-pay list (F-33) and this week's scam trends (F-34).

Everything shown here is an aggregate. Nobody learns who reported what, counts are given as ranges, and a
pattern is named only when several different people ran into it.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from .. import db, i18n
from ..common import clean, now_utc, to_uuid
from ..core import warm
from ..realtime import emit_user
from ..runtime import rt
from ..security import ApiError, Principal, get_principal, limit_user, phone_hash, require_user, vpa_token
from ..sessions import security_event
from .reports import file_report

router = APIRouter(prefix="/v1", tags=["community"])

VPA_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}@[a-z0-9][a-z0-9.-]{1,31}$")
MAX_BLOCKS = 200
UNBLOCK_DELAY = timedelta(hours=24)
MIN_PEOPLE = 3                      # a pattern is named only when at least this many different people met it
CALL_REPORT_DAYS = 7                # a call can be reported for this long
RISK_LABEL = {0: "NONE", 1: "SUSPECTED", 2: "LIKELY_SCAM", 3: "KNOWN_SCAM"}


def advice(locale: str, family: str) -> str:
    """General advice about a scam family. The family's `body` is written for one payment's warning screen
    ("this UPI ID was created recently..."); `tip` says the same thing with no particular payment in mind."""
    fam = i18n.family(locale, family)
    return fam.get("tip") or fam["body"]


def bucket(n: int) -> str:
    """A range instead of a count, so a small number never points at the few people behind it."""
    n = int(n or 0)
    return "0" if n <= 0 else "1-2" if n <= 2 else "3-9" if n <= 9 else "10+"


async def _me(p: Principal) -> dict:
    u = rt.user_index.get(p.sub)
    if u is None and await warm.load_user(p.sub):
        u = rt.user_index.get(p.sub)
    if u is None:
        raise ApiError(404, "NOT_FOUND", "Account not found")
    return u


def unblock_delay() -> timedelta:
    s = rt.settings
    return timedelta(seconds=s.unblock_seconds_demo) if s.demo_mode else UNBLOCK_DELAY


# ------------------------------------------------------------- caller warning --
async def caller_info(caller_hash: str | None, risk: int | None = None) -> dict:
    """What is known about a number, without saying who said it."""
    if not caller_hash or caller_hash == "-":
        return {"risk": int(risk or 0), "label": RISK_LABEL.get(int(risk or 0), "NONE"), "sources": [], "reports": "0"}
    row = await db.fetchrow("SELECT risk, source FROM caller_risk_list WHERE caller_hash=$1", caller_hash)
    n = await db.fetchval("SELECT count(*) FROM caller_reports WHERE caller_hash=$1 AND category='scam'", caller_hash)
    level = max(int(risk or 0), int(row["risk"]) if row else 0)
    sources = sorted({s for s in ((row["source"] if row else None), "caller_reports" if n else None) if s})
    return {"risk": level, "label": RISK_LABEL.get(level, "NONE"), "sources": sources, "reports": bucket(n)}


@router.get("/users/me/call-status")
async def call_status(p: Principal = Depends(require_user)) -> dict:
    """Is this person on a call, or just off one, and has the number been reported? The app asks when it
    opens and shows a warning before any payment is started (F-31)."""
    u = await _me(p)
    cur = await rt.store.r.hgetall(f"call_flag:{u['phone_hash']}")
    if not cur:
        return {"active": False, "recent": False}
    now = time.time()
    ended = float(cur.get("ended_at") or 0)
    active = cur.get("state") == "active"
    recent = (not active) and ended > 0 and now - ended < 1800
    if not active and not recent:
        return {"active": False, "recent": False}
    row = await db.fetchrow("SELECT caller_hash, caller_known FROM call_events WHERE call_id=$1 AND callee_hash=$2",
                            to_uuid(cur["call_id"]), u["phone_hash"])
    caller = await caller_info(row["caller_hash"] if row else None, int(float(cur.get("caller_risk") or 0)))
    mine = bool(row) and bool(await db.fetchval(
        "SELECT 1 FROM caller_reports WHERE caller_hash=$1 AND reporter_id=$2", row["caller_hash"], to_uuid(p.sub)))
    return {"active": active, "recent": recent, "call_id": cur["call_id"],
            "started_at": float(cur.get("started_at") or 0), "ended_at": ended or None,
            "caller": {**caller, "known": cur.get("caller_known") == "1"},
            "can_report": bool(row) and row["caller_hash"] != "-", "reported_by_you": mine}


class CallerReport(BaseModel):
    call_id: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, min_length=10, max_length=16)
    decision_id: str | None = Field(default=None, max_length=64)
    category: str = Field(default="scam", max_length=8)


def risk_from_reports(verified: int, unverified: int) -> int:
    """How far reports alone can raise a number. Verified means the reporter was called by it, or found it
    in a message that was flagged. Two such people make a number "likely", five make it "known". Typed-in
    reports from people it never called count only towards the lowest level, and that takes three people."""
    if verified >= 5:
        return 3
    if verified >= 2:
        return 2
    if verified + unverified >= 3:
        return 1
    return 0


@router.post("/callers/report", dependencies=[Depends(limit_user("caller-report", 10, 86400,
                                                                    "You have reported many numbers today."))])
async def report_caller(body: CallerReport, request: Request, p: Principal = Depends(require_user)) -> dict:
    """\"This caller was a scammer.\" One report per person per number; a number is not branded on one
    person's word (docs/02, F-31)."""
    if body.category not in ("scam", "spam"):
        raise ApiError(400, "VALIDATION_ERROR", "category must be scam or spam")
    u = await _me(p)
    me = to_uuid(p.sub)
    call_id = None
    if body.phone and not body.call_id:
        digits = "".join(ch for ch in body.phone if ch.isdigit())
        if len(digits) < 10:
            raise ApiError(400, "VALIDATION_ERROR", "Enter the 10-digit number")
        caller = phone_hash(digits)
        verified = bool(await db.fetchval(
            "SELECT 1 FROM call_events WHERE callee_hash=$1 AND caller_hash=$2 AND started_at > now() - interval '30 days' LIMIT 1",
            u["phone_hash"], caller)) or bool(await db.fetchval(
            "SELECT 1 FROM message_checks WHERE user_id=$1 AND flag_set AND entities->'phones' ? $2 LIMIT 1", me, caller))
    else:
        row = await db.fetchrow(
            """SELECT call_id, caller_hash FROM call_events
               WHERE callee_hash=$1 AND ($2::uuid IS NULL OR call_id=$2) AND started_at > now() - make_interval(days => $3)
               ORDER BY started_at DESC LIMIT 1""",
            u["phone_hash"], to_uuid(body.call_id) if body.call_id else None, CALL_REPORT_DAYS)
        if not row or row["caller_hash"] == "-":
            raise ApiError(404, "NOT_FOUND", "There is no recent call to report")
        caller, call_id, verified = row["caller_hash"], row["call_id"], True
    if caller == u["phone_hash"]:
        raise ApiError(400, "VALIDATION_ERROR", "That is your own number")
    await db.execute(
        """INSERT INTO caller_reports(report_id,tenant_id,caller_hash,reporter_id,call_id,decision_id,category,verified)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8)
           ON CONFLICT (caller_hash,reporter_id) DO UPDATE SET category=EXCLUDED.category,
             verified=caller_reports.verified OR EXCLUDED.verified,
             call_id=COALESCE(EXCLUDED.call_id, caller_reports.call_id),
             decision_id=COALESCE(EXCLUDED.decision_id, caller_reports.decision_id)""",
        uuid.uuid4(), p.tenant_id, caller, me, call_id, to_uuid(body.decision_id) if body.decision_id else None,
        body.category, verified)
    c = await db.fetchrow(
        "SELECT count(*) FILTER (WHERE verified) AS v, count(*) FILTER (WHERE NOT verified) AS u "
        "FROM caller_reports WHERE caller_hash=$1 AND category='scam'", caller)
    risk = risk_from_reports(int(c["v"]), int(c["u"]))
    if risk > 0:                                    # reports raise a number's level; they never lower it
        await db.execute(
            """INSERT INTO caller_risk_list(caller_hash,risk,source,updated_at) VALUES($1,$2,'caller_reports',now())
               ON CONFLICT (caller_hash) DO UPDATE SET risk=GREATEST(caller_risk_list.risk, EXCLUDED.risk),
                 updated_at=EXCLUDED.updated_at""", caller, risk)
        cur = int(await rt.store.r.get(f"caller_risk:{caller}") or 0)
        if risk > cur:
            await rt.store.r.set(f"caller_risk:{caller}", risk)
    info = await caller_info(caller)
    await security_event("caller.report", caller[:12], {"category": body.category, "verified": verified},
                         tenant=p.tenant_id, actor=p.actor, name=p.name, request=request)
    return {"reported": True, "verified": verified, "caller": info}


# ------------------------------------------------------------------ look up --
async def lookup_vpa(vpa: str, p: Principal, locale: str) -> dict:
    now = time.time()
    snap = await rt.store.rep_snapshot(p.tenant_id, vpa, now)
    if not snap["exists"] and await warm.load_payee(p.tenant_id, vpa):
        snap = await rt.store.rep_snapshot(p.tenant_id, vpa, now)
    me = to_uuid(p.sub)
    mine = await db.fetchrow("SELECT unblock_at FROM user_blocks WHERE user_id=$1 AND payee_vpa=$2", me, vpa)
    you = {"blocked": bool(mine), "unblock_at": clean(mine["unblock_at"]) if mine else None,
           "is_self": (rt.user_index.get(p.sub) or {}).get("vpa") == vpa}
    if not snap["exists"]:
        return {"kind": "vpa", "vpa": vpa, "exists": False, "you": {**you, "paid_before": 0}}
    known = await rt.store.r.hget(f"known_payee:{p.sub}", vpa)
    you["paid_before"] = int(known.split("|")[0]) if known else 0
    cons = await rt.store.r.hgetall(f"consortium:{p.tenant_id}:{vpa_token(vpa)}")
    rows = await db.fetch(
        """SELECT scam_family, count(DISTINCT user_id) AS people FROM risk_decisions
           WHERE payee_vpa=$1 AND tenant_id=$2 AND created_at > now() - interval '30 days'
             AND tier IN ('STEP_UP','BLOCK') AND scam_family IS NOT NULL AND scam_family <> 'UNCLASSIFIED'
           GROUP BY 1 HAVING count(DISTINCT user_id) >= $3 ORDER BY 2 DESC LIMIT 2""", vpa, p.tenant_id, MIN_PEOPLE)
    return {
        "kind": "vpa", "vpa": vpa, "exists": True, "registered_name": snap["registered_name"],
        "payee_kind": snap["kind"], "verified": snap["verified"], "band": snap["band"],
        "band_reason": i18n.band_reason(locale, snap["band_reason"]), "trust_score": snap["trust_score"],
        "age_days": int(max(0.0, now - snap["first_seen"]) // 86400),
        "community": {"reports_30d": bucket(snap["reports_30d"]), "stopped_14d": bucket(snap["near_miss_14d"]),
                      "confirmed_fraud": snap["confirmed_90d"] > 0, "other_bank": bool(cons),
                      "patterns": [{"code": r["scam_family"], "name": i18n.family(locale, r["scam_family"])["name"],
                                    "advice": advice(locale, r["scam_family"])} for r in rows]},
        "you": you}


async def lookup_phone(digits: str, p: Principal) -> dict:
    u = await _me(p)
    caller = phone_hash(digits)
    info = await caller_info(caller)
    calls = await db.fetchrow(
        "SELECT count(*) AS n, max(started_at) AS last FROM call_events WHERE callee_hash=$1 AND caller_hash=$2 "
        "AND started_at > now() - interval '30 days'", u["phone_hash"], caller)
    mine = await db.fetchval("SELECT 1 FROM caller_reports WHERE caller_hash=$1 AND reporter_id=$2", caller,
                             to_uuid(p.sub))
    return {"kind": "phone", "phone_last4": digits[-4:], **info,
            "called_you": {"count_30d": int(calls["n"]), "last_at": clean(calls["last"])} if calls and calls["n"] else None,
            "reported_by_you": bool(mine), "is_self": caller == u["phone_hash"]}


@router.get("/lookup", dependencies=[Depends(limit_user("lookup", 30, 60, "Too many look-ups. Wait a minute.")),
                                     Depends(limit_user("lookup-day", 300, 86400, "You have reached today's look-up limit."))])
async def lookup(q: str = Query(min_length=3, max_length=120), p: Principal = Depends(require_user)) -> dict:
    """Check a UPI ID or a phone number before dealing with it (F-32)."""
    text = q.strip().lower()
    locale = i18n.norm_locale((rt.user_index.get(p.sub) or {}).get("locale"))
    if "@" in text:
        if not VPA_RE.match(text):
            raise ApiError(400, "VALIDATION_ERROR", "That does not look like a UPI ID (name@bank)")
        return await lookup_vpa(text, p, locale)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 10 or re.search(r"[a-z]", text):
        raise ApiError(400, "VALIDATION_ERROR", "Enter a UPI ID (name@bank) or a 10-digit phone number")
    return await lookup_phone(digits[-10:], p)


# ------------------------------------------------------------ never-pay list --
class BlockCreate(BaseModel):
    vpa: str = Field(min_length=5, max_length=120)
    reason: str | None = Field(default=None, max_length=120)
    report: bool = False                        # also report the UPI ID as fraud


def _block(b, now: datetime) -> dict:
    pending = b["unblock_at"] is not None
    return {"vpa": b["payee_vpa"], "name": b["payee_name"], "reason": b["reason"], "created_at": clean(b["created_at"]),
            "status": "UNBLOCK_PENDING" if pending else "ACTIVE", "unblock_at": clean(b["unblock_at"]),
            "seconds_left": max(0, int((b["unblock_at"] - now).total_seconds())) if pending else None}


@router.get("/users/me/blocks")
async def my_blocks(p: Principal = Depends(require_user)) -> dict:
    rows = await db.fetch("SELECT * FROM user_blocks WHERE user_id=$1 AND (unblock_at IS NULL OR unblock_at > now()) "
                          "ORDER BY created_at DESC", to_uuid(p.sub))
    now = now_utc()
    return {"blocks": [_block(b, now) for b in rows], "unblock_delay_s": int(unblock_delay().total_seconds()),
            "limit": MAX_BLOCKS}


@router.post("/users/me/blocks", dependencies=[Depends(limit_user("block", 30, 3600))])
async def add_block(body: BlockCreate, request: Request, p: Principal = Depends(require_user)) -> dict:
    """Never pay this UPI ID. From now on a payment to it is declined before the PIN, whatever its risk."""
    vpa = body.vpa.strip().lower()
    if not VPA_RE.match(vpa):
        raise ApiError(400, "VALIDATION_ERROR", "That does not look like a UPI ID (name@bank)")
    u = await _me(p)
    if vpa == u["vpa"]:
        raise ApiError(400, "VALIDATION_ERROR", "That is your own UPI ID")
    me = to_uuid(p.sub)
    if await db.fetchval("SELECT count(*) FROM user_blocks WHERE user_id=$1", me) >= MAX_BLOCKS:
        raise ApiError(409, "CONFLICT", f"Your list is full ({MAX_BLOCKS}). Remove one first.")
    d = await rt.store.r.hgetall(f"vpa_dir:{vpa}")
    if not d and await warm.load_payee(p.tenant_id, vpa):
        d = await rt.store.r.hgetall(f"vpa_dir:{vpa}")
    await db.execute(
        """INSERT INTO user_blocks(user_id,payee_vpa,payee_name,reason) VALUES($1,$2,$3,$4)
           ON CONFLICT (user_id,payee_vpa) DO UPDATE SET unblock_at=NULL, reason=COALESCE(EXCLUDED.reason, user_blocks.reason)""",
        me, vpa, (d or {}).get("registered_name"), (body.reason or "").strip() or None)
    await rt.store.set_user_block(p.sub, vpa, 0.0)
    report = None
    if body.report:
        try:
            report = await file_report(tenant=p.tenant_id, kind="FRAUD", reporter_id=p.sub, decision_id=None, txn_id=None,
                                       payee_vpa=vpa, comment=(body.reason or "Added to the never-pay list")[:200])
        except ApiError:
            report = None
    await security_event("block.add", vpa, {"reported": bool(report)}, tenant=p.tenant_id, actor=p.actor, name=p.name,
                         request=request)
    return {**(await my_blocks(p)), "report": report, "exists": bool(d)}


async def _my_block(vpa: str, p: Principal):
    b = await db.fetchrow("SELECT * FROM user_blocks WHERE user_id=$1 AND payee_vpa=$2", to_uuid(p.sub), vpa.strip().lower())
    if not b:
        raise ApiError(404, "NOT_FOUND", "That UPI ID is not on your list")
    return b


@router.delete("/users/me/blocks/{vpa}")
async def remove_block(vpa: str, request: Request, p: Principal = Depends(require_user)) -> dict:
    """Taking a UPI ID off the list takes 24 hours, for the same reason removing a guardian does: someone
    on the phone can talk a person into unblocking, but not into waiting a day."""
    b = await _my_block(vpa, p)
    if b["unblock_at"] is None:
        at = now_utc() + unblock_delay()
        await db.execute("UPDATE user_blocks SET unblock_at=$3 WHERE user_id=$1 AND payee_vpa=$2",
                         b["user_id"], b["payee_vpa"], at)
        await rt.store.set_user_block(p.sub, b["payee_vpa"], at.timestamp())
        await security_event("block.remove_requested", b["payee_vpa"], {"lifts_at": at.isoformat()}, tenant=p.tenant_id,
                             actor=p.actor, name=p.name, request=request)
    return await my_blocks(p)


@router.post("/users/me/blocks/{vpa}/keep")
async def keep_block(vpa: str, request: Request, p: Principal = Depends(require_user)) -> dict:
    """Changed my mind: keep it blocked."""
    b = await _my_block(vpa, p)
    await db.execute("UPDATE user_blocks SET unblock_at=NULL WHERE user_id=$1 AND payee_vpa=$2", b["user_id"], b["payee_vpa"])
    await rt.store.set_user_block(p.sub, b["payee_vpa"], 0.0)
    await security_event("block.kept", b["payee_vpa"], {}, tenant=p.tenant_id, actor=p.actor, name=p.name, request=request)
    return await my_blocks(p)


# -------------------------------------------------------------------- trends --
def _round_down(n: int) -> int:
    return n if n < 10 else (n // 10) * 10 if n < 100 else (n // 50) * 50


@router.get("/trends")
async def trends(p: Principal = Depends(get_principal)) -> dict:
    """The scam patterns seen most at this bank in the last seven days (F-34)."""
    if p.kind == "service":
        raise ApiError(403, "FORBIDDEN", "Sign in to see trends")
    locale = i18n.norm_locale((rt.user_index.get(p.sub) or {}).get("locale"))
    key = f"trends:{p.tenant_id}:{locale}"
    cached = await rt.store.r.get(key)
    if cached:
        return json.loads(cached)
    rows = await db.fetch(
        """SELECT scam_family,
                  count(*) FILTER (WHERE created_at > now() - interval '7 days')  AS this_week,
                  count(*) FILTER (WHERE created_at <= now() - interval '7 days') AS last_week,
                  count(DISTINCT user_id) FILTER (WHERE created_at > now() - interval '7 days') AS people
           FROM risk_decisions
           WHERE tenant_id=$1 AND created_at > now() - interval '14 days' AND tier IN ('STEP_UP','BLOCK')
             AND scam_family IS NOT NULL AND scam_family <> 'UNCLASSIFIED'
           GROUP BY 1 ORDER BY 2 DESC""", p.tenant_id)
    items = []
    for r in rows:
        if r["people"] < MIN_PEOPLE:
            continue
        now_n, before = int(r["this_week"]), int(r["last_week"])
        trend = "new" if before == 0 else "up" if now_n > before * 1.2 else "down" if now_n < before * 0.8 else "steady"
        fam = i18n.family(locale, r["scam_family"])
        items.append({"code": r["scam_family"], "name": fam["name"], "advice": advice(locale, r["scam_family"]),
                      "seen": _round_down(now_n), "trend": trend})
    out = {"days": 7, "trends": items[:3], "generated_at": datetime.now(timezone.utc).isoformat()}
    await rt.store.r.set(key, json.dumps(out), ex=300)
    return out
