#!/usr/bin/env python
"""UPI Inspector task runner (the Makefile equivalent, so it works on Windows without make).

    python manage.py setup         create .env with fresh random secrets
    python manage.py bootstrap     setup + text model + simulate + train + load everything into PostgreSQL
    python manage.py api           run the API (with its workers) on http://localhost:8000
    python manage.py test          unit tests
    python manage.py preflight     everything a demo needs, checked in one go
    python manage.py --help        every task
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "services" / "risk-api"))
sys.path.insert(0, str(ROOT / "ml"))
os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _seed_pin() -> str:
    """A random six-digit PIN that passes the app's own PIN rules."""
    from app.security import weak_pin

    while True:
        pin = f"{secrets.randbelow(900000) + 100000}"
        if weak_pin(pin) is None:
            return pin


def task_setup(args: argparse.Namespace) -> None:
    env = ROOT / ".env"
    if env.exists() and not args.force:
        print(".env already exists (use --force to regenerate; that invalidates stored hashes)")
        return
    tpl = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = {
        "JWT_SECRET": secrets.token_urlsafe(48), "PHONE_HASH_KEY": secrets.token_urlsafe(32),
        "CONSORTIUM_KEY": secrets.token_urlsafe(32),
        "SERVICE_API_KEYS": f"simulator:{secrets.token_urlsafe(24)},mockpsp:{secrets.token_urlsafe(24)}",
        "SEED_STAFF_PASSWORD": secrets.token_urlsafe(9),
        "SEED_USER_PIN": _seed_pin(),
    }
    out = []
    for line in tpl.splitlines():
        key = line.split("=", 1)[0].strip()
        out.append(f"{key}={values[key]}" if key in values else line)
    env.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("Wrote .env with fresh secrets. Seeded sign-in credentials are in SEED_STAFF_PASSWORD and SEED_USER_PIN.")


def task_db(args: argparse.Namespace) -> None:
    from app.db import resolve_urls

    app_url, _ = resolve_urls()
    print("PostgreSQL is running:", app_url)


def task_db_stop(args: argparse.Namespace) -> None:
    from app.config import get_settings

    s = get_settings()
    if not s.database_url.startswith("pgserver"):
        print("DATABASE_URL is external; nothing to stop")
        return
    import pgserver

    srv = pgserver.get_server(ROOT / s.pg_data_dir, cleanup_mode="stop")
    srv.cleanup()
    print("PostgreSQL stopped")


def task_text(args: argparse.Namespace) -> None:
    from upi_ml.text_train import train_text

    train_text()


def task_simulate(args: argparse.Namespace) -> None:
    from upi_ml.pipeline import simulate

    asyncio.run(simulate(n_users=args.users, days=args.days, n_campaigns=args.campaigns, seed=args.seed,
                         load_db=not args.no_db))


def task_train(args: argparse.Namespace) -> None:
    from upi_ml.pipeline import train_and_register

    asyncio.run(train_and_register(promote=not args.no_promote, register=not args.no_db))


def task_backfill(args: argparse.Namespace) -> None:
    from upi_ml.pipeline import backfill

    asyncio.run(backfill())


def task_bootstrap(args: argparse.Namespace) -> None:
    if not (ROOT / ".env").exists():
        task_setup(argparse.Namespace(force=False))
    from app.config import get_settings

    get_settings.cache_clear()
    task_text(args)
    from upi_ml.pipeline import backfill, simulate, train_and_register

    async def run() -> None:
        await simulate(n_users=args.users, days=args.days, n_campaigns=args.campaigns, seed=args.seed, load_db=True)
        await train_and_register(promote=True, register=True)
        await backfill()

    asyncio.run(run())
    print("\nBootstrap complete. Start the API with:  python manage.py api")


