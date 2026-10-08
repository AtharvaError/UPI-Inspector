"""Timed jobs: the mule-network graph (F-20) and drift and fairness monitoring (F-26)."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

import numpy as np

from .. import db
from concurrent.futures import ProcessPoolExecutor

from ..core.features import NUMERIC
from ..core.graphfeat import graph_work
from ..realtime import emit_analyst
from ..runtime import rt

log = logging.getLogger("workers")
_graph_state = {"runs": 0, "comm": {}, "keys": set(), "last": 0.0, "seconds": 0.0, "nodes": 0, "where": ""}
_pool: ProcessPoolExecutor | None = None


async def _compute(*args) -> tuple[dict, dict]:
    """The graph is pure Python and takes around a second. In a thread it would hold the interpreter that
    the payment path needs, so it runs in a separate process. If that process cannot be started or dies,
    a thread is used instead: slower for payments while it runs, but the features stay fresh."""
    global _pool
    try:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=1)
        out = await asyncio.get_running_loop().run_in_executor(_pool, graph_work, *args)
        _graph_state["where"] = "process"
        return out
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("graph process failed; computing in a thread")
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None
        _graph_state["where"] = "thread"
        return await asyncio.to_thread(graph_work, *args)


def stop_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


async def run_graph_job(force_communities: bool = False) -> dict:
    t0 = time.time()
    now = time.time()
    # Read the month's payments in slices. One query for all of them would decode tens of thousands of rows
    # in a single step on the thread that also answers payments.
    edges: list = []
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            async for r in conn.cursor(
                    """SELECT created_at, payer_vpa, payee_vpa, amount FROM transactions
                       WHERE status='SUCCESS' AND created_at > now() - interval '30 days'""", prefetch=1000):
                edges.append((r["created_at"].timestamp(), r["payer_vpa"], r["payee_vpa"], float(r["amount"])))
    links: dict[str, set] = {}
    for d in await db.fetch("SELECT vpa_id, fingerprint FROM vpa_devices"):
        links.setdefault(d["vpa_id"], set()).add(d["fingerprint"])
    reported = {r["vpa_id"] for r in await db.fetch("SELECT DISTINCT vpa_id FROM vpa_reputation WHERE band='REPORTED'")}
    reported |= {r["vpa_id"] for r in await db.fetch("SELECT vpa_id FROM vpas WHERE on_denylist")}
    risky = {r["vpa_id"] for r in await db.fetch("SELECT DISTINCT vpa_id FROM vpa_reputation WHERE band='CAUTION'")}
    merchants = {r["vpa_id"] for r in await db.fetch("SELECT vpa_id FROM vpas WHERE kind='merchant'")}

    do_comm = force_communities or _graph_state["runs"] % 5 == 0 or not _graph_state["comm"]
    feats, comm = await _compute(edges, links, merchants, reported, risky, now, _graph_state["comm"], do_comm)
    _graph_state["comm"] = comm
    stale = _graph_state["keys"] - set(feats)
    for vpa in stale:
        await rt.store.r.delete(f"vpa_graph:{vpa}")
    if stale:
        await db.execute("DELETE FROM graph_features WHERE vpa_id = ANY($1::text[])", list(stale))
    for n, (vpa, g) in enumerate(feats.items()):
        await rt.store.put_graph(vpa, g)
        if n % 50 == 49:
            await asyncio.sleep(0)             # let waiting payments through
    from datetime import datetime, timezone

    at = datetime.fromtimestamp(now, timezone.utc)
    await db.executemany(
        """INSERT INTO graph_features(vpa_id,downstream_hop,shares_device_with_reported,passthrough_ratio,community_id,
              community_risk_share,computed_at) VALUES($1,$2,$3,$4,$5,$6,$7)
           ON CONFLICT (vpa_id) DO UPDATE SET downstream_hop=EXCLUDED.downstream_hop,
              shares_device_with_reported=EXCLUDED.shares_device_with_reported,
              passthrough_ratio=EXCLUDED.passthrough_ratio, community_id=EXCLUDED.community_id,
              community_risk_share=EXCLUDED.community_risk_share, computed_at=EXCLUDED.computed_at""",
        [(vpa, g["downstream_hop"], bool(g["shares_device_with_reported"]), g["passthrough_ratio"], g["community_id"],
          g["community_risk_share"], at) for vpa, g in feats.items()])
    _graph_state.update({"runs": _graph_state["runs"] + 1, "keys": set(feats), "last": now,
                         "seconds": round(time.time() - t0, 2), "nodes": len(feats), "edges": len(edges)})
    return dict(_graph_state, comm=None, keys=None)


def graph_status() -> dict:
    return {k: v for k, v in _graph_state.items() if k not in ("comm", "keys")}


async def graph_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await run_graph_job()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("graph job failed")
        await asyncio.sleep(rt.settings.graph_job_interval_s)


# --------------------------------------------------------------------- monitoring --
def _psi(values: np.ndarray, edges: list[float], expected: list[float]) -> float:
    e = np.asarray(edges, dtype=float)
    hist, _ = np.histogram(np.clip(values, e[0], e[-1]), bins=e)
    actual = hist / max(1, hist.sum())
    exp = np.asarray(expected, dtype=float)
    a = np.clip(actual, 1e-4, None)
    x = np.clip(exp, 1e-4, None)
    return float(((a - x) * np.log(a / x)).sum())


async def _fetch_sliced(sql: str, *args) -> list:
    """Read a large result a thousand rows at a time, so the thread that answers payments is never held
    for the whole of it."""
    out: list = []
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            async for r in conn.cursor(sql, *args, prefetch=1000):
                out.append(tuple(r))
    return out


async def run_drift(hours: float = 24.0) -> dict:
    """PSI for the score and the model's most important features against the training baseline."""
    if not (rt.bundle and rt.bundle.baseline):
        return {"status": "no_baseline", "features": []}
    base = rt.bundle.baseline
    # The stored feature record is JSON. Parsing twenty thousand of them here would stop payments being
    # answered for about a second, so PostgreSQL picks out the few values that are compared.
    names = [n for n in base if n in NUMERIC]
    cols = "".join(f", (features->>'{n}')::float8" for n in names)
    rows = await _fetch_sliced(
        f"SELECT score, created_at{cols} FROM risk_decisions WHERE created_at > now() - ($1 || ' hours')::interval "
        "ORDER BY created_at DESC LIMIT 20000", str(hours))
    if len(rows) < 200:
        rows = await _fetch_sliced(f"SELECT score, created_at{cols} FROM risk_decisions ORDER BY created_at DESC LIMIT 5000")
    if len(rows) < 50:
        return {"status": "not_enough_data", "features": []}
    report_id = uuid.uuid4()
    start, end = rows[-1][1], rows[0][1]

    def work() -> list[dict]:
        score = np.array([r[0] for r in rows], dtype=float)
        vals = np.array([[x if x is not None else 0.0 for x in r[2:]] for r in rows], dtype=float).reshape(len(rows), len(names))
        res = []
        for name, b in base.items():
            if name == "__score__":
                v = score
            elif name in names:
                v = vals[:, names.index(name)]
            else:
                continue
            psi = _psi(v, b["edges"], b["expected"])
            res.append({"feature": name, "psi": round(psi, 4),
                        "status": "alert" if psi > 0.2 else "watch" if psi > 0.1 else "stable"})
        return res

    out = await asyncio.to_thread(work)
    await db.executemany(
        "INSERT INTO drift_reports(report_id,model_version,window_start,window_end,feature,psi,status) "
        "VALUES($1,$2,$3,$4,$5,$6,$7)",
        [(report_id, rt.model_version, start, end, o["feature"], o["psi"], o["status"]) for o in out])
    for o in out:
        if o["status"] == "alert":
            for tenant in ("bank_a", "bank_b"):
                await emit_analyst(tenant, "drift.alert", o)
    return {"status": "ok", "report_id": str(report_id), "n": len(rows), "features": sorted(out, key=lambda o: -o["psi"])}


