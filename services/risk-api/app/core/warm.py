"""Rebuild the cache from PostgreSQL: all of it at start-up, or one user or payee on a cache miss.

The cache is never the system of record (docs/05). With the in-process cache this runs on every start.
"""
from __future__ import annotations

import json
import time

from .. import db
from ..runtime import rt
from ..security import vpa_token
from ..sessions import load_sessions
from .features import DAY, HOUR, RawState

BATCH = 4000


class _Pipe:
    """Flush a pipeline every BATCH commands."""

    def __init__(self, r) -> None:
        self.r = r
        self.p = r.pipeline(transaction=False)
        self.n = 0

    async def tick(self) -> None:
        self.n += 1
        if self.n >= BATCH:
            await self.flush()

    async def flush(self) -> None:
        if self.n:
            await self.p.execute()
            self.p = self.r.pipeline(transaction=False)
            self.n = 0


def _ts(d) -> float:
    return d.timestamp() if d is not None else 0.0


async def load_policy(tenant: str | None = None) -> None:
    rows = await db.fetch("SELECT * FROM policy_config" + (" WHERE tenant_id=$1" if tenant else ""),
                          *([tenant] if tenant else []))
    for r in rows:
        d = dict(r)
        d.pop("updated_by", None)
        d.pop("updated_at", None)
        await rt.store.put_policy(d.pop("tenant_id"), d)


async def load_guardian(user_id: str) -> None:
    g = await db.fetchrow(
        """SELECT g.*, u.display_name AS guardian_name FROM guardians g JOIN users u ON u.user_id=g.guardian_user_id
           WHERE g.user_id=$1 AND g.status IN ('ACTIVE','REMOVAL_PENDING')""", user_id)
    await rt.store.set_guardian(str(user_id), dict(g) if g else None)


async def index_user(row) -> None:
    rt.user_index[str(row["user_id"])] = {
        "phone_hash": row["phone_hash"], "tenant": row["tenant_id"], "locale": row["locale"],
        "name": row["display_name"], "vpa": row["primary_vpa"]}
    rt.phone_index[row["phone_hash"]] = str(row["user_id"])