def task_api(args: argparse.Namespace) -> None:
    import uvicorn

    # server_header=False: responses do not say which server software (and so which known bugs) is behind them.
    # h11_max_incomplete_event_size caps the size of request headers.
    uvicorn.run("app.main:asgi", host=args.host, port=args.port, reload=False, log_level="info",
                app_dir=str(ROOT / "services" / "risk-api"), server_header=False, timeout_keep_alive=15,
                h11_max_incomplete_event_size=16384)


def task_test(args: argparse.Namespace) -> None:
    cmd = [sys.executable, "-m", "pytest", "-q", str(ROOT / "services" / "risk-api" / "tests")] + args.rest
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "services" / "risk-api"), str(ROOT / "ml")]))
    sys.exit(subprocess.call(cmd, env=env, cwd=str(ROOT)))


def task_golden(args: argparse.Namespace) -> None:
    from upi_ml.golden import run_golden

    sys.exit(0 if asyncio.run(run_golden(verbose=True)) else 1)


def task_load(args: argparse.Namespace) -> None:
    from upi_ml.loadtest import run_load

    asyncio.run(run_load(base=args.base, rps=args.rps, seconds=args.seconds))


def task_e2e(args: argparse.Namespace) -> None:
    from upi_ml.e2e import run_e2e

    sys.exit(0 if run_e2e(args.base, only=args.only, slow=not args.fast) else 1)


def task_dashboard(args: argparse.Namespace) -> None:
    """Runs Next.js through node directly, so it works where npm is not on PATH."""
    d = ROOT / "apps" / "dashboard"
    cmd = ["node", str(d / "node_modules" / "next" / "dist" / "bin" / "next"), "dev" if args.dev else "start", "-p", str(args.port)]
    if not args.dev and not (d / ".next" / "BUILD_ID").exists():
        subprocess.check_call(["node", str(d / "node_modules" / "next" / "dist" / "bin" / "next"), "build"], cwd=str(d))
    sys.exit(subprocess.call(cmd, cwd=str(d)))


def task_app(args: argparse.Namespace) -> None:
    """Runs the Expo dev server through node directly. Scan the QR code with Expo Go, or press w for web."""
    d = ROOT / "apps" / "mobile"
    cmd = ["node", str(d / "node_modules" / "expo" / "bin" / "cli"), "start"] + (["--web"] if args.web else []) + ["--port", str(args.port)]
    env = dict(os.environ, EXPO_NO_TELEMETRY="1")
    sys.exit(subprocess.call(cmd, cwd=str(d), env=env))


def task_demo_reset(args: argparse.Namespace) -> None:
    from upi_ml.pipeline import demo_reset

    asyncio.run(demo_reset(args.base))


def task_preflight(args: argparse.Namespace) -> None:
    from upi_ml.checks import preflight

    sys.exit(0 if preflight(args.base) else 1)


def task_security_check(args: argparse.Namespace) -> None:
    from upi_ml.checks import security_check

    sys.exit(0 if security_check(args.base) else 1)


def task_i18n_check(args: argparse.Namespace) -> None:
    from upi_ml.checks import i18n_check

    sys.exit(0 if i18n_check(write=not args.no_sheet) else 1)


def task_i18n_apply(args: argparse.Namespace) -> None:
    from upi_ml.checks import i18n_apply

    sys.exit(0 if i18n_apply(args.sheet) else 1)


def task_phone_check(args: argparse.Namespace) -> None:
    from upi_ml.checks import phone_check

    sys.exit(0 if phone_check(args.base, export=args.export) else 1)


def task_llm_check(args: argparse.Namespace) -> None:
    from upi_ml.checks import llm_check

    sys.exit(0 if llm_check(live=args.live) else 1)


def task_bus_check(args: argparse.Namespace) -> None:
    from upi_ml.checks import bus_check

    sys.exit(0 if bus_check(args.url, args.events) else 1)


def task_redis_emulator(args: argparse.Namespace) -> None:
    from upi_ml.checks import redis_emulator

    redis_emulator(args.port)


