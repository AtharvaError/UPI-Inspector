"""FeatureStore: every read and write of risk state in the shared cache.

One class is used by the live service (wall clock) and by the bootstrap replay (simulated clock),
so every method takes the event time explicitly and never relies on key expiry for correctness.
Key names follow docs/05, section 2.
"""
from __future__ import annotations

import json
from typing import Any

from ..security import vpa_token
from . import reputation as rep
from .features import DAY, HOUR, RawState

TTL_DAY = 86400


class FeatureStore:
    def __init__(self, r: Any, demo_mode: bool = False) -> None:
        self.r = r
        self.demo_mode = demo_mode

    # ------------------------------------------------------------------ read --
    async def read(self, *, tenant: str, user_id: str, phone_hash: str, payee: str, device_id: str | None,
                   txn_id: str, now: float) -> RawState:
        p = self.r.pipeline(transaction=False)
        p.get(f"decision:{user_id}:{txn_id}")                                        # 0
        p.hgetall(f"policy:{tenant}")                                                # 1
        p.hgetall(f"user_prof:{user_id}")                                            # 2
        p.hget(f"known_payee:{user_id}", payee)                                      # 3
        p.zrangebyscore(f"vel:{user_id}", now - 30 * DAY, now + 1)                   # 4
        p.hget(f"dev:{user_id}", device_id or "-")                                   # 5
        p.hgetall(f"vpa_dir:{payee}")                                                # 6
        p.hgetall(f"vpa_rep:{tenant}:{payee}")                                       # 7
        p.zcount(f"vpa_in:{tenant}:{payee}", now - HOUR, now + 1)                    # 8
        p.zcount(f"vpa_in:{tenant}:{payee}", now - DAY, now + 1)                     # 9
        p.zcount(f"vpa_in1:{tenant}:{payee}", now - DAY, now + 1)                    # 10
        p.zcount(f"vpa_ev:{tenant}:{payee}:report", now - 30 * DAY, now + 1)         # 11
        p.zcount(f"vpa_ev:{tenant}:{payee}:confirmed", now - 90 * DAY, now + 1)      # 12
        p.zcount(f"vpa_ev:{tenant}:{payee}:nearmiss", now - 14 * DAY, now + 1)       # 13
        p.hgetall(f"vpa_graph:{payee}")                                              # 14
        p.hgetall(f"consortium:{tenant}:{vpa_token(payee)}")                         # 15
        p.hgetall(f"call_flag:{phone_hash}")                                         # 16
        p.zcount(f"calls_24h:{phone_hash}", now - DAY, now + 1)                      # 17
        p.hgetall(f"msg_flag:{user_id}")                                             # 18
        p.zscore(f"msg_vpas:{user_id}", payee)                                       # 19
        p.get(f"pair_block:{user_id}:{payee}")                                       # 20
        p.hgetall(f"guardian:{user_id}")                                             # 21
        p.hgetall(f"demo_ovr:{user_id}")                                             # 22
        p.hget(f"user_block:{user_id}", payee)                                       # 23
        x = await p.execute()
        return RawState(
            cached_decision=x[0], policy=x[1] or {}, user_prof=x[2] or {}, known=x[3], vel=x[4] or [],
            dev_first=x[5], vdir=x[6] or {}, vrep=x[7] or {}, fan_1h=int(x[8] or 0), fan_24h=int(x[9] or 0),
            first_24h=int(x[10] or 0), reports_30d=int(x[11] or 0), confirmed_90d=int(x[12] or 0),
            near_miss_14d=int(x[13] or 0), graph=x[14] or {}, cons=x[15] or {}, call=x[16] or {},
            unknown_calls_24h=int(x[17] or 0), msg=x[18] or {}, msg_vpa_ts=x[19], pair_block=x[20],
            guardian=x[21] or {}, demo_ovr=(x[22] or {}) if self.demo_mode else {}, user_block=x[23],
        )

    async def rep_snapshot(self, tenant: str, vpa: str, now: float) -> dict:
        """This bank's current view of a payee: directory entry, counters and the derived band."""
        p = self.r.pipeline(transaction=False)
        p.hgetall(f"vpa_dir:{vpa}")
        p.hgetall(f"vpa_rep:{tenant}:{vpa}")
        p.zcount(f"vpa_in:{tenant}:{vpa}", now - HOUR, now + 1)
        p.zcount(f"vpa_in:{tenant}:{vpa}", now - DAY, now + 1)
        p.zcount(f"vpa_in1:{tenant}:{vpa}", now - DAY, now + 1)
        p.zcount(f"vpa_ev:{tenant}:{vpa}:report", now - 30 * DAY, now + 1)
        p.zcount(f"vpa_ev:{tenant}:{vpa}:confirmed", now - 90 * DAY, now + 1)
        p.zcount(f"vpa_ev:{tenant}:{vpa}:nearmiss", now - 14 * DAY, now + 1)
        vdir, vrep, f1, f24, first24, r30, c90, n14 = await p.execute()
        vdir, vrep = vdir or {}, vrep or {}
        first_seen = float(vdir.get("first_seen", now - 180 * DAY))
        verified = vdir.get("verified") in ("1", "true", "True")
        ratio = (int(first24 or 0) / int(f24)) if int(f24 or 0) > 0 else 0.5
        d = rep.derive(recv=float(vrep.get("recv", 0)), bad=float(vrep.get("bad", 0)),
                       counters_at=float(vrep.get("at", now)), lifetime_recv=int(float(vrep.get("lifetime", 0))),
                       confirmed_90d=int(c90 or 0), first_seen=first_seen, now=now, reports_30d=int(r30 or 0),
                       near_miss_14d=int(n14 or 0), fan_in_1h=int(f1 or 0), first_time_ratio=ratio,
                       verified=verified, exists=bool(vdir), individual=vdir.get("kind", "person") == "person")
        d.update({
            "vpa": vpa, "tenant": tenant, "exists": bool(vdir), "registered_name": vdir.get("registered_name", ""),
            "kind": vdir.get("kind", "person"), "verified": verified,
            "on_denylist": vdir.get("on_denylist") in ("1", "true", "True"), "first_seen": first_seen,
            "home_tenant": vdir.get("tenant", ""),
            "recv": float(vrep.get("recv", 0)), "bad": float(vrep.get("bad", 0)),
            "counters_at": float(vrep.get("at", now)), "lifetime_recv": int(float(vrep.get("lifetime", 0))),
            "fan_in_1h": int(f1 or 0), "fan_in_24h": int(f24 or 0), "first_time_payer_ratio_24h": ratio,
            "reports_30d": int(r30 or 0), "confirmed_90d": int(c90 or 0), "near_miss_14d": int(n14 or 0),
        })
        return d

    # ----------------------------------------------------------------- write --
    async def put_user_profile(self, user_id: str, *, first_seen: float, phone_hash: str, tenant: str,
                               balance: float, locale: str = "en") -> None:
        await self.r.hset(f"user_prof:{user_id}", mapping={
            "first_seen": first_seen, "phone_hash": phone_hash, "tenant": tenant, "balance": balance,
            "locale": locale})

    async def put_vpa_dir(self, vpa: str, *, registered_name: str, kind: str, verified: bool, on_denylist: bool,
                          first_seen: float, tenant: str) -> None:
        await self.r.hset(f"vpa_dir:{vpa}", mapping={
            "registered_name": registered_name, "kind": kind, "verified": int(verified),
            "on_denylist": int(on_denylist), "first_seen": first_seen, "tenant": tenant})

    async def _bump_rep(self, tenant: str, vpa: str, *, recv: float, bad: float, ts: float) -> None:
        key = f"vpa_rep:{tenant}:{vpa}"
        cur = await self.r.hgetall(key) or {}
        at = float(cur.get("at", ts))
        new_recv = rep.decay(float(cur.get("recv", 0)), at, ts) + recv
        new_bad = rep.decay(float(cur.get("bad", 0)), at, ts) + bad
        await self.r.hset(key, mapping={"recv": new_recv, "bad": new_bad, "at": ts,
                                        "lifetime": int(float(cur.get("lifetime", 0))) + (1 if recv > 0 else 0)})

    async def payment_success(self, *, tenant: str, user_id: str, payee: str, amount: float, ts: float,
                              device_id: str | None, debit: bool = True) -> dict:
        """A payment completed: update the payer's habits and the payee's inbound counters."""
        known = await self.r.hget(f"known_payee:{user_id}", payee)
        is_new = known is None
        cnt, first = 0, ts
        if not is_new:
            try:
                c, fp = known.split("|")
                cnt, first = int(c), float(fp)
            except ValueError:
                pass
        p = self.r.pipeline(transaction=False)
        p.zadd(f"vel:{user_id}", {f"{ts:.3f}|{amount:.2f}|{payee}|{int(is_new)}": ts})
        p.zremrangebyscore(f"vel:{user_id}", 0, ts - 31 * DAY)
        p.hset(f"known_payee:{user_id}", payee, f"{cnt + 1}|{first}")
        if device_id:
            p.hsetnx(f"dev:{user_id}", device_id, ts)
        if debit:
            p.hincrbyfloat(f"user_prof:{user_id}", "balance", -amount)
        p.zadd(f"vpa_in:{tenant}:{payee}", {user_id: ts})
        p.zremrangebyscore(f"vpa_in:{tenant}:{payee}", 0, ts - 2 * DAY)
        if is_new:
            p.zadd(f"vpa_in1:{tenant}:{payee}", {user_id: ts})
            p.zremrangebyscore(f"vpa_in1:{tenant}:{payee}", 0, ts - 2 * DAY)
        await p.execute()
        await self._bump_rep(tenant, payee, recv=1.0, bad=0.0, ts=ts)
        return {"is_new": is_new}

    async def bad_event(self, *, tenant: str, vpa: str, weight: float, kind: str, ts: float, ref: str) -> None:
        """kind: report | confirmed | nearmiss"""
        await self._bump_rep(tenant, vpa, recv=0.0, bad=weight, ts=ts)
        await self.r.zadd(f"vpa_ev:{tenant}:{vpa}:{kind}", {ref: ts})

    async def call_event(self, *, phone_hash: str, call_id: str, event: str, caller_known: bool, caller_risk: int,
                         ts: float, started_at: float | None = None) -> None:
        key = f"call_flag:{phone_hash}"
        if event == "started":
            await self.r.delete(key)
            await self.r.hset(key, mapping={
                "call_id": call_id, "state": "active", "caller_risk": caller_risk, "caller_known": int(caller_known),
                "started_at": started_at if started_at is not None else ts, "ended_at": 0})
            await self.r.expire(key, 8 * 3600)
            if not caller_known:
                await self.r.zadd(f"calls_24h:{phone_hash}", {call_id: ts})
                await self.r.zremrangebyscore(f"calls_24h:{phone_hash}", 0, ts - 2 * DAY)
        else:
            cur = await self.r.hget(key, "call_id")
            if cur == call_id:
                await self.r.hset(key, mapping={"state": "ended", "ended_at": ts})
                await self.r.expire(key, 3600)

    async def message_flag(self, *, user_id: str, check_id: str, scam_prob: float, label: str, family: str | None,
                           vpas: list[str], ts: float) -> None:
        await self.r.hset(f"msg_flag:{user_id}", mapping={
            "check_id": check_id, "scam_prob": scam_prob, "label": label, "family": family or "", "set_at": ts})
        await self.r.expire(f"msg_flag:{user_id}", 2 * TTL_DAY)
        if vpas:
            await self.r.zadd(f"msg_vpas:{user_id}", {v: ts for v in vpas})
            await self.r.expire(f"msg_vpas:{user_id}", 8 * TTL_DAY)

    async def put_graph(self, vpa: str, g: dict) -> None:
        await self.r.hset(f"vpa_graph:{vpa}", mapping={
            "downstream_hop": int(g.get("downstream_hop", 9)),
            "shares_device_with_reported": int(bool(g.get("shares_device_with_reported", 0))),
            "passthrough_ratio": float(g.get("passthrough_ratio", 0.0)),
            "community_risk_share": float(g.get("community_risk_share", 0.0))})

    async def put_consortium(self, tenant: str, token: str, *, p_bad: float, source_tenant: str,
                             confirmed: bool) -> None:
        await self.r.hset(f"consortium:{tenant}:{token}", mapping={
            "reported": 1, "p_bad": p_bad, "source_tenant": source_tenant, "confirmed": int(confirmed)})

    async def set_guardian(self, user_id: str, link: dict | None) -> None:
        key = f"guardian:{user_id}"
        await self.r.delete(key)
        if link:
            await self.r.hset(key, mapping={
                "guardian_link_id": str(link["guardian_link_id"]), "guardian_user_id": str(link["guardian_user_id"]),
                "guardian_name": link.get("guardian_name", ""), "mode": link["mode"],
                "amount_threshold": float(link.get("amount_threshold") or 0)})

    async def set_pair_block(self, user_id: str, payee: str, until: float, decision: dict) -> None:
        key = f"pair_block:{user_id}:{payee}"
        await self.r.set(key, json.dumps({"until": until, **decision}, default=str))
        await self.r.expireat(key, int(until) + 60)

    async def clear_pair_block(self, user_id: str, payee: str) -> None:
        await self.r.delete(f"pair_block:{user_id}:{payee}")

    async def set_user_block(self, user_id: str, payee: str, unblock_at: float = 0.0) -> None:
        """unblock_at 0 means blocked until further notice; otherwise the moment the block lifts."""
        await self.r.hset(f"user_block:{user_id}", payee, unblock_at)

    async def clear_user_block(self, user_id: str, payee: str) -> None:
        await self.r.hdel(f"user_block:{user_id}", payee)

    async def put_policy(self, tenant: str, policy: dict) -> None:
        await self.r.delete(f"policy:{tenant}")
        await self.r.hset(f"policy:{tenant}", mapping={k: str(v) for k, v in policy.items() if v is not None})
