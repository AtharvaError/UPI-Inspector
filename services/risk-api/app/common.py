"""Small helpers shared by the routers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import Depends, Header

from . import db
from .security import ApiError, Principal, get_principal, require_staff

TENANTS = ("bank_a", "bank_b")


def clean(v: Any) -> Any:
    """Make database values JSON-friendly."""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if hasattr(v, "keys") and hasattr(v, "values"):      # asyncpg Record
        return {k: clean(x) for k, x in dict(v).items()}
    return v


def to_uuid(v: Any, what: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "VALIDATION_ERROR", f"{what} is not a valid identifier") from exc


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def audit(p: Principal, action: str, subject: str, detail: dict | None = None, tenant: str | None = None) -> None:
    await db.execute(
        "INSERT INTO audit_log(tenant_id,actor,actor_name,action,subject,detail) VALUES($1,$2,$3,$4,$5,$6)",
        tenant or p.tenant_id, p.actor, p.name, action, subject, detail or {})


def staff_scope(*roles: str):
    """Staff dependency that also resolves which bank the request is about.

    Prototype convenience: an admin may look at either bank by sending X-Tenant (the dashboard's
    bank switcher). Everyone else is confined to their own bank.
    """

    async def dep(p: Principal = Depends(require_staff(*roles)), x_tenant: str | None = Header(default=None)) -> Principal:
        if x_tenant and x_tenant != p.tenant_id:
            if p.role != "admin" or x_tenant not in TENANTS:
                raise ApiError(403, "FORBIDDEN", "You cannot view another bank")
            return Principal(kind=p.kind, sub=p.sub, tenant_id=x_tenant, role=p.role, name=p.name)
        return p

    return dep


async def any_principal(p: Principal = Depends(get_principal)) -> Principal:
    return p