def task_export_openapi(args: argparse.Namespace) -> None:
    import yaml

    from app.main import create_app

    spec = create_app().openapi()
    out = ROOT / "contracts" / "openapi.yaml"
    out.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print("wrote", out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="task", required=True)

    def add(name: str, fn, help_: str):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    add("setup", task_setup, "create .env with random secrets").add_argument("--force", action="store_true")
    add("db", task_db, "start the embedded PostgreSQL and print its URL")
    add("db-stop", task_db_stop, "stop the embedded PostgreSQL")
    add("text", task_text, "train the text classifier")
    for name, fn, help_ in (("simulate", task_simulate, "generate synthetic history, replay it and load PostgreSQL"),
                            ("bootstrap", task_bootstrap, "everything needed for a first run")):
        sp = add(name, fn, help_)
        sp.add_argument("--users", type=int, default=800)
        sp.add_argument("--days", type=int, default=90)
        sp.add_argument("--campaigns", type=int, default=66)
        sp.add_argument("--seed", type=int, default=7)
        sp.add_argument("--no-db", action="store_true")
    sp = add("train", task_train, "train, calibrate, evaluate, register; promote if the gates pass")
    sp.add_argument("--no-promote", action="store_true")
    sp.add_argument("--no-db", action="store_true")
    add("backfill", task_backfill, "score the last 15 days of history into decisions (shadow)")
    sp = add("api", task_api, "run the API and its workers")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    add("test", task_test, "run the test suite").add_argument("rest", nargs="*")
    add("golden", task_golden, "run the golden scenarios")
    sp = add("load", task_load, "load test the assess endpoint")
    sp.add_argument("--base", default="http://localhost:8000")
    sp.add_argument("--rps", type=int, default=100)
    sp.add_argument("--seconds", type=int, default=60)
    add("demo-reset", task_demo_reset, "restore the seeded demo state").add_argument("--base", default="http://localhost:8000")
    sp = add("e2e", task_e2e, "run the acceptance tests against a running API")
    sp.add_argument("--base", default="http://localhost:8000")
    sp.add_argument("--only", default="", help="comma-separated test ids, e.g. AT-3,AT-11")
    sp.add_argument("--fast", action="store_true", help="skip the tests that wait on timers")
    sp = add("dashboard", task_dashboard, "run the analyst dashboard on http://localhost:3000")
    sp.add_argument("--dev", action="store_true")
    sp.add_argument("--port", type=int, default=3000)
    sp = add("app", task_app, "run the payer app (Expo)")
    sp.add_argument("--web", action="store_true")
    sp.add_argument("--port", type=int, default=8081)
    add("export-openapi", task_export_openapi, "write contracts/openapi.yaml from the running code")
    add("preflight", task_preflight, "everything a demo needs, checked in one go").add_argument("--base", default="http://localhost:8000")
    add("security-check", task_security_check, "configuration, front door and who-may-call-what").add_argument("--base", default="http://localhost:8000")
    add("i18n-check", task_i18n_check, "English and Hindi strings line up; writes docs/hindi-review.csv").add_argument("--no-sheet", action="store_true")
    add("i18n-apply", task_i18n_apply, "put a reviewer's corrections from the sheet back into the code").add_argument("sheet")
    sp = add("phone-check", task_phone_check, "is the payer app ready for a real phone")
    sp.add_argument("--base", default="http://localhost:8000")
    sp.add_argument("--export", action="store_true", help="also build the Android bundle (about two minutes)")
    add("llm-check", task_llm_check, "case-summary providers").add_argument("--live", action="store_true", help="make one real call per configured provider")
    sp = add("bus-check", task_bus_check, "run the Redis event bus")
    sp.add_argument("--url", default="", help="redis://host:port/0; default is REDIS_URL from .env")
    sp.add_argument("--events", type=int, default=500)
    add("redis-emulator", task_redis_emulator, "a Redis look-alike on a TCP port, for trying REDIS_URL=redis://...").add_argument("--port", type=int, default=6390)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
