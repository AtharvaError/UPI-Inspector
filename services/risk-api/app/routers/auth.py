"""Sign-in, sessions, registration and who-am-I (F-30, F-35)."""
from __future__ import annotations

import re
import secrets
import time
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .. import db
from ..common import audit, now_utc, to_uuid
from ..core import warm
from ..runtime import rt
from ..security import (LOCK_MAX_SECONDS, LOCK_SECONDS, MAX_FAILED, ApiError, Principal, client_ip,
                        demo_login_allowed, get_principal, hash_secret_async, limit_ip, limit_user, make_token,
                        phone_hash, rate_limit, require_demo_login, require_staff, require_user, session_deadline,
                        verify_secret_async, weak_password, weak_pin)
from ..sessions import bump_generation, revoke_session, security_event

router = APIRouter(prefix="/v1/auth", tags=["auth"])
HANDLE = {"bank_a": "banka", "bank_b": "bankb"}
MAX_DEVICE_KEYS = 5
WRONG_PIN = (403, "WRONG_PIN", "Wrong PIN")
NAME_BAD = set('<>{}[]\\/|;"=`')


def Pin():
    return Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class DeviceKey(BaseModel):
    """A secret one installed copy of the app holds in the phone's secure storage (docs/11, section 1.3)."""
    key_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    secret: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    label: str | None = Field(default=None, max_length=60)


class Login(BaseModel):
    phone: str = Field(min_length=10, max_length=16)
    pin: str = Pin()
    device: DeviceKey | None = None