async def warm_all() -> dict:
    t0 = time.time()
    r, store = rt.store.r, rt.store
    now = time.time()
    if rt.settings.event_bus == "redis":
        # The event streams live in the same Redis. Emptying everything would take them, and every consumer
        # group, with it; so only the cache's own keys are removed.
        batch: list = []
        async for key in r.scan_iter(match="*", count=1000):
            if not str(key).startswith("stream:"):
                batch.append(key)
                if len(batch) >= 1000:
                    await r.delete(*batch)
                    batch = []
        if batch:
            await r.delete(*batch)
    else:
        await r.flushdb()
    pp = _Pipe(r)
    counts: dict = {}

    users = await db.fetch("SELECT * FROM users")
    for u in users:
        await index_user(u)
        pp.p.hset(f"user_prof:{u['user_id']}", mapping={
            "first_seen": _ts(u["first_seen"]), "phone_hash": u["phone_hash"], "tenant": u["tenant_id"],
            "balance": float(u["balance"]), "locale": u["locale"]})
        if not u["active"]:
            pp.p.set(f"deactivated:user:{u['user_id']}", "1")
        await pp.tick()
    counts["users"] = len(users)
    for s in await db.fetch("SELECT staff_id FROM staff_users WHERE NOT active"):
        pp.p.set(f"deactivated:staff:{s['staff_id']}", "1")

    for d in await db.fetch("SELECT device_id,user_id,first_seen FROM devices"):
        pp.p.hset(f"dev:{d['user_id']}", str(d["device_id"]), _ts(d["first_seen"]))
        await pp.tick()

    known = await db.fetch(
        """SELECT user_id, payee_vpa, count(*) AS c, min(created_at) AS first FROM transactions
           WHERE status='SUCCESS' GROUP BY 1,2""")
    first_paid: dict = {}
    for k in known:
        first_paid[(k["user_id"], k["payee_vpa"])] = _ts(k["first"])
        pp.p.hset(f"known_payee:{k['user_id']}", k["payee_vpa"], f"{k['c']}|{_ts(k['first'])}")
        await pp.tick()
    counts["known_pairs"] = len(known)

    recent = await db.fetch(
        """SELECT tenant_id,user_id,payee_vpa,amount,created_at FROM transactions
           WHERE status='SUCCESS' AND created_at > now() - interval '31 days' ORDER BY created_at""")
    for t in recent:
        ts = _ts(t["created_at"])
        is_new = int(abs(first_paid.get((t["user_id"], t["payee_vpa"]), -1) - ts) < 1e-3)
        pp.p.zadd(f"vel:{t['user_id']}", {f"{ts:.3f}|{float(t['amount']):.2f}|{t['payee_vpa']}|{is_new}": ts})
        if ts >= now - 2 * DAY:
            pp.p.zadd(f"vpa_in:{t['tenant_id']}:{t['payee_vpa']}", {str(t["user_id"]): ts})
            if is_new:
                pp.p.zadd(f"vpa_in1:{t['tenant_id']}:{t['payee_vpa']}", {str(t["user_id"]): ts})
        await pp.tick()
    counts["recent_payments"] = len(recent)

    vpas = await db.fetch("SELECT * FROM vpas")
    for v in vpas:
        pp.p.hset(f"vpa_dir:{v['vpa_id']}", mapping={
            "registered_name": v["registered_name"], "kind": v["kind"], "verified": int(v["verified"]),
            "on_denylist": int(v["on_denylist"]), "first_seen": _ts(v["first_seen"]), "tenant": v["tenant_id"]})
        await pp.tick()
    counts["vpas"] = len(vpas)

    for x in await db.fetch("SELECT tenant_id,vpa_id,recv_decayed,bad_decayed,lifetime_recv,counters_at FROM vpa_reputation"):
        pp.p.hset(f"vpa_rep:{x['tenant_id']}:{x['vpa_id']}", mapping={
            "recv": x["recv_decayed"], "bad": x["bad_decayed"], "at": _ts(x["counters_at"]),
            "lifetime": x["lifetime_recv"]})
        await pp.tick()
    for e in await db.fetch("SELECT id,tenant_id,vpa_id,kind,ref,at FROM vpa_events WHERE at > now() - interval '91 days'"):
        kind = "report" if e["kind"] == "flagged_message" else e["kind"]
        pp.p.zadd(f"vpa_ev:{e['tenant_id']}:{e['vpa_id']}:{kind}", {e["ref"] or str(e["id"]): _ts(e["at"])})
        await pp.tick()

    for g in await db.fetch("SELECT * FROM graph_features"):
        pp.p.hset(f"vpa_graph:{g['vpa_id']}", mapping={
            "downstream_hop": g["downstream_hop"], "shares_device_with_reported": int(g["shares_device_with_reported"]),
            "passthrough_ratio": g["passthrough_ratio"], "community_risk_share": g["community_risk_share"]})
        await pp.tick()

    tenants = [t["tenant_id"] for t in await db.fetch("SELECT tenant_id FROM tenants")]
    for c in await db.fetch("SELECT * FROM consortium_signals"):
        for tenant in tenants:
            if tenant != c["source_tenant"]:
                pp.p.hset(f"consortium:{tenant}:{c['vpa_token']}", mapping={
                    "reported": 1, "p_bad": c["p_bad"], "source_tenant": c["source_tenant"],
                    "confirmed": int(c["confirmed"])})
                await pp.tick()

    for cr in await db.fetch("SELECT caller_hash,risk FROM caller_risk_list"):
        pp.p.set(f"caller_risk:{cr['caller_hash']}", cr["risk"])
        await pp.tick()
    # the payers' own never-pay lists (F-33); 0 = blocked until further notice, otherwise when the block lifts
    blocks = await db.fetch("SELECT user_id, payee_vpa, unblock_at FROM user_blocks WHERE unblock_at IS NULL OR unblock_at > now()")
    for b in blocks:
        pp.p.hset(f"user_block:{b['user_id']}", b["payee_vpa"], _ts(b["unblock_at"]))
        await pp.tick()
    counts["user_blocks"] = len(blocks)
    await pp.flush()
    counts["sessions"] = await load_sessions(r)         # signed-out sessions and raised token generations (F-35)

    for c in await db.fetch("SELECT * FROM call_events WHERE started_at > now() - interval '8 hours' ORDER BY started_at"):
        await store.call_event(phone_hash=c["callee_hash"], call_id=str(c["call_id"]), event="started",
                               caller_known=c["caller_known"], caller_risk=c["caller_risk"], ts=_ts(c["started_at"]))
        if c["ended_at"]:
            await store.call_event(phone_hash=c["callee_hash"], call_id=str(c["call_id"]), event="ended",
                                   caller_known=c["caller_known"], caller_risk=c["caller_risk"], ts=_ts(c["ended_at"]))
    for m in await db.fetch("SELECT * FROM message_checks WHERE flag_set AND created_at > now() - interval '7 days' "
                            "ORDER BY created_at"):
        ts = _ts(m["created_at"])
        vp = (m["entities"] or {}).get("vpas", [])
        if ts >= now - DAY:
            await store.message_flag(user_id=str(m["user_id"]), check_id=str(m["check_id"]), scam_prob=m["scam_prob"],
                                     label=m["label"], family=m["family"], vpas=vp, ts=ts)
        elif vp:
            await r.zadd(f"msg_vpas:{m['user_id']}", {v: ts for v in vp})

    for g in await db.fetch(
            """SELECT g.*, u.display_name AS guardian_name FROM guardians g JOIN users u ON u.user_id=g.guardian_user_id
               WHERE g.status IN ('ACTIVE','REMOVAL_PENDING')"""):
        await store.set_guardian(str(g["user_id"]), dict(g))
    for pb in await db.fetch(
            """SELECT p.user_id,p.payee_vpa,p.until,d.decision_id,d.tier,d.reasons,d.scam_family,d.family_sim,d.detail
               FROM pair_blocks p LEFT JOIN risk_decisions d ON d.decision_id=p.decision_id WHERE p.until > now()"""):
        await store.set_pair_block(str(pb["user_id"]), pb["payee_vpa"], _ts(pb["until"]), {
            "decision_id": str(pb["decision_id"]) if pb["decision_id"] else None, "reasons": pb["reasons"] or [],
            "scam_family": pb["scam_family"], "family_sim": pb["family_sim"]})
    await load_policy()
    rt.warm_seconds = round(time.time() - t0, 2)
    counts["seconds"] = rt.warm_seconds
    return counts


