"""Shared cache (Redis API).

`fakeredis://` keeps the cache inside the API process, which is what the laptop setup uses:
the API and its workers are one process, so they share it. Any `redis://` URL uses a real Redis.
The cache can always be rebuilt from PostgreSQL (see core/warm.py).
"""
from __future__ import annotations

from typing import Any

_cache: Any = None


def make_cache(url: str) -> Any:
    if url.startswith("fakeredis"):
        import fakeredis

        return fakeredis.FakeAsyncRedis(decode_responses=True)
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)


def init_cache(url: str) -> Any:
    global _cache
    _cache = make_cache(url)
    return _cache


def cache() -> Any:
    if _cache is None:
        raise RuntimeError("cache is not initialised")
    return _cache
