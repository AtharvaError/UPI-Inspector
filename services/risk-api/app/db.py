"""PostgreSQL access (asyncpg). The system of record."""
from __future__ import annotations

import json
from typing import Any

import asyncpg

from .config import ROOT, Settings, get_settings

_pool: asyncpg.Pool | None = None


def resolve_urls(settings: Settings | None = None) -> tuple[str, str]:
    """Return (application database URL, maintenance database URL).

    DATABASE_URL=pgserver starts (or attaches to) the embedded PostgreSQL that ships in the
    `pgserver` wheel, so the prototype needs no Docker and no system service.
    """
    s = settings or get_settings()
    if s.database_url.startswith("pgserver"):
        import pgserver

        data_dir = ROOT / s.pg_data_dir
        data_dir.parent.mkdir(parents=True, exist_ok=True)
        # pgserver waits only 10 s for PostgreSQL to start. After an unclean shutdown (a reboot, say) crash
        # recovery can take longer, and the server finishes starting on its own; so try again for a while.
        import time

        last: Exception | None = None
        for attempt in range(12):
            try:
                srv = pgserver.get_server(data_dir, cleanup_mode=None)
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt == 0:
                    print("PostgreSQL is starting (recovering after an unclean shutdown can take a minute)...", flush=True)
                time.sleep(8)
        else:
            raise RuntimeError(f"PostgreSQL did not start; see {data_dir / 'log'}") from last
        base = srv.get_uri()
    else:
        base = s.database_url
    root = base.rsplit("/", 1)[0]
    return f"{root}/{s.database_name}", f"{root}/postgres"


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=_dumps, decoder=json.loads, schema="pg_catalog")


def _dumps(v: Any) -> str:
    return json.dumps(v, default=str)


async def ensure_database(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    app_url, admin_url = resolve_urls(s)
    conn = await asyncpg.connect(admin_url)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", s.database_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{s.database_name}"')
    finally:
        await conn.close()
    return app_url


async def init_pool(min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = await ensure_database()
        _pool = await asyncpg.create_pool(url, min_size=min_size, max_size=max_size, init=_init_conn)
    return _pool


async def apply_additions() -> list[str]:
    """Apply the schema files that follow 001: tables and columns added since the first build.
    They are idempotent, so this runs at every start and an existing database needs no rebuild."""
    done = []
    for path in sorted((ROOT / "infra" / "postgres").glob("0*.sql")):
        if path.name.startswith("001_"):
            continue
        async with pool().acquire() as conn:
            await conn.execute(path.read_text(encoding="utf-8"))
        done.append(path.name)
    return done


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not initialised")
    return _pool


async def fetch(sql: str, *args: Any) -> list[asyncpg.Record]:
    return await pool().fetch(sql, *args)


async def fetchrow(sql: str, *args: Any) -> asyncpg.Record | None:
    return await pool().fetchrow(sql, *args)


async def fetchval(sql: str, *args: Any) -> Any:
    return await pool().fetchval(sql, *args)


async def execute(sql: str, *args: Any) -> str:
    return await pool().execute(sql, *args)


async def executemany(sql: str, rows: list[tuple]) -> None:
    if rows:
        await pool().executemany(sql, rows)


def rows(recs: list[asyncpg.Record]) -> list[dict]:
    return [dict(r) for r in recs]


def row(rec: asyncpg.Record | None) -> dict | None:
    return dict(rec) if rec is not None else None
