"""Load test for POST /v1/risk/assess (docs/10, section 4):  python manage.py load --rps 100 --seconds 60

Replays simulated payments (99% legitimate, 1% scam) at a fixed rate and reports the server's own latency
histogram as well as what the client saw.

The client is deliberately plain: a fixed set of keep-alive connections writing pre-encoded requests. A
general HTTP library costs several milliseconds of processor time per request, and on a laptop the load
generator shares the processor with the server it is measuring.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from urllib.parse import urlparse

import httpx
import numpy as np

from app import db
from app.config import get_settings

MARK = b"@@TXN@@"


async def _sample(n: int) -> list[dict]:
    await db.init_pool()
    rows = await db.fetch(
        """SELECT user_id, payer_vpa, payee_vpa, amount, note, claimed_name, device_id, session, truth_is_fraud
           FROM transactions WHERE is_synthetic AND truth_is_fraud IS NOT NULL AND created_at > now() - interval '25 days'
           ORDER BY random() LIMIT $1""", n * 2)
    await db.close_pool()
    legit = [dict(r) for r in rows if not r["truth_is_fraud"]]
    fraud = [dict(r) for r in rows if r["truth_is_fraud"]]
    k = max(1, n // 100)
    picked = legit[: n - k] + fraud[:k]
    random.shuffle(picked)
    return picked


def _encode(row: dict) -> bytes:
    return json.dumps({
        "txn_id": MARK.decode(),
        "payer": {"user_id": str(row["user_id"]), "vpa": row["payer_vpa"],
                  "device_id": str(row["device_id"]) if row["device_id"] else None},
        "payee": {"vpa": row["payee_vpa"], "claimed_name": row["claimed_name"]},
        "amount": float(row["amount"]), "note": row["note"], "session": row["session"] or {}}).encode()


async def _read(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    head = await reader.readuntil(b"\r\n\r\n")
    status = int(head[9:12])
    low = head.lower()
    at = low.find(b"content-length:")
    n = int(low[at + 15: low.find(b"\r\n", at)]) if at >= 0 else 0
    return status, (await reader.readexactly(n) if n else b"")


async def run_load(base: str = "http://localhost:8000", rps: int = 100, seconds: int = 60, connections: int = 32) -> dict:
    s = get_settings()
    key = next(iter(s.api_keys), "")
    u = urlparse(base)
    host, port = u.hostname or "localhost", u.port or 80
    total = rps * seconds
    bodies = [_encode(r) for r in await _sample(min(total, 4000))]
    head = (f"POST /v1/risk/assess HTTP/1.1\r\nHost: {host}:{port}\r\nX-API-Key: {key}\r\n"
            f"Content-Type: application/json\r\nContent-Length: ").encode()

    async with httpx.AsyncClient(base_url=base, headers={"X-API-Key": key}, timeout=20) as c:
        r = await c.post("/v1/demo/metrics-reset")
        if r.status_code != 200:
            print("could not reset server metrics:", r.status_code, r.text[:200])

    svc: list[float] = []         # send to response
    late: list[float] = []        # scheduled time to response: what a caller arriving on schedule would wait
    codes: dict[int, int] = {}
    tiers: dict[str, int] = {}
    state = {"next": 0, "degraded": 0}

    # Connect before the clock starts. On Windows "localhost" is tried over IPv6 first, which takes about a
    # second to fail against a server listening on IPv4; counted inside the test, that looked like a stall.
    t_conn = time.perf_counter()
    pool = await asyncio.gather(*(asyncio.open_connection(host, port) for _ in range(connections)))
    print(f"opened {connections} connections in {time.perf_counter() - t_conn:.2f}s")
    start = time.perf_counter()
    give_up = start + seconds * 2 + 10          # an overloaded server must not make the test run for ever

    async def worker(conn) -> None:
        reader, writer = conn
        while True:
            i = state["next"]
            if i >= total or time.perf_counter() > give_up:
                break
            state["next"] = i + 1
            due = start + i / rps
            wait = due - time.perf_counter()
            if wait > 0:
                await asyncio.sleep(wait)
            body = bodies[i % len(bodies)].replace(MARK, f"LOAD-{uuid.uuid4().hex[:16]}".encode())
            sent = time.perf_counter()
            try:
                if writer is None:
                    reader, writer = await asyncio.open_connection(host, port)
                writer.write(head + str(len(body)).encode() + b"\r\n\r\n" + body)
                await writer.drain()
                status, payload = await asyncio.wait_for(_read(reader), 10)
                codes[status] = codes.get(status, 0) + 1
                if status == 200:
                    at = payload.find(b'"tier":"')
                    tier = payload[at + 8: payload.find(b'"', at + 8)].decode() if at >= 0 else "?"
                    tiers[tier] = tiers.get(tier, 0) + 1
                    state["degraded"] += int(b'"degraded":true' in payload)
            except Exception:
                codes[0] = codes.get(0, 0) + 1
                if writer is not None:
                    writer.close()
                reader = writer = None
            done = time.perf_counter()
            svc.append((done - sent) * 1000)
            late.append((done - due) * 1000)
        if writer is not None:
            writer.close()

    print(f"sending {total:,} assessments at {rps}/s for {seconds}s to {base} over {connections} connections ...")
    await asyncio.gather(*(worker(c) for c in pool))
    took = time.perf_counter() - start
    sent_n = len(svc)

    # the server's own numbers are read as a member of staff, on a client that does not carry the service key
    server: dict = {}
    async with httpx.AsyncClient(base_url=base, timeout=20) as staff:
        login = await staff.post("/v1/auth/demo-staff-login", json={"email": "admin@banka.demo"})
        if login.status_code == 200:
            m = await staff.get("/v1/metrics/summary", headers={"Authorization": f"Bearer {login.json()['token']}"})
            server = m.json() if m.status_code == 200 else {}

    def pct(a: list[float]) -> dict:
        arr = np.asarray(a or [0.0])
        return {"p50": round(float(np.percentile(arr, 50)), 1), "p95": round(float(np.percentile(arr, 95)), 1),
                "p99": round(float(np.percentile(arr, 99)), 1)}

    report = {
        "offered_rps": rps, "planned": total, "sent": sent_n, "seconds": round(took, 1),
        "achieved_rps": round(sent_n / took, 1), "status_codes": codes, "tiers": tiers,
        "client_ms": pct(svc), "from_schedule_ms": pct(late), "client_saw_degraded": state["degraded"],
        "server_ms": server.get("latency_ms", {}), "degraded": server.get("degraded"),
        "timeouts": server.get("timeouts"), "shed": server.get("shed"), "event_backlog": server.get("event_backlog"),
    }
    kept_up = sent_n == total and report["from_schedule_ms"]["p99"] < 1000
    tot = report["server_ms"].get("total", {})
    print("status codes:", codes, "| tiers:", tiers)
    print(f"answered {sent_n:,} of {total:,} in {took:.1f}s = {report['achieved_rps']}/s  "
          f"({'kept up with the schedule' if kept_up else 'FELL BEHIND the schedule'})")
    cm, fs = report["client_ms"], report["from_schedule_ms"]
    print(f"client: request p50/p95/p99 = {cm['p50']}/{cm['p95']}/{cm['p99']} ms | "
          f"from scheduled time = {fs['p50']}/{fs['p95']}/{fs['p99']} ms")
    if tot:
        print(f"SERVER decision p50/p95/p99 = {tot['p50']}/{tot['p95']}/{tot['p99']} ms over {tot['n']:,} decisions "
              f"(budget: p95 150, p99 250) -> {'PASS' if tot['p95'] <= 150 and tot['p99'] <= 250 else 'OVER BUDGET'}")
        for stage in ("read", "features", "rules", "model", "explain"):
            st = report["server_ms"].get(stage)
            if st:
                print(f"   {stage:9s} p50 {st['p50']:6.2f}  p95 {st['p95']:6.2f}  p99 {st['p99']:6.2f}")
    print(f"degraded: {report['degraded']} | timeouts: {report['timeouts']} | shed under overload: {report['shed']} | "
          f"event backlog now: {report['event_backlog']}")
    print("This test writes real decisions. Run  python manage.py demo-reset  afterwards.")
    return report
