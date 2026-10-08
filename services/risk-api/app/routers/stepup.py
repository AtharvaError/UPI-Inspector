"""Step-Up: a pause, one question, then authentication (F-09). The server enforces all three.

Authentication is a one-time code, or a proof from a device enrolled with the PIN (docs/11, section 1.3).
Three wrong attempts end the challenge; the payment has to be started again.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db, i18n
from ..common import to_uuid
from ..core.present import family_payload
from ..orchestrator import get_snapshot, publish_outcome, save_snapshot
from ..realtime import emit_user
from ..runtime import rt
from ..security import ApiError, Principal, keyed, rate_limit, require_user, same

router = APIRouter(prefix="/v1/stepup", tags=["step-up"])
MAX_TRIES = 3            # wrong codes or proofs before the challenge is closed
MAX_SENDS = 3            # one-time codes that can be sent for one challenge
RESEND_WAIT_S = 20
OTP_TTL_S = 300


class ChallengeStart(BaseModel):
    challenge_id: str = Field(min_length=8, max_length=64)
    method: str = "otp"


class Auth(BaseModel):
    method: str = Field(max_length=16)
    otp: str | None = Field(default=None, max_length=12)
    key_id: str | None = Field(default=None, max_length=64)       # biometric: which enrolled device
    proof: str | None = Field(default=None, max_length=64)        # biometric: HMAC-SHA256(device secret, challenge_id)
    biometric_ok: bool | None = None                              # accepted only when TRUST_CLIENT_BIOMETRIC=true


class Verify(BaseModel):
    challenge_id: str = Field(min_length=8, max_length=64)
    answer: str = Field(max_length=8)
    auth: Auth | None = None


async def _load(challenge_id: str, p: Principal) -> dict:
    raw = await rt.store.r.get(f"chal:{challenge_id}")
    if not raw:
        raise ApiError(404, "NOT_FOUND", "Unknown or expired challenge")
    chal = json.loads(raw)
    if chal["user_id"] != p.sub:
        raise ApiError(403, "FORBIDDEN", "Not your challenge")
    if chal["status"] != "OPEN":
        raise ApiError(409, "CONFLICT", f"This challenge is already {chal['status'].lower()}")
    return chal


async def _save(chal: dict, ttl: int = 3600) -> None:
    await rt.store.r.set(f"chal:{chal['challenge_id']}", json.dumps(chal), ex=ttl)


def device_proof(secret_hex: str, challenge_id: str) -> str:
    """What an enrolled device sends: it shows the request comes from the phone that holds the secret."""
    return hmac.new(bytes.fromhex(secret_hex), challenge_id.encode(), hashlib.sha256).hexdigest()


@router.post("/challenge")
async def start_auth(body: ChallengeStart, p: Principal = Depends(require_user)) -> dict:
    """Send a one-time code. In the prototype it is delivered as a simulated SMS inside the app."""
    chal = await _load(body.challenge_id, p)
    now = time.time()
    if now < chal["cooloff_until"]:
        raise ApiError(425, "TOO_EARLY", "Please wait for the pause to finish",
                       [{"retry_in_s": round(chal["cooloff_until"] - now, 1)}])
    if chal.get("sends", 0) >= MAX_SENDS:
        raise ApiError(429, "RATE_LIMITED", "No more codes can be sent for this payment. Start the payment again.")
    wait = RESEND_WAIT_S - (now - chal.get("sent_at", 0))
    if wait > 0:
        raise ApiError(429, "RATE_LIMITED", "A code was just sent. Wait a moment before asking for another.",
                       [{"retry_in_s": round(wait, 1)}])
    await rate_limit("otp", p.sub, 12, 600, "Too many codes requested. Wait a few minutes.")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    # Stored as a keyed hash bound to this challenge: reading the cache does not reveal a usable code.
    await rt.store.r.set(f"otp:{body.challenge_id}", keyed("otp", body.challenge_id, code), ex=OTP_TTL_S)
    chal["sends"] = chal.get("sends", 0) + 1
    chal["sent_at"] = now
    await _save(chal, 24 * 3600)
    await emit_user(p.sub, "sms.simulated", {"kind": "otp", "text": f"Your UPI Inspector code is {code}. Do not share it."})
    out = {"delivery": "simulated", "expires_in_s": OTP_TTL_S, "sends_left": MAX_SENDS - chal["sends"],
           "resend_in_s": RESEND_WAIT_S}
    if rt.settings.demo_mode:
        out["demo_code"] = code          # so the demo still works if the socket is down
    return out


async def _authenticated(auth: Auth, chal: dict, p: Principal) -> bool:
    if auth.method == "otp":
        key = f"otp:{chal['challenge_id']}"
        stored = await rt.store.r.get(key)
        code = (auth.otp or "").strip()
        ok = bool(stored) and code.isdigit() and same(stored, keyed("otp", chal["challenge_id"], code))
        if ok:
            await rt.store.r.delete(key)                    # a code works once
        return ok
    if auth.method == "biometric":
        if auth.key_id and auth.proof:
            row = await db.fetchrow("SELECT secret FROM device_keys WHERE user_id=$1 AND key_id=$2",
                                    to_uuid(p.sub), auth.key_id)
            ok = bool(row) and same(auth.proof.lower(), device_proof(row["secret"], chal["challenge_id"]))
            if ok:
                await db.execute("UPDATE device_keys SET last_used=now() WHERE user_id=$1 AND key_id=$2",
                                 to_uuid(p.sub), auth.key_id)
            return ok
        if rt.settings.trust_client_biometric:
            return bool(auth.biometric_ok)
        raise ApiError(400, "BIOMETRIC_NOT_ENROLLED", "This device is not enrolled. Use the one-time code.")
    raise ApiError(400, "VALIDATION_ERROR", "auth.method must be biometric or otp")


@router.post("/verify")
async def verify(body: Verify, p: Principal = Depends(require_user)) -> dict:
    chal = await _load(body.challenge_id, p)
    now = time.time()
    if now < chal["cooloff_until"]:
        raise ApiError(425, "TOO_EARLY", "Please wait for the pause to finish",
                       [{"retry_in_s": round(chal["cooloff_until"] - now, 1)}])
    answer = body.answer.strip().lower()
    if answer not in ("yes", "no"):
        raise ApiError(400, "VALIDATION_ERROR", "answer must be yes or no")
    snap = await get_snapshot(chal["decision_id"])
    if not snap:
        raise ApiError(404, "NOT_FOUND", "Unknown decision")

    if answer == chal["risky_answer"]:
        # The payer told us they are being coached: the payment is stopped.
        chal["status"] = "FAILED"
        await _save(chal)
        await rt.store.r.delete(f"otp:{body.challenge_id}")
        snap["action"] = "DECLINE"
        await save_snapshot(snap)
        hours = float((await rt.store.r.hget(f"policy:{snap['tenant_id']}", "pair_block_hours")) or 24)
        await rt.store.set_pair_block(snap["user_id"], snap["payee_vpa"], now + hours * 3600, {
            "decision_id": snap["decision_id"], "scam_family": snap.get("scam_family"), "family_sim": None,
            "family_variant": snap.get("family_variant")})
        await db.execute(
            """INSERT INTO pair_blocks(user_id,payee_vpa,until,decision_id) VALUES($1,$2,$3,$4)
               ON CONFLICT (user_id,payee_vpa) DO UPDATE SET until=EXCLUDED.until, decision_id=EXCLUDED.decision_id""",
            to_uuid(snap["user_id"]), snap["payee_vpa"], datetime.fromtimestamp(now + hours * 3600, timezone.utc),
            to_uuid(snap["decision_id"]))
        await db.execute("UPDATE stepup_challenges SET status='FAILED' WHERE challenge_id=$1", to_uuid(body.challenge_id))
        await publish_outcome(snap, "STEPUP_FAILED", "NOT_ATTEMPTED")
        loc = i18n.norm_locale(snap.get("locale"))
        return {"result": "FAILED", "action": "DECLINE",
                "scam_family": family_payload(loc, snap.get("scam_family") or "UNCLASSIFIED", None,
                                              variant=snap.get("family_variant"))}

    auth = body.auth
    if auth is None:
        # The answer was the safe one. The app asked first so that a risky answer stops the payment before
        # anyone is asked to authenticate; it now sends the same answer again with the authentication.
        enrolled = await db.fetchval("SELECT count(*) FROM device_keys WHERE user_id=$1", to_uuid(p.sub))
        methods = ["otp"] + (["biometric"] if (enrolled or rt.settings.trust_client_biometric) else [])
        return {"result": "AUTH_REQUIRED", "action": "STEP_UP", "auth_methods": methods,
                "tries_left": MAX_TRIES - chal.get("tries", 0)}

    if not await _authenticated(auth, chal, p):
        chal["tries"] = chal.get("tries", 0) + 1
        if chal["tries"] >= MAX_TRIES:
            # Whoever is holding the phone cannot authenticate. The challenge is closed for good.
            chal["status"] = "LOCKED"
            await _save(chal, 24 * 3600)
            await rt.store.r.delete(f"otp:{body.challenge_id}")
            await db.execute("UPDATE stepup_challenges SET status='LOCKED', auth_method=$2 WHERE challenge_id=$1",
                             to_uuid(body.challenge_id), auth.method)
            await publish_outcome(snap, "STEPUP_LOCKED", "NOT_ATTEMPTED")
            return {"result": "LOCKED", "action": "DECLINE", "tries_left": 0}
        await _save(chal, 24 * 3600)
        return {"result": "BAD_CODE", "action": "STEP_UP", "tries_left": MAX_TRIES - chal["tries"]}

    chal["status"] = "PASSED"
    await _save(chal, 24 * 3600)
    snap = await get_snapshot(chal["decision_id"]) or snap
    snap["stepup_passed_at"] = time.time()          # the payment is cleared for a limited time from here
    await save_snapshot(snap)
    await db.execute("UPDATE stepup_challenges SET status='PASSED', auth_method=$2 WHERE challenge_id=$1",
                     to_uuid(body.challenge_id), auth.method)
    guardian = None
    if snap.get("guardian_request_id"):
        raw = await rt.store.r.get(f"greq:{snap['guardian_request_id']}")
        g = json.loads(raw) if raw else {}
        guardian = {"required": bool(snap.get("guardian_required")), "request_id": snap["guardian_request_id"],
                    "status": g.get("status", "PENDING")}
    return {"result": "PASSED", "action": "ALLOW", "guardian": guardian,
            "valid_for_s": rt.settings.clearance_ttl_s}