async def load_user(user_id: str) -> bool:
    """Cache-miss fallback for one payer."""
    u = await db.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
    if not u:
        return False
    await index_user(u)
    r = rt.store.r
    await rt.store.put_user_profile(str(user_id), first_seen=_ts(u["first_seen"]), phone_hash=u["phone_hash"],
                                    tenant=u["tenant_id"], balance=float(u["balance"]), locale=u["locale"])
    rows = await db.fetch(
        """SELECT payee_vpa, amount, created_at,
                  min(created_at) OVER (PARTITION BY payee_vpa) AS first, count(*) OVER (PARTITION BY payee_vpa) AS c
           FROM transactions WHERE user_id=$1 AND status='SUCCESS'""", user_id)
    p = r.pipeline(transaction=False)
    now = time.time()
    for t in rows:
        ts = _ts(t["created_at"])
        p.hset(f"known_payee:{user_id}", t["payee_vpa"], f"{t['c']}|{_ts(t['first'])}")
        if ts >= now - 31 * DAY:
            p.zadd(f"vel:{user_id}", {f"{ts:.3f}|{float(t['amount']):.2f}|{t['payee_vpa']}|{int(t['first'] == t['created_at'])}": ts})
    for d in await db.fetch("SELECT device_id,first_seen FROM devices WHERE user_id=$1", user_id):
        p.hset(f"dev:{user_id}", str(d["device_id"]), _ts(d["first_seen"]))
    await p.execute()
    await load_guardian(str(user_id))
    return True


async def load_payee(tenant: str, vpa: str) -> bool:
    """Cache-miss fallback for one payee."""
    v = await db.fetchrow("SELECT * FROM vpas WHERE vpa_id=$1", vpa)
    if not v:
        return False
    await rt.store.put_vpa_dir(vpa, registered_name=v["registered_name"], kind=v["kind"], verified=v["verified"],
                               on_denylist=v["on_denylist"], first_seen=_ts(v["first_seen"]), tenant=v["tenant_id"])
    x = await db.fetchrow("SELECT * FROM vpa_reputation WHERE tenant_id=$1 AND vpa_id=$2", tenant, vpa)
    if x:
        await rt.store.r.hset(f"vpa_rep:{tenant}:{vpa}", mapping={
            "recv": x["recv_decayed"], "bad": x["bad_decayed"], "at": _ts(x["counters_at"]),
            "lifetime": x["lifetime_recv"]})
    return True


async def pg_state(tenant: str, user_id: str, payee: str) -> RawState:
    """Enough state for a rules-first decision when the cache is unavailable (degraded mode)."""
    st = RawState(degraded=True)
    v = await db.fetchrow("SELECT * FROM vpas WHERE vpa_id=$1", payee)
    if v:
        st.vdir = {"registered_name": v["registered_name"], "kind": v["kind"], "verified": str(int(v["verified"])),
                   "on_denylist": str(int(v["on_denylist"])), "first_seen": str(_ts(v["first_seen"])),
                   "tenant": v["tenant_id"]}
    k = await db.fetchrow(
        "SELECT count(*) AS c, min(created_at) AS first FROM transactions WHERE user_id=$1 AND payee_vpa=$2 "
        "AND status='SUCCESS'", user_id, payee)
    if k and k["c"]:
        st.known = f"{k['c']}|{_ts(k['first'])}"
    p = await db.fetchrow("SELECT * FROM policy_config WHERE tenant_id=$1", tenant)
    if p:
        st.policy = {kk: str(vv) for kk, vv in dict(p).items() if vv is not None}
    return st
