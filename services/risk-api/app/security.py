"""Hashing, tokens, sessions, rate limits and the FastAPI dependencies that enforce who may call what (F-30, F-35)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException, Request

from .config import get_settings

_ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
# Verified when the account does not exist, so an unknown phone number takes as long to refuse as a wrong PIN.
_DUMMY_HASH = _ph.hash("no-such-account")

ISSUER = "upi-inspector"
MAX_FAILED = 5
LOCK_SECONDS = 300
LOCK_MAX_SECONDS = 24 * 3600
# "A device on the same network": the private ranges and link-local addresses, and nothing else. (Python's
# is_private also counts documentation and other reserved ranges, which are not anyone's local network.)
LAN_NETWORKS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                                                  "fc00::/7", "fe80::/10")]
FORWARD_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host", "cf-connecting-ip")

COMMON_PINS = {"123123", "112233", "121212", "123321", "654321", "696969", "159753", "147258", "789456", "102030",
               "131313", "123654", "112211", "101010", "202020", "252525", "007007", "998877", "100000", "200000"}
COMMON_PASSWORDS = {"password", "password1", "password123", "passw0rd", "qwerty123", "qwertyuiop", "1234567890",
                    "letmein123", "welcome123", "admin12345", "administrator", "iloveyou123", "changeme123", "upiinspector"}


def phone_hash(phone: str) -> str:
    """Keyed hash of a phone number. The number itself is never stored."""
    digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
    mac = hmac.new(get_settings().phone_hash_key.encode(), digits.encode(), hashlib.sha256)
    return "h_" + mac.hexdigest()[:30]


def vpa_token(vpa: str) -> str:
    """Keyed hash of a UPI ID, shared between consortium members (F-24)."""
    mac = hmac.new(get_settings().consortium_key.encode(), vpa.strip().lower().encode(), hashlib.sha256)
    return "t_" + mac.hexdigest()[:40]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()[:32]


def keyed(purpose: str, *parts: str) -> str:
    """HMAC under a key derived from the token secret, for short-lived server-side values (one-time codes)."""
    key = hmac.new(get_settings().jwt_secret.encode(), purpose.encode(), hashlib.sha256).digest()
    return hmac.new(key, "\x1f".join(parts).encode(), hashlib.sha256).hexdigest()


def same(a: str | None, b: str | None) -> bool:
    """Compare two secrets without leaking, through timing, how much of them matched."""
    return bool(a) and bool(b) and hmac.compare_digest(str(a).encode(), str(b).encode())


def hash_secret(secret: str) -> str:
    return _ph.hash(secret)


def verify_secret(stored: str | None, secret: str) -> bool:
    try:
        return bool(_ph.verify(stored or _DUMMY_HASH, secret)) and bool(stored)
    except (VerifyMismatchError, Exception):
        return False


async def verify_secret_async(stored: str | None, secret: str) -> bool:
    """Argon2 takes tens of milliseconds by design. Off the event loop, so a sign-in never delays a payment."""
    return await asyncio.to_thread(verify_secret, stored, secret)


async def hash_secret_async(secret: str) -> str:
    return await asyncio.to_thread(hash_secret, secret)


def lock_seconds(lockouts: int) -> int:
    """Five minutes the first time, then twice as long each time, up to a day."""
    return min(LOCK_SECONDS * 2 ** max(0, min(lockouts, 12)), LOCK_MAX_SECONDS)


def weak_pin(pin: str, phone: str = "") -> str | None:
    """Why a PIN is too easy to guess, or None if it will do."""
    if len(set(pin)) == 1:
        return "Do not use the same digit six times"
    steps = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])}
    if steps in ({1}, {9}):
        return "Do not use a run of digits such as 123456"
    if pin[:3] == pin[3:] or pin[:2] * 3 == pin or pin == pin[::-1]:
        return "Do not use a repeated or mirrored pattern"
    if pin in COMMON_PINS:
        return "That PIN is one of the most commonly used"
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits and pin in digits:
        return "Do not use part of your phone number"
    return None


def weak_password(password: str, email: str = "") -> str | None:
    """Why a staff password is too weak, or None."""
    if len(password) < 10:
        return "Use at least 10 characters"
    if len(password) > 200:
        return "Use at most 200 characters"
    low = password.lower()
    if low in COMMON_PASSWORDS or len(set(low)) < 5:
        return "That password is too easy to guess"
    if not (any(c.isalpha() for c in password) and any(not c.isalpha() for c in password)):
        return "Mix letters with digits or symbols"
    local = email.split("@")[0].lower()
    if len(local) >= 4 and local in low:
        return "Do not build the password from the email address"
    return None


# ------------------------------------------------------------------ who is calling --
@dataclass
class Principal:
    kind: str                 # user | staff | service
    sub: str                  # user_id, staff_id or service name
    tenant_id: str
    role: str = ""            # staff: admin | analyst | auditor; user: user
    name: str = ""
    sid: str = ""             # session: stays the same across refreshes; signing out revokes it
    gen: int = 0              # the account's token generation when this token was issued
    amr: str = ""             # how the session was authenticated: pin | password | demo
    auth_time: int = 0
    exp: int = 0

    @property
    def is_staff(self) -> bool:
        return self.kind == "staff"

    @property
    def actor(self) -> str:
        return f"{self.kind}:{self.sub}"


def session_deadline(auth_time: int) -> int:
    return auth_time + get_settings().session_max_hours * 3600


def make_token(p: Principal, *, amr: str = "", gen: int = 0, sid: str | None = None,
               auth_time: int | None = None) -> str:
    """A short-lived token. While the app is in use it is refreshed; left idle it expires, and in any
    case the session ends SESSION_MAX_HOURS after sign-in."""
    s = get_settings()
    now = int(time.time())
    started = auth_time or now
    minutes = s.staff_token_minutes if p.kind == "staff" else s.user_token_minutes
    claims = {"iss": ISSUER, "sub": p.sub, "kind": p.kind, "tenant_id": p.tenant_id, "role": p.role, "name": p.name,
              "iat": now, "exp": min(now + minutes * 60, session_deadline(started)), "jti": uuid.uuid4().hex,
              "sid": sid or uuid.uuid4().hex, "gen": int(gen), "amr": amr or p.amr or "pin", "auth_time": started}
    return jwt.encode(claims, s.jwt_secret, algorithm="HS256")


def read_token(token: str) -> Principal:
    try:
        c = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], issuer=ISSUER,
                       options={"require": ["exp", "iat", "sub", "sid"]})
        return Principal(kind=c["kind"], sub=c["sub"], tenant_id=c["tenant_id"], role=c.get("role", ""),
                         name=c.get("name", ""), sid=c["sid"], gen=int(c.get("gen", 0)), amr=c.get("amr", ""),
                         auth_time=int(c.get("auth_time", c["iat"])), exp=int(c["exp"]))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise ApiError(401, "UNAUTHENTICATED", "Invalid or expired token") from exc


class ApiError(HTTPException):
    """Rendered as {"error": {"code", "message", "details"}} (docs/07, section 1.2)."""

    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(status_code=status, detail={"code": code, "message": message, "details": details or []})


def service_name(key: str | None) -> str | None:
    """The service a key belongs to. Every configured key is compared in constant time."""
    found = None
    for known, name in get_settings().api_keys.items():
        if same(known, key):
            found = name
    return found


async def session_state(kind: str, sub: str, sid: str) -> tuple[bool, bool, int]:
    """(deactivated, session revoked, current token generation). The cache answers; if it cannot, PostgreSQL does."""
    from .cache import cache

    try:
        pipe = cache().pipeline(transaction=False)
        pipe.get(f"deactivated:{kind}:{sub}")
        pipe.get(f"revoked_sid:{sid}")
        pipe.get(f"tgen:{kind}:{sub}")
        dead, revoked, gen = await pipe.execute()
        return bool(dead), bool(revoked), int(gen or 0)
    except Exception:
        from . import db

        table, key = ("staff_users", "staff_id") if kind == "staff" else ("users", "user_id")
        try:
            row = await asyncio.wait_for(db.fetchrow(
                f"SELECT active, token_gen, EXISTS(SELECT 1 FROM revoked_sessions WHERE sid=$2) AS revoked "
                f"FROM {table} WHERE {key}=$1", uuid.UUID(sub), sid), 2.0)
        except Exception as exc:
            raise ApiError(503, "UNAVAILABLE", "Sign-in cannot be checked right now. Try again.") from exc
        if row is None:
            return True, False, 0
        return (not row["active"]), bool(row["revoked"]), int(row["token_gen"])


async def check_session(p: Principal) -> None:
    dead, revoked, gen = await session_state(p.kind, p.sub, p.sid)
    if dead:
        raise ApiError(401, "UNAUTHENTICATED", "This account has been deactivated")
    if revoked:
        raise ApiError(401, "UNAUTHENTICATED", "This session has been signed out")
    if p.gen != gen:
        raise ApiError(401, "UNAUTHENTICATED", "Your sign-in details changed. Please sign in again.")


async def get_principal(authorization: str | None = Header(default=None),
                        x_api_key: str | None = Header(default=None)) -> Principal:
    if x_api_key:
        name = service_name(x_api_key)
        if not name:
            raise ApiError(401, "UNAUTHENTICATED", "Unknown API key")
        return Principal(kind="service", sub=name, tenant_id="bank_a", role="service", name=name)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "UNAUTHENTICATED", "Missing bearer token")
    p = read_token(authorization.split(" ", 1)[1].strip())
    await check_session(p)
    return p


async def require_user(p: Principal = Depends(get_principal)) -> Principal:
    if p.kind != "user":
        raise ApiError(403, "FORBIDDEN", "App user token required")
    return p


async def require_user_or_service(p: Principal = Depends(get_principal)) -> Principal:
    if p.kind not in ("user", "service"):
        raise ApiError(403, "FORBIDDEN", "App user or service credentials required")
    return p


def require_staff(*roles: str):
    """Dependency factory. No roles means any staff member."""

    async def dep(p: Principal = Depends(get_principal)) -> Principal:
        if p.kind != "staff":
            raise ApiError(403, "FORBIDDEN", "Staff token required")
        if roles and p.role not in roles:
            raise ApiError(403, "FORBIDDEN", f"Requires role: {', '.join(roles)}")
        return p

    return dep


async def require_service(p: Principal = Depends(get_principal)) -> Principal:
    if p.kind != "service" and not (p.kind == "staff" and p.role == "admin"):
        raise ApiError(403, "FORBIDDEN", "Service credentials required")
    return p


# ------------------------------------------------------------------ where from --
def client_ip(request: Request) -> str:
    return request.client.host if request.client else "-"


def ip_class(host: str) -> str:
    """loopback | private | public | unknown"""
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return "unknown"
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip.is_loopback:
        return "loopback"
    return "private" if any(ip.version == net.version and ip in net for net in LAN_NETWORKS) else "public"


def forwarded(headers) -> bool:
    """True when the request came through a proxy or a tunnel. Such a request is never treated as local,
    whatever address it appears to come from."""
    return any(h in headers for h in FORWARD_HEADERS)


def demo_login_allowed(request: Request) -> bool:
    """The passwordless demo sign-in: only in demo mode, and only from where DEMO_LOGIN says (docs/11, section 1.1)."""
    s = get_settings()
    scope = s.demo_login.strip().lower()
    if not s.demo_mode or scope in ("", "off", "false", "0", "no"):
        return False
    if scope == "any":
        return True
    if forwarded(request.headers):
        return False
    where = ip_class(client_ip(request))
    return where == "loopback" or (scope == "lan" and where == "private")


async def require_demo_login(request: Request) -> None:
    if not demo_login_allowed(request):
        raise ApiError(404, "NOT_FOUND", "Demo sign-in is not available here")


# ------------------------------------------------------------------ rate limits --
async def rate_limit(bucket: str, key: str, limit: int, window_s: int, message: str | None = None) -> int:
    """Fixed-window counter in the cache. Returns the count so far; raises 429 past the limit.
    A limiter that cannot reach the cache lets the request through: it must never take the service down."""
    if limit <= 0:
        return 0
    from .cache import cache

    k = f"rl:{bucket}:{key}"
    try:
        r = cache()
        n = int(await r.incr(k))
        if n == 1:
            await r.expire(k, window_s)
        if n <= limit:
            return n
        ttl = int(await r.ttl(k))
        if ttl < 0:                                   # the window was never armed: arm it now
            await r.expire(k, window_s)
            ttl = window_s
    except Exception:
        return 0
    raise ApiError(429, "RATE_LIMITED", message or "Too many requests. Please wait and try again.",
                   [{"retry_in_s": ttl}])


def limit_ip(bucket: str, limit: int, window_s: int = 60, message: str | None = None):
    """Dependency: at most `limit` calls per window from one address."""

    async def dep(request: Request) -> None:
        await rate_limit(bucket, client_ip(request), limit, window_s, message)

    return dep


def limit_user(bucket: str, limit: int, window_s: int = 60, message: str | None = None):
    """Dependency: at most `limit` calls per window by one signed-in person. Services are not limited."""

    async def dep(p: Principal = Depends(get_principal)) -> None:
        if p.kind != "service":
            await rate_limit(bucket, f"{p.kind}:{p.sub}", limit, window_s, message)

    return dep
