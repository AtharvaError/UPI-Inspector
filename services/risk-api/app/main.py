"""UPI Inspector risk API: application factory and lifespan.

Run with:  python manage.py api      (uvicorn target: app.main:asgi)
"""
from __future__ import annotations

import asyncio
import gc
import logging
import sys
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, workers
from .bus import init_bus
from .cache import init_cache
from .config import get_settings
from .core import warm
from .core.rules import RuleEngine
from .core.scoring import ModelBundle, active_model_dir
from .core.state import FeatureStore
from .core.textmodel import TextModel
from .guard import Guard, cors_setting
from .posture import posture_warnings
from .realtime import sio
from .routers import analyst, auth, community, demo, ops, payer, reports, risk, signals, stepup
from .runtime import rt

log = logging.getLogger("upi")


def load_models() -> None:
    s = rt.settings
    text_path = s.model_path / "text.joblib"
    rt.text = TextModel.load(text_path) if text_path.exists() else None
    d = active_model_dir(s.model_path)
    rt.bundle = ModelBundle.load(d).for_serving() if d is not None else None


async def _housekeeping() -> None:
    """Hot reload: pick up an edited rules file or a newly promoted model without a restart."""
    while True:
        await asyncio.sleep(10)
        try:
            if rt.engine.maybe_reload():
                log.info("rules reloaded: %s", rt.engine.version)
            d = active_model_dir(rt.settings.model_path)
            if d is not None and (rt.bundle is None or d.name != rt.bundle.version):
                rt.bundle = ModelBundle.load(d).for_serving()
                log.info("model loaded: %s", rt.bundle.version)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("housekeeping failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s  %(message)s")
    rt.settings = s
    # The graph job computes in a background thread. With Python's default hand-over interval of 5 ms, that
    # thread takes the interpreter for 5 ms every time the request thread waits on the network, and payments
    # queue for the length of the job. A 0.5 ms interval keeps a decision within a few milliseconds of normal.
    sys.setswitchinterval(0.0005)
    # The settings have placeholder secrets so that tools can import the code without a .env file.
    # A service that signs tokens with them must not run outside a demo.
    placeholders = [k for k in ("jwt_secret", "phone_hash_key", "consortium_key") if getattr(s, k) == "change-me"]
    if placeholders:
        if not s.demo_mode:
            raise RuntimeError(f"Placeholder secrets in use ({', '.join(placeholders)}). Run:  python manage.py setup")
        log.warning("placeholder secrets in use (%s): run  python manage.py setup", ", ".join(placeholders))
    for line in posture_warnings(s):
        log.warning("security: %s", line)
    await db.init_pool()
    if not await db.fetchval("SELECT to_regclass('public.users')"):
        raise RuntimeError("The database is empty. Run:  python manage.py bootstrap")
    applied = await db.apply_additions()
    if applied:
        log.info("schema additions applied: %s", ", ".join(applied))
    cache = init_cache(s.redis_url)
    bus = init_bus(s.event_bus, cache, s.kafka_brokers)
    rt.store = FeatureStore(cache, demo_mode=s.demo_mode)
    rt.engine = RuleEngine()
    rt.sio = sio
    load_models()
    counts = await warm.warm_all()
    log.info("cache warmed from PostgreSQL: %s", counts)
    log.info("model=%s rules=%s text=%s", rt.model_version, rt.engine.version, rt.text.version if rt.text else None)
    if s.workers_in_process:
        workers.subscribe()
    await bus.start()
    if s.workers_in_process:
        workers.start_loops()
    house = asyncio.create_task(_housekeeping())
    # Everything loaded so far (models, the warmed cache) lives for the life of the process. Freezing it
    # keeps the garbage collector from walking those objects again and again while payments are answered.
    gc.collect()
    gc.freeze()
    rt.ready = True
    try:
        yield
    finally:
        rt.ready = False
        house.cancel()
        await workers.stop_loops()
        await bus.stop()
        await db.close_pool()


def create_app() -> FastAPI:
    s = get_settings()
    # The interactive API documentation lists every endpoint. It is a demo aid, not something to publish.
    app = FastAPI(title="UPI Inspector", version="2.2", lifespan=lifespan,
                  description="Real-time, explainable UPI fraud prevention. Prototype on synthetic data.",
                  docs_url="/docs" if s.demo_mode else None, redoc_url=None,
                  openapi_url="/openapi.json" if s.demo_mode else None)
    # Added first, so it sits inside CORS: a refusal from the guard still reaches the page that asked.
    app.add_middleware(Guard, settings=s)
    origins, pattern = cors_setting(s.cors_origins)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_origin_regex=pattern,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "X-Tenant", "X-API-Key"],
                       allow_credentials=False, max_age=600)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) and "code" in exc.detail else {
            "code": {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(exc.status_code, "ERROR"),
            "message": str(exc.detail), "details": []}
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        msg = f"{details[0]['field']}: {details[0]['message']}" if details else "Invalid request"
        return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": msg, "details": details}}, status_code=400)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"error": {"code": "INTERNAL", "message": "Something went wrong", "details": []}},
                            status_code=500)

    for r in (auth.router, risk.router, stepup.router, signals.router, payer.router, community.router, reports.router,
              analyst.router, ops.router, demo.router):
        app.include_router(r)

    @app.get("/")
    async def root() -> dict:
        return {"service": "UPI Inspector risk API", "docs": "/docs" if s.demo_mode else None, "ready": rt.ready}

    return app


app = create_app()
asgi = socketio.ASGIApp(sio, other_asgi_app=app)
