"""Ending sessions, and the record of security events (F-35).

A token is short-lived and carries its session id and the account's token generation. Signing out revokes
the session; changing a PIN or password, a role change, or "sign out everywhere" raises the generation,
which refuses every token issued before. Both are written to PostgreSQL and mirrored in the cache, so a
restart does not bring a signed-out session back.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import Request

from . import db
from .cache import cache
from .realtime import drop_account, drop_session
from .security import Principal, client_ip, session_deadline

log = logging.getLogger("security")
TABLE = {"user": ("users", "user_id"), "staff": ("staff_users", "staff_id")}


async def security_event(action: str, subject: str, detail: dict | None = None, *, tenant: str | None = None,
                         actor: str = "anon", name: str | None = None, request: Request | None = None) -> None:
    """Sign-in failures, lockouts, sign-outs and credential changes go to the audit log, with the address
    they came from. Writing the record must never be the reason a request fails."""
    d = dict(detail or {})
    if request is not None:
        d.setdefault("ip", client_ip(request))
    try:
        await db.execute(
            "INSERT INTO audit_log(tenant_id,actor,actor_name,action,subject,detail) VALUES($1,$2,$3,$4,$5,$6)",
            tenant, actor, name, action, subject, d)
    except Exception:
        log.exception("could not record %s", action)


async def revoke_session(p: Principal, reason: str = "logout") -> None:
    ends = max(session_deadline(p.auth_time), int(time.time()) + 60)
    await cache().set(f"revoked_sid:{p.sid}", reason, ex=max(60, ends - int(time.time())))
    await db.execute(
        "INSERT INTO revoked_sessions(sid,kind,sub,reason,expires_at) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
        p.sid, p.kind, p.sub, reason, datetime.fromtimestamp(ends, timezone.utc))
    await drop_session(p.sid)


async def bump_generation(kind: str, sub: str) -> int:
    """Refuse every token this account holds. Returns the new generation, for the token issued next."""
    table, key = TABLE[kind]
    gen = await db.fetchval(f"UPDATE {table} SET token_gen = token_gen + 1 WHERE {key}=$1 RETURNING token_gen",
                            uuid.UUID(str(sub)))
    gen = int(gen or 0)
    await cache().set(f"tgen:{kind}:{sub}", gen)
    await drop_account(kind, str(sub))
    return gen


async def load_sessions(r) -> int:
    """Start-up: put revocations and raised generations back in the cache (called from warm_all)."""
    n = 0
    await db.execute("DELETE FROM revoked_sessions WHERE expires_at < now()")
    now = time.time()
    pipe = r.pipeline(transaction=False)
    for row in await db.fetch("SELECT sid, reason, expires_at FROM revoked_sessions"):
        pipe.set(f"revoked_sid:{row['sid']}", row["reason"], ex=max(60, int(row["expires_at"].timestamp() - now)))
        n += 1
    for kind, (table, key) in TABLE.items():
        for row in await db.fetch(f"SELECT {key} AS id, token_gen FROM {table} WHERE token_gen > 0"):
            pipe.set(f"tgen:{kind}:{row['id']}", row["token_gen"])
            n += 1
    if n:
        await pipe.execute()
    return n
