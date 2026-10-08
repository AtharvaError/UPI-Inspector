"""Real-time channels (Socket.IO). Events are notifications; clients re-fetch over REST for the full record."""
from __future__ import annotations

import logging

import socketio

from .config import get_settings
from .guard import origin_allowed
from .security import ApiError, check_session, read_token, session_deadline

log = logging.getLogger("realtime")


def _origin_ok(origin: str | None, environ: dict | None = None) -> bool:
    return origin_allowed(origin, get_settings().cors_origins)


sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=_origin_ok, logger=False, engineio_logger=False,
                           max_http_buffer_size=65536)
_sockets: dict[str, tuple[str, str, str, int]] = {}     # socket id -> (session id, kind, account, session deadline)


async def _principal(auth: dict | None, sid: str):
    """A socket is opened with the same token as the REST calls and is held to the same checks:
    a deactivated account, a signed-out session or an out-of-date token cannot connect."""
    token = (auth or {}).get("token", "") if isinstance(auth, dict) else ""
    try:
        p = read_token(token)
        await check_session(p)
    except ApiError:
        return None
    _sockets[sid] = (p.sid, p.kind, p.sub, session_deadline(p.auth_time))
    return p


def _forget(sid: str) -> None:
    _sockets.pop(sid, None)


async def _close(sids: list[str]) -> int:
    for sid in sids:
        _sockets.pop(sid, None)
        for ns in ("/user", "/analyst"):
            try:
                await sio.disconnect(sid, namespace=ns)
            except Exception:
                pass
    return len(sids)


async def drop_session(session_id: str) -> int:
    """Close the sockets a signed-out session left open."""
    return await _close([k for k, v in _sockets.items() if v[0] == session_id])


async def drop_account(kind: str, sub: str) -> int:
    """Close every socket of an account: its PIN or password changed, or it was deactivated."""
    return await _close([k for k, v in _sockets.items() if v[1] == kind and v[2] == str(sub)])


async def sweep_sockets(now: float) -> int:
    """Close sockets whose session has reached its absolute end. Called from the scheduler."""
    return await _close([k for k, v in _sockets.items() if v[3] <= now])


class AnalystNamespace(socketio.AsyncNamespace):
    async def on_connect(self, sid, environ, auth):
        p = await _principal(auth, sid)
        if p is None or p.kind != "staff":
            raise ConnectionRefusedError("staff token required")
        await self.enter_room(sid, p.tenant_id)
        if p.role == "admin":                      # prototype: an admin may watch both banks
            for tenant in ("bank_a", "bank_b"):
                await self.enter_room(sid, tenant)

    async def on_disconnect(self, sid, *args):
        _forget(sid)


class UserNamespace(socketio.AsyncNamespace):
    async def on_connect(self, sid, environ, auth):
        p = await _principal(auth, sid)
        if p is None or p.kind != "user":
            raise ConnectionRefusedError("user token required")
        await self.enter_room(sid, p.sub)

    async def on_disconnect(self, sid, *args):
        _forget(sid)


sio.register_namespace(AnalystNamespace("/analyst"))
sio.register_namespace(UserNamespace("/user"))


async def emit_analyst(tenant: str, event: str, data: dict) -> None:
    try:
        await sio.emit(event, {**data, "tenant_id": tenant}, namespace="/analyst", room=tenant)
    except Exception:
        log.exception("emit %s failed", event)


async def emit_user(user_id: str, event: str, data: dict) -> None:
    try:
        await sio.emit(event, data, namespace="/user", room=str(user_id))
    except Exception:
        log.exception("emit %s failed", event)
