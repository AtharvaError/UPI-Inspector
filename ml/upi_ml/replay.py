"""Bootstrap replay: push the simulated event stream through the real FeatureStore on a simulated clock.

Every payment's features are computed by the same code the live service uses, so the first model
is trained on exactly what it will see when serving.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import fakeredis

from app.core.features import AssessInput, compute_features
from app.core.graphfeat import communities, compute_graph_features
from app.core.state import FeatureStore
from app.core.textmodel import TextModel
from app.security import vpa_token

from .simulator import DAY, HOUR, World

GRAPH_EVERY = 6 * HOUR
COMM_EVERY = 7 * DAY
EDGE_WINDOW = 30 * DAY


@dataclass
class ReplayResult:
    rows: list[dict] = field(default_factory=list)
    store: FeatureStore | None = None
    msg_rows: list[dict] = field(default_factory=list)
    bad_events: list[dict] = field(default_factory=list)
    consortium: dict = field(default_factory=dict)
    graph: dict = field(default_factory=dict)
    seconds: float = 0.0


async def run_replay(w: World, events: list[dict], text_model: TextModel, progress: bool = True) -> ReplayResult:
    t0 = time.time()
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = FeatureStore(r)
    res = ReplayResult(store=store)

    for v in w.vpas.values():
        await store.put_vpa_dir(v["vpa"], registered_name=v["registered_name"], kind=v["kind"],
                                verified=v["verified"], on_denylist=v.get("on_denylist", False),
                                first_seen=v["first_seen"], tenant=v["tenant"])
    for u in w.users:
        await store.put_user_profile(u["user_id"], first_seen=u["first_seen"], phone_hash=u["phone_hash"],
                                     tenant=u["tenant"], balance=u["balance"], locale=u["locale"])
        await r.hsetnx(f"dev:{u['user_id']}", u["device_id"], u["first_seen"])

    merchants = {v["vpa"] for v in w.vpas.values() if v["kind"] == "merchant"}
    edges: deque = deque()
    reported: set[str] = set()
    risky: set[str] = set()
    comm: dict[str, int] = {}
    graph_keys: set[str] = set()
    last_graph = last_comm = w.cfg.start_ts
    classify = text_model.classify
    n = len(events)

    async def graph_tick(now: float, with_comm: bool) -> None:
        nonlocal comm, graph_keys
        while edges and edges[0][0] < now - EDGE_WINDOW:
            edges.popleft()
        el = list(edges)
        if with_comm:
            comm = communities(el, w.vpa_fps, merchants)
        feats = compute_graph_features(el, w.vpa_fps, reported, risky, now, comm)
        for vpa in graph_keys - set(feats):
            await r.delete(f"vpa_graph:{vpa}")
        for vpa, g in feats.items():
            await store.put_graph(vpa, g)
        graph_keys = set(feats)
        res.graph = feats

    for i, e in enumerate(events):
        ts = e["ts"]
        if ts - last_graph >= GRAPH_EVERY:
            do_comm = ts - last_comm >= COMM_EVERY
            await graph_tick(ts, do_comm)
            last_graph = ts
            if do_comm:
                last_comm = ts
        kind = e["t"]
        if kind == "pay":
            u = e["user"]
            st = await store.read(tenant=u["tenant"], user_id=u["user_id"], phone_hash=u["phone_hash"],
                                  payee=e["payee"], device_id=e["device_id"], txn_id=e["txn_id"], now=ts)
            req = AssessInput(txn_id=e["txn_id"], tenant=u["tenant"], user_id=u["user_id"], payer_vpa=u["vpa"],
                              payee_vpa=e["payee"], amount=e["amount"], device_id=e["device_id"],
                              claimed_name=e["claimed"], note=e["note"], session=e["session"], locale=u["locale"])
            f, aux = compute_features(req, st, ts, classify)
            if not e["exclude"]:
                res.rows.append({
                    **f,
                    "aux_json": json.dumps({k: aux.get(k) for k in (
                        "odd_hour", "consortium_confirmed", "band", "mean_amount", "note_probs", "msg_probs",
                        "vpa_exists", "registered_name", "claimed_name", "consortium_source")}),
                    "txn_id": e["txn_id"], "ts": ts, "tenant": u["tenant"], "user_id": u["user_id"],
                    "segment": u["segment"], "age_band": u["age_band"], "locale": u["locale"], "payee": e["payee"],
                    "is_fraud": int(e["fraud"]), "family": e["family"] or "", "campaign": e["campaign"] or "",
                    "variant": e["variant"] or "",
                })
            await store.payment_success(tenant=u["tenant"], user_id=u["user_id"], payee=e["payee"],
                                        amount=e["amount"], ts=ts, device_id=e["device_id"], debit=False)
            edges.append((ts, u["vpa"], e["payee"], e["amount"]))
        elif kind == "call":
            u = e["user"]
            await store.call_event(phone_hash=u["phone_hash"], call_id=e["call_id"], event=e["event"],
                                   caller_known=e["known"], caller_risk=e["risk"], ts=ts)
        elif kind == "msg":
            u = e["user"]
            label, probs, scam_prob = classify(e["text"])
            flagged = e["flagged"] and scam_prob >= 0.7
            if flagged:
                await store.message_flag(user_id=u["user_id"], check_id=e["check_id"], scam_prob=scam_prob,
                                         label=label, family=e["family"], vpas=e["vpas"], ts=ts)
            res.msg_rows.append({"check_id": e["check_id"], "user": u, "ts": ts, "text": e["text"], "label": label,
                                 "scam_prob": scam_prob, "probs": probs, "family": e["family"], "vpas": e["vpas"],
                                 "flagged": flagged})
        elif kind == "report":
            await store.bad_event(tenant=e["tenant"], vpa=e["vpa"], weight=e["weight"], kind=e["kind"], ts=ts,
                                  ref=e["ref"])
            res.bad_events.append({"tenant": e["tenant"], "vpa": e["vpa"], "kind": e["kind"], "weight": e["weight"],
                                   "ref": e["ref"], "ts": ts})
            risky.add(e["vpa"])
            if e["kind"] == "confirmed":
                reported.add(e["vpa"])
            snap = await store.rep_snapshot(e["tenant"], e["vpa"], ts)
            if snap["bad_now"] >= 2 and (snap["confirmed_90d"] >= 1 or snap["band"] in ("CAUTION", "REPORTED")):
                token = vpa_token(e["vpa"])
                confirmed = snap["confirmed_90d"] >= 1
                for other in ("bank_a", "bank_b"):
                    if other != e["tenant"]:
                        await store.put_consortium(other, token, p_bad=snap["p_bad"], source_tenant=e["tenant"],
                                                   confirmed=confirmed)
                res.consortium[(token, e["tenant"])] = {
                    "vpa": e["vpa"], "bad": snap["bad_now"], "recv": snap["recv_now"], "p_bad": snap["p_bad"],
                    "band": snap["band"], "confirmed": confirmed, "ts": ts}
        if progress and i and i % 20000 == 0:
            el = time.time() - t0
            print(f"  replay {i:,}/{n:,} events  {el:5.0f}s  ({i / el:,.0f}/s)", flush=True)

    await graph_tick(w.cfg.end_ts, True)
    res.seconds = time.time() - t0
    return res
