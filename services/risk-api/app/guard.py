"""The first thing every HTTP request meets: Host check, HTTPS check, body-size limit, a per-address rate
limit and the security headers on the way out (docs/11, section 1.5).

Written as plain ASGI rather than as a Starlette middleware class: it runs on the hot path, and this way it
adds a dictionary lookup and one cache increment to a request, not a task and a stream copy.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from .config import Settings
from .security import ip_class, service_name

# Pages that load scripts and styles of their own: the interactive API documentation, on in demo mode only.
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
# Not counted against the rate limit: probes, and the load balancer's view of health.
UNLIMITED = ("/healthz", "/readyz", "/metrics")
LOCAL_NAMES = ("localhost", "127.0.0.1", "::1", "[::1]")
_PRIVATE = (r"(?:localhost|127\.0\.0\.1|\[::1\]|[a-z0-9-]+\.localhost|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            r"|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})")
# A web page served from this machine or from a private address: the dashboard, and the app's web build.
LOCAL_ORIGIN = rf"^https?://{_PRIVATE}(?::\d{{1,5}})?$"
_LOCAL_ORIGIN_RE = re.compile(LOCAL_ORIGIN)

HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
NO_STORE = (b"cache-control", b"no-store")
HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


def cors_setting(value: str) -> tuple[list[str], str | None]:
    """(explicit origins, origin pattern) for CORS_ORIGINS."""
    v = (value or "local").strip()
    if v.lower() == "local":
        return [], LOCAL_ORIGIN
    origins = [o.strip().rstrip("/") for o in v.split(",") if o.strip()]
    return (["*"], None) if "*" in origins else (origins, None)


def origin_allowed(origin: str | None, value: str) -> bool:
    """The same decision for the socket server. A request with no Origin is not from a web page (the phone app)."""
    if not origin:
        return True
    origins, pattern = cors_setting(value)
    if "*" in origins:
        return True
    return origin.rstrip("/") in origins or bool(pattern and _LOCAL_ORIGIN_RE.match(origin))


def host_name(host: str) -> str:
    """'192.168.1.5:8000' -> '192.168.1.5'; '[::1]:8000' -> '::1'."""
    h = host.strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


class Guard:
    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.s = settings
        extra = {h.strip().lower() for h in settings.allowed_hosts.split(",") if h.strip()}
        self.any_host = "*" in extra
        public = urlparse(settings.public_base_url).hostname
        self.hosts = extra | ({public.lower()} if public else set())

    def host_ok(self, host: str) -> bool:
        """Answer only to names this service is meant to be reached by. A page on another site that points
        its own name at 127.0.0.1 (DNS rebinding) would otherwise be talking to this API as if it were local."""
        if self.any_host:
            return True
        name = host_name(host)
        if not name:
            return False
        if name in LOCAL_NAMES or name.endswith(".localhost") or name in self.hosts:
            return True
        return ip_class(name) in ("loopback", "private")

    @staticmethod
    async def reject(send, status: int, code: str, message: str, extra: list | None = None) -> None:
        body = json.dumps({"error": {"code": code, "message": message, "details": extra or []}}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                NO_STORE, *HEADERS]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        path = scope["path"]
        peer = scope["client"][0] if scope.get("client") else "-"

        if not self.host_ok(headers.get(b"host", b"").decode("latin-1")):
            return await self.reject(send, 400, "BAD_HOST", "This service does not answer to that host name")
        # The scheme comes from the server, which honours X-Forwarded-Proto only from a proxy it trusts
        # (uvicorn's forwarded-allow-ips). Reading the header here would let any client claim to be on HTTPS.
        https = scope.get("scheme") == "https"
        if self.s.require_https and not https and not (ip_class(peer) == "loopback" and b"x-forwarded-for" not in headers):
            return await self.reject(send, 403, "HTTPS_REQUIRED", "Use HTTPS")

        if scope["method"] in ("POST", "PUT", "PATCH", "DELETE"):
            length = headers.get(b"content-length")
            if length is None:
                if b"transfer-encoding" in headers:
                    return await self.reject(send, 411, "LENGTH_REQUIRED", "Send a Content-Length")
            else:
                try:
                    too_big = int(length) > self.s.max_body_bytes
                except ValueError:
                    return await self.reject(send, 400, "VALIDATION_ERROR", "Bad Content-Length")
                if too_big:
                    return await self.reject(send, 413, "TOO_LARGE", "The request is too large")

        limit = self.s.rate_limit_per_ip
        if limit > 0 and path not in UNLIMITED:
            key = headers.get(b"x-api-key")
            if not (key and service_name(key.decode("latin-1"))):       # a bank's own systems are not throttled here
                try:
                    from .cache import cache

                    r = cache()
                    k = f"rl:ip:{peer}"
                    n = int(await r.incr(k))
                    if n == 1:
                        await r.expire(k, 60)
                    elif n > limit and int(await r.ttl(k)) < 0:
                        await r.expire(k, 60)
                except Exception:
                    n = 0                                               # never fail a request because of the limiter
                if n > limit:
                    return await self.reject(send, 429, "RATE_LIMITED", "Too many requests. Please wait a minute.",
                                             [{"retry_in_s": 60}])

        docs = path.startswith(DOCS_PATHS)

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                out = list(message.get("headers") or [])
                have = {k.lower() for k, _ in out}
                for item in HEADERS:
                    if item[0] not in have:
                        out.append(item)
                if not docs and CSP[0] not in have:
                    out.append(CSP)
                if NO_STORE[0] not in have:
                    out.append(NO_STORE)
                if https:
                    out.append(HSTS)
                message = {**message, "headers": out}
            await send(message)

        await self.app(scope, receive, send_with_headers)