async def run_fairness(days: float = 15.0) -> dict:
    """Among legitimate payments: how often is each group of payers interrupted?"""
    rows = await db.fetch(
        """SELECT u.age_band, u.segment, u.locale, d.score
           FROM risk_decisions d JOIN transactions t ON t.txn_id=d.txn_id JOIN users u ON u.user_id=d.user_id
           LEFT JOIN decision_labels l ON l.decision_id=d.decision_id
           WHERE d.created_at > now() - ($1 || ' days')::interval
             AND (l.label='LEGITIMATE' OR (l.label IS NULL AND t.truth_is_fraud IS FALSE))""", str(days))
    if not rows:
        return {"status": "not_enough_data", "dimensions": []}
    pol = await db.fetchrow("SELECT t_warning,t_stepup,t_block FROM policy_config LIMIT 1")
    tw, ts, tb = pol["t_warning"], pol["t_stepup"], pol["t_block"]
    report_id = uuid.uuid4()
    dims, inserts = [], []
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    for dim in ("age_band", "segment", "locale"):
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r[dim], []).append(r["score"])
        segs = []
        for name, scores in sorted(groups.items()):
            a = np.asarray(scores)
            seg = {"segment": name, "n_legit": int(len(a)), "share_warning": float(((a >= tw) & (a < ts)).mean()),
                   "share_stepup": float(((a >= ts) & (a < tb)).mean()), "share_block": float((a >= tb).mean()),
                   "fpr_stepup_plus": float((a >= ts).mean())}
            segs.append(seg)
            inserts.append((report_id, rt.model_version, start, end, dim, name, seg["n_legit"], seg["share_warning"],
                            seg["share_stepup"], seg["share_block"], seg["fpr_stepup_plus"]))
        usable = [s["fpr_stepup_plus"] for s in segs if s["n_legit"] >= 200]
        ratio = (max(usable) / max(min(usable), 1e-4)) if len(usable) >= 2 and max(usable) > 0 else 1.0
        dims.append({"dimension": dim, "segments": segs, "disparity_ratio": round(float(ratio), 2),
                     "flagged": bool(ratio > 1.5 and max(usable or [0]) >= 0.002)})
    await db.executemany(
        """INSERT INTO fairness_reports(report_id,model_version,window_start,window_end,dimension,segment,n_legit,
              share_warning,share_stepup,share_block,fpr_stepup_plus) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""", inserts)
    return {"status": "ok", "report_id": str(report_id), "n": len(rows), "dimensions": dims}


async def monitor_loop() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await run_drift()
            await run_fairness()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("monitor run failed")
        await asyncio.sleep(rt.settings.monitor_interval_s)