class Register(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    phone: str = Field(min_length=10, max_length=16)
    pin: str = Pin()
    locale: str = "en"
    tenant_id: str = "bank_a"
    age_band: str = "26-59"
    device: DeviceKey | None = None


class StaffLogin(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class PinCheck(BaseModel):
    pin: str = Pin()


class PinChange(BaseModel):
    current_pin: str = Pin()
    new_pin: str = Pin()


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class DeviceEnrol(BaseModel):
    pin: str = Pin()
    device: DeviceKey


def _user_payload(u) -> dict:
    return {"user_id": str(u["user_id"]), "name": u["display_name"], "tenant_id": u["tenant_id"],
            "vpa": u["primary_vpa"], "locale": u["locale"], "segment": u["segment"], "age_band": u["age_band"],
            "voice_warnings": u["voice_warnings"], "large_text": u["large_text"], "balance": float(u["balance"]),
            "phone_last4": u["phone_last4"], "is_demo": u["is_demo"]}


def _staff_payload(s) -> dict:
    return {"staff_id": str(s["staff_id"]), "name": s["display_name"], "email": s["email"], "role": s["role"],
            "tenant_id": s["tenant_id"]}


def _user_principal(u) -> Principal:
    return Principal(kind="user", sub=str(u["user_id"]), tenant_id=u["tenant_id"], role="user", name=u["display_name"])


def _staff_principal(s) -> Principal:
    return Principal(kind="staff", sub=str(s["staff_id"]), tenant_id=s["tenant_id"], role=s["role"],
                     name=s["display_name"])


def _session(p: Principal, row, amr: str, *, sid: str | None = None, auth_time: int | None = None) -> dict:
    """The token and what the app needs to know about how long it lasts."""
    s = rt.settings
    now = int(time.time())
    started = auth_time or now
    token = make_token(p, amr=amr, gen=row["token_gen"], sid=sid, auth_time=started)
    minutes = s.staff_token_minutes if p.kind == "staff" else s.user_token_minutes
    return {"token": token, "expires_in_s": min(minutes * 60, session_deadline(started) - now),
            "session_ends_at": session_deadline(started), "auth_method": amr}


async def _guard(table: str, key: str, row, ok: bool, request: Request, what: str,
                 wrong: tuple = (401, "UNAUTHENTICATED", "Wrong sign-in details")) -> None:
    """Count failures. Five in a row lock the account: five minutes the first time, then twice as long each
    time, up to a day. A correct PIN during a lock is refused too, so a lock cannot be guessed through."""
    if row["locked_until"] and row["locked_until"] > now_utc():
        wait = int((row["locked_until"] - now_utc()).total_seconds()) + 1
        raise ApiError(429, "RATE_LIMITED", "Too many wrong attempts. Try again later.", [{"retry_in_s": wait}])
    if ok:
        if row["failed_attempts"] or row["lockouts"] or row["locked_until"]:
            await db.execute(f"UPDATE {table} SET failed_attempts=0, lockouts=0, locked_until=NULL WHERE {key}=$1", row[key])
        return
    # one statement, so two wrong attempts arriving together cannot both read the same count
    r = await db.fetchrow(
        f"""UPDATE {table} SET
              failed_attempts = CASE WHEN failed_attempts + 1 >= $2 THEN 0 ELSE failed_attempts + 1 END,
              lockouts        = CASE WHEN failed_attempts + 1 >= $2 THEN lockouts + 1 ELSE lockouts END,
              locked_until    = CASE WHEN failed_attempts + 1 >= $2
                                     THEN now() + make_interval(secs => LEAST($3 * power(2, LEAST(lockouts, 12)), $4))
                                     ELSE locked_until END
            WHERE {key}=$1 RETURNING failed_attempts, lockouts, locked_until""",
        row[key], MAX_FAILED, float(LOCK_SECONDS), float(LOCK_MAX_SECONDS))
    locked = bool(r and r["locked_until"] and r["locked_until"] > now_utc())
    kind = "staff" if table == "staff_users" else "user"
    await security_event("auth.lockout" if locked else f"{what}_failed", str(row[key]),
                         {"lockouts": r["lockouts"] if r else None} if locked else {}, tenant=row["tenant_id"],
                         actor=f"{kind}:{row[key]}", name=row["display_name"], request=request)
    if locked:
        raise ApiError(429, "RATE_LIMITED", "Too many wrong attempts. Try again later.",
                       [{"retry_in_s": int((r["locked_until"] - now_utc()).total_seconds()) + 1}])
    raise ApiError(wrong[0], wrong[1], wrong[2], [{"tries_left": MAX_FAILED - int(r["failed_attempts"])}] if r else None)


async def _enrol(user_id, dev: DeviceKey) -> None:
    await db.execute(
        """INSERT INTO device_keys(user_id,key_id,secret,label) VALUES($1,$2,$3,$4)
           ON CONFLICT (user_id,key_id) DO UPDATE SET secret=EXCLUDED.secret, label=EXCLUDED.label, created_at=now()""",
        user_id, dev.key_id, dev.secret, dev.label)
    await db.execute(
        """DELETE FROM device_keys WHERE user_id=$1 AND key_id NOT IN
             (SELECT key_id FROM device_keys WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2)""",
        user_id, MAX_DEVICE_KEYS)


@router.get("/config")
async def config(request: Request) -> dict:
    """What the sign-in screen needs before anyone has signed in."""
    s = rt.settings
    return {"demo_mode": s.demo_mode, "demo_login": demo_login_allowed(request),
            "tenants": [{"tenant_id": "bank_a", "name": "Bank A"}, {"tenant_id": "bank_b", "name": "Bank B"}],
            "session": {"user_idle_minutes": s.user_token_minutes, "staff_idle_minutes": s.staff_token_minutes,
                        "max_hours": s.session_max_hours}}


@router.post("/login", dependencies=[Depends(limit_ip("login", 20, 60))])
async def login(body: Login, request: Request) -> dict:
    ph = phone_hash(body.phone)
    await rate_limit("login-id", ph, 15, 300, "Too many attempts for this number. Try again in a few minutes.")
    u = await db.fetchrow("SELECT * FROM users WHERE phone_hash=$1", ph)
    ok = await verify_secret_async(u["pin_hash"] if u else None, body.pin)      # same work whether or not it exists
    if not u or not u["pin_hash"]:
        await security_event("auth.login_failed", "unknown", {"phone_last4": body.phone[-4:]}, request=request)
        raise ApiError(401, "UNAUTHENTICATED", "Wrong sign-in details")
    await _guard("users", "user_id", u, ok, request, "auth.login")
    if not u["active"]:                                # said only to someone who has proved they know the PIN
        raise ApiError(403, "FORBIDDEN", "This account has been deactivated")
    if body.device:
        await _enrol(u["user_id"], body.device)
    p = _user_principal(u)
    await security_event("auth.login", p.sub, {"method": "pin", "device_enrolled": bool(body.device)},
                         tenant=p.tenant_id, actor=p.actor, name=p.name, request=request)
    return {**_session(p, u, "pin"), "user": _user_payload(u)}


@router.post("/register", dependencies=[Depends(limit_ip("register", 10, 600, "Too many new accounts from here. Try later."))])
async def register(body: Register, request: Request) -> dict:
    if body.tenant_id not in HANDLE:
        raise ApiError(400, "VALIDATION_ERROR", "Unknown bank")
    why = weak_pin(body.pin, body.phone)
    if why:
        raise ApiError(400, "WEAK_PIN", why)
    name = " ".join(body.name.split())
    if len(name) < 2 or not any(ch.isalpha() for ch in name) or any(ch in NAME_BAD or ord(ch) < 32 for ch in name):
        raise ApiError(400, "VALIDATION_ERROR", "Enter your name using letters")
    ph = phone_hash(body.phone)
    if await db.fetchval("SELECT 1 FROM users WHERE phone_hash=$1", ph):
        raise ApiError(409, "CONFLICT", "An account with this phone number already exists")
    user_id = uuid.uuid4()
    stem = re.sub(r"[^a-z0-9]", "", name.lower().split()[0]) or "user"
    vpa = f"{stem}{secrets.randbelow(9000) + 1000}@{HANDLE[body.tenant_id]}"
    locale = body.locale if body.locale in ("en", "hi") else "en"
    age_band = body.age_band if body.age_band in ("18-25", "26-59", "60+") else "26-59"
    segment = {"18-25": "student", "60+": "elderly"}.get(age_band, "salaried")
    digits = "".join(ch for ch in body.phone if ch.isdigit())
    now = now_utc()
    await db.execute(
        """INSERT INTO users(user_id,tenant_id,display_name,phone_hash,phone_last4,pin_hash,primary_vpa,segment,age_band,
                             locale,balance,seed_balance,first_seen,pin_changed_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,50000,50000,$11,$11)""",
        user_id, body.tenant_id, name, ph, digits[-4:], await hash_secret_async(body.pin), vpa, segment, age_band,
        locale, now)
    await db.execute(
        """INSERT INTO vpas(vpa_id,tenant_id,registered_name,kind,verified,owner_user_id,first_seen)
           VALUES($1,$2,$3,'person',false,$4,$5)""", vpa, body.tenant_id, name, user_id, now)
    if body.device:
        await _enrol(user_id, body.device)
    u = await db.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
    await warm.load_user(str(user_id))
    await warm.load_payee(body.tenant_id, vpa)
    p = _user_principal(u)
    await security_event("user.register", p.sub, {"vpa": vpa}, tenant=p.tenant_id, actor=p.actor, name=p.name,
                         request=request)
    return {**_session(p, u, "pin"), "user": _user_payload(u)}


@router.get("/demo-users", dependencies=[Depends(require_demo_login)])
async def demo_users() -> dict:
    rows = await db.fetch("SELECT * FROM users WHERE is_demo ORDER BY created_at, display_name")
    return {"users": [_user_payload(u) for u in rows]}


@router.post("/demo-login", dependencies=[Depends(require_demo_login), Depends(limit_ip("demo-login", 60, 60))])
async def demo_login(body: dict, request: Request) -> dict:
    u = await db.fetchrow("SELECT * FROM users WHERE user_id=$1 AND is_demo AND active", to_uuid(body.get("user_id")))
    if not u:
        raise ApiError(404, "NOT_FOUND", "No such demo user")
    p = _user_principal(u)
    await security_event("auth.demo_login", p.sub, {}, tenant=p.tenant_id, actor=p.actor, name=p.name, request=request)
    return {**_session(p, u, "demo"), "user": _user_payload(u)}


@router.post("/staff-login", dependencies=[Depends(limit_ip("staff-login", 20, 60))])
async def staff_login(body: StaffLogin, request: Request) -> dict:
    email = body.email.strip().lower()
    await rate_limit("login-id", f"staff:{email}", 15, 300, "Too many attempts for this account. Try again in a few minutes.")
    s = await db.fetchrow("SELECT * FROM staff_users WHERE lower(email)=$1", email)
    ok = await verify_secret_async(s["password_hash"] if s else None, body.password)
    if not s:
        await security_event("auth.staff_login_failed", "unknown", {"email": email[:80]}, request=request)
        raise ApiError(401, "UNAUTHENTICATED", "Wrong sign-in details")
    await _guard("staff_users", "staff_id", s, ok, request, "auth.staff_login")
    if not s["active"]:
        raise ApiError(403, "FORBIDDEN", "This account has been deactivated")
    await db.execute("UPDATE staff_users SET last_login_at=now() WHERE staff_id=$1", s["staff_id"])
    p = _staff_principal(s)
    await security_event("staff.login", p.sub, {"method": "password"}, tenant=p.tenant_id, actor=p.actor, name=p.name,
                         request=request)
    return {**_session(p, s, "password"), "staff": _staff_payload(s)}


@router.get("/demo-staff", dependencies=[Depends(require_demo_login)])
async def demo_staff() -> dict:
    rows = await db.fetch("SELECT * FROM staff_users WHERE active ORDER BY tenant_id, role")
    return {"staff": [_staff_payload(s) for s in rows if s["email"].endswith(".demo")]}


@router.post("/demo-staff-login", dependencies=[Depends(require_demo_login), Depends(limit_ip("demo-login", 60, 60))])
async def demo_staff_login(body: dict, request: Request) -> dict:
    s = await db.fetchrow("SELECT * FROM staff_users WHERE lower(email)=lower($1) AND active", str(body.get("email", "")))
    if not s or not s["email"].endswith(".demo"):
        raise ApiError(404, "NOT_FOUND", "No such demo account")
    await db.execute("UPDATE staff_users SET last_login_at=now() WHERE staff_id=$1", s["staff_id"])
    p = _staff_principal(s)
    await security_event("staff.demo_login", p.sub, {}, tenant=p.tenant_id, actor=p.actor, name=p.name, request=request)
    return {**_session(p, s, "demo"), "staff": _staff_payload(s)}


async def _account(p: Principal):
    if p.kind == "user":
        return await db.fetchrow("SELECT * FROM users WHERE user_id=$1", to_uuid(p.sub))
    if p.kind == "staff":
        return await db.fetchrow("SELECT * FROM staff_users WHERE staff_id=$1", to_uuid(p.sub))
    return None


@router.get("/me")
async def me(p: Principal = Depends(get_principal)) -> dict:
    if p.kind == "service":
        return {"kind": "service", "name": p.sub, "at": time.time()}
    row = await _account(p)
    if not row:
        raise ApiError(404, "NOT_FOUND", "Account not found")
    session = {"expires_at": p.exp, "session_ends_at": session_deadline(p.auth_time), "auth_method": p.amr}
    if p.kind == "user":
        return {"kind": "user", "user": _user_payload(row), "session": session}
    return {"kind": "staff", "staff": _staff_payload(row), "session": session}


@router.post("/refresh", dependencies=[Depends(limit_user("refresh", 30, 60))])
async def refresh(p: Principal = Depends(get_principal)) -> dict:
    """A new token for the same session. The apps call this while they are in use, so an idle session
    lapses on its own and an active one is never interrupted, up to the session's absolute end."""
    if p.kind == "service":
        raise ApiError(400, "VALIDATION_ERROR", "Services do not hold sessions")
    if time.time() >= session_deadline(p.auth_time) - 5:
        raise ApiError(401, "UNAUTHENTICATED", "This session has ended. Please sign in again.")
    row = await _account(p)                    # read again: a changed name, role or bank takes effect here
    if not row or not row["active"]:
        raise ApiError(401, "UNAUTHENTICATED", "This account has been deactivated")
    fresh = _user_principal(row) if p.kind == "user" else _staff_principal(row)
    return _session(fresh, row, p.amr or "pin", sid=p.sid, auth_time=p.auth_time)


@router.post("/logout")
async def logout(request: Request, p: Principal = Depends(get_principal)) -> dict:
    if p.kind == "service":
        return {"signed_out": False}
    await revoke_session(p, "logout")
    await security_event("auth.logout", p.sub, {}, tenant=p.tenant_id, actor=p.actor, name=p.name, request=request)
    return {"signed_out": True}


@router.post("/logout-all")
async def logout_all(request: Request, p: Principal = Depends(get_principal)) -> dict:
    """Sign out every device, this one included."""
    if p.kind == "service":
        return {"signed_out": False}
    await bump_generation(p.kind, p.sub)
    await security_event("auth.logout_all", p.sub, {}, tenant=p.tenant_id, actor=p.actor, name=p.name, request=request)
    return {"signed_out": True}


@router.post("/verify-pin")
async def verify_pin(body: PinCheck, request: Request, p: Principal = Depends(require_user)) -> dict:
    """The app's lock screen. Counted like a sign-in: five wrong PINs lock the account and end this session."""
    u = await db.fetchrow("SELECT * FROM users WHERE user_id=$1", to_uuid(p.sub))
    if not u:
        raise ApiError(404, "NOT_FOUND", "Account not found")
    ok = await verify_secret_async(u["pin_hash"], body.pin)
    try:
        await _guard("users", "user_id", u, ok, request, "auth.unlock", WRONG_PIN)
    except ApiError as exc:
        if exc.status_code == 429:               # locked: whoever is holding the phone does not know the PIN
            await revoke_session(p, "locked")
            raise ApiError(401, "UNAUTHENTICATED", "Too many wrong PINs. Sign in again later.") from exc
        raise
    return {"ok": True}


@router.post("/change-pin", dependencies=[Depends(limit_user("change-pin", 5, 600))])
async def change_pin(body: PinChange, request: Request, p: Principal = Depends(require_user)) -> dict:
    u = await db.fetchrow("SELECT * FROM users WHERE user_id=$1", to_uuid(p.sub))
    if not u:
        raise ApiError(404, "NOT_FOUND", "Account not found")
    await _guard("users", "user_id", u, await verify_secret_async(u["pin_hash"], body.current_pin), request,
                 "auth.change_pin", WRONG_PIN)
    if body.new_pin == body.current_pin:
        raise ApiError(400, "WEAK_PIN", "Choose a PIN you have not just used")
    why = weak_pin(body.new_pin)
    if why:
        raise ApiError(400, "WEAK_PIN", why)
    await db.execute("UPDATE users SET pin_hash=$2, pin_changed_at=now() WHERE user_id=$1", u["user_id"],
                     await hash_secret_async(body.new_pin))
    await bump_generation("user", p.sub)       # every other device is signed out; this one gets a new session
    u = await db.fetchrow("SELECT * FROM users WHERE user_id=$1", u["user_id"])
    await security_event("auth.pin_changed", p.sub, {}, tenant=p.tenant_id, actor=p.actor, name=p.name, request=request)
    return {**_session(_user_principal(u), u, "pin"), "user": _user_payload(u)}


@router.post("/change-password", dependencies=[Depends(limit_user("change-password", 5, 600))])
async def change_password(body: PasswordChange, request: Request, p: Principal = Depends(require_staff())) -> dict:
    s = await db.fetchrow("SELECT * FROM staff_users WHERE staff_id=$1", to_uuid(p.sub))
    if not s:
        raise ApiError(404, "NOT_FOUND", "Account not found")
    await _guard("staff_users", "staff_id", s, await verify_secret_async(s["password_hash"], body.current_password),
                 request, "auth.change_password", (403, "WRONG_PASSWORD", "Wrong current password"))
    if body.new_password == body.current_password:
        raise ApiError(400, "WEAK_PASSWORD", "Choose a password you have not just used")
    why = weak_password(body.new_password, s["email"])
    if why:
        raise ApiError(400, "WEAK_PASSWORD", why)
    await db.execute("UPDATE staff_users SET password_hash=$2, password_changed_at=now() WHERE staff_id=$1",
                     s["staff_id"], await hash_secret_async(body.new_password))
    await bump_generation("staff", p.sub)
    s = await db.fetchrow("SELECT * FROM staff_users WHERE staff_id=$1", s["staff_id"])
    fresh = _staff_principal(s)
    await audit(fresh, "staff.password_changed", p.sub, {"ip": client_ip(request)})
    return {**_session(fresh, s, "password"), "staff": _staff_payload(s)}


@router.post("/device-key", dependencies=[Depends(limit_user("device-key", 5, 600))])
async def enrol_device(body: DeviceEnrol, request: Request, p: Principal = Depends(require_user)) -> dict:
    """Enrol this copy of the app for Step-Up by fingerprint or face. It needs the PIN, so a stolen token
    cannot enrol a device of its own."""
    u = await db.fetchrow("SELECT * FROM users WHERE user_id=$1", to_uuid(p.sub))
    if not u:
        raise ApiError(404, "NOT_FOUND", "Account not found")
    await _guard("users", "user_id", u, await verify_secret_async(u["pin_hash"], body.pin), request, "auth.device_key",
                 WRONG_PIN)
    await _enrol(u["user_id"], body.device)
    await security_event("auth.device_enrolled", p.sub, {"key_id": body.device.key_id[:12]}, tenant=p.tenant_id,
                         actor=p.actor, name=p.name, request=request)
    return {"enrolled": True, "key_id": body.device.key_id}


@router.get("/devices")
async def devices(p: Principal = Depends(require_user)) -> dict:
    rows = await db.fetch("SELECT key_id, label, created_at, last_used FROM device_keys WHERE user_id=$1 "
                          "ORDER BY created_at DESC", to_uuid(p.sub))
    return {"devices": [{"key_id": r["key_id"], "label": r["label"], "created_at": r["created_at"].isoformat(),
                         "last_used": r["last_used"].isoformat() if r["last_used"] else None} for r in rows]}


@router.delete("/devices/{key_id}")
async def forget_device(key_id: str, request: Request, p: Principal = Depends(require_user)) -> dict:
    n = await db.execute("DELETE FROM device_keys WHERE user_id=$1 AND key_id=$2", to_uuid(p.sub), key_id)
    await security_event("auth.device_removed", p.sub, {"key_id": key_id[:12]}, tenant=p.tenant_id, actor=p.actor,
                         name=p.name, request=request)
    return {"removed": not n.endswith(" 0")}
