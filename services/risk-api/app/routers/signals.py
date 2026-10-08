"""Signals around a payment: calls (F-05) and pasted messages (F-16)."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db, i18n
from ..bus import bus
from ..common import clean, to_uuid
from ..core.textcheck import extract_entities, red_flags
from ..core.textmodel import CLASS_FAMILY, verdict_for
from ..realtime import emit_user
from ..runtime import rt
from ..security import ApiError, Principal, limit_user, phone_hash, require_service, require_user, text_hash

router = APIRouter(prefix="/v1/signals", tags=["signals"])


class CallEvent(BaseModel):
    call_id: str | None = Field(default=None, max_length=64)
    event: str = Field(max_length=16)            # started | ended
    callee_phone_hash: str | None = Field(default=None, max_length=64)
    callee_user_id: str | None = Field(default=None, max_length=64)
    caller_phone_hash: str = Field(max_length=64)
    caller_known: bool = False
    at: float | None = None
    started_at: float | None = None              # optional backdating, used by demo staging
    source: str = "simulated"


async def ingest_call(ev: CallEvent) -> dict:
    """Shared by the endpoint and the demo control. No audio or content is ever received."""
    if ev.event not in ("started", "ended"):
        raise ApiError(400, "VALIDATION_ERROR", "event must be started or ended")
    callee = ev.callee_phone_hash
    if not callee and ev.callee_user_id:
        callee = (rt.user_index.get(ev.callee_user_id) or {}).get("phone_hash")
    if not callee:
        raise ApiError(400, "VALIDATION_ERROR", "callee_phone_hash or a known callee_user_id is required")
    ts = ev.at or time.time()
    call_id = ev.call_id or str(uuid.uuid4())
    risk = int(await rt.store.r.get(f"caller_risk:{ev.caller_phone_hash}") or 0)
    started = ev.started_at or ts
    await rt.store.call_event(phone_hash=callee, call_id=call_id, event=ev.event, caller_known=ev.caller_known,
                              caller_risk=risk, ts=ts, started_at=started)
    if ev.event == "started":
        await db.execute(
            """INSERT INTO call_events(call_id,callee_hash,caller_hash,caller_risk,caller_known,started_at,source)
               VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING""",
            to_uuid(call_id), callee, ev.caller_phone_hash, risk, ev.caller_known,
            datetime.fromtimestamp(started, timezone.utc), ev.source)
    else:
        await db.execute("UPDATE call_events SET ended_at=$2 WHERE call_id=$1", to_uuid(call_id),
                         datetime.fromtimestamp(ts, timezone.utc))
    await bus().publish("call.events", {"tenant_id": "", "call_id": call_id, "event": ev.event, "callee_hash": callee,
                                        "caller_risk": risk, "caller_known": ev.caller_known, "at": ts})
    # A number other people have reported is calling this person: their app shows a warning at once (F-31).
    user_id = rt.phone_index.get(callee)
    if user_id and ev.event == "started" and risk >= 2 and not ev.caller_known:
        await emit_user(user_id, "call.flagged", {"call_id": call_id, "risk": risk})
    return {"call_id": call_id, "caller_risk": risk}


@router.post("/call", status_code=202)
async def signal_call(body: CallEvent, p: Principal = Depends(require_service)) -> dict:
    return await ingest_call(body)


class MessageCheck(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    source: str = "paste"


async def check_message(user_id: str, tenant: str, text: str, locale: str) -> dict:
    now = time.time()
    label, probs, scam_prob = rt.classify(text)
    verdict = verdict_for(scam_prob)
    fam_code = CLASS_FAMILY.get(label) if verdict != "FINE" else None
    ents = extract_entities(text)
    cat = i18n.catalog(locale)["message"]
    flags = [{**f, "why": cat["flags"].get(f["kind"], "")} for f in red_flags(text)] if verdict != "FINE" else []
    flag_set = scam_prob >= 0.7
    check_id = str(uuid.uuid4())
    stored_ents = {**ents, "phones": [phone_hash(x) for x in ents["phones"]]}
    if flag_set:
        await rt.store.message_flag(user_id=user_id, check_id=check_id, scam_prob=scam_prob, label=label,
                                    family=fam_code, vpas=ents["vpas"], ts=now)
    await db.execute(
        """INSERT INTO message_checks(check_id,user_id,tenant_id,text,text_hash,label,scam_prob,verdict,family,entities,
              red_flags,class_probs,flag_set) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
        to_uuid(check_id), to_uuid(user_id), tenant, text, text_hash(text), label, scam_prob, verdict, fam_code,
        stored_ents, flags, {k: round(v, 4) for k, v in probs.items()}, flag_set)
    await bus().publish("msg.checked", {"tenant_id": tenant, "check_id": check_id, "user_id": user_id,
                                        "verdict": verdict, "label": label, "scam_prob": scam_prob,
                                        "flag_set": flag_set, "entities": stored_ents, "at": now})
    return {
        "check_id": check_id, "verdict": verdict, "scam_prob": round(scam_prob, 3), "label": label,
        "family": ({"code": fam_code, "name": i18n.family(locale, fam_code)["name"],
                    "headline": i18n.family(locale, fam_code)["headline"]} if fam_code else None),
        "red_flags": flags, "entities": ents, "advice": cat["advice"][verdict], "flag_set": flag_set,
        "flag_expires_at": datetime.fromtimestamp(now + 86400, timezone.utc).isoformat() if flag_set else None,
    }


@router.post("/message", dependencies=[Depends(limit_user("msg", 20, 60, "Too many checks. Try again in a minute."))])
async def signal_message(body: MessageCheck, p: Principal = Depends(require_user)) -> dict:
    locale = i18n.norm_locale((rt.user_index.get(p.sub) or {}).get("locale"))
    return await check_message(p.sub, p.tenant_id, body.text.strip(), locale)


@router.get("/messages")
async def my_messages(p: Principal = Depends(require_user)) -> dict:
    rows = await db.fetch(
        "SELECT check_id, text, verdict, label, family, scam_prob, flag_set, created_at FROM message_checks "
        "WHERE user_id=$1 ORDER BY created_at DESC LIMIT 20", to_uuid(p.sub))
    return {"checks": clean(rows)}
