"""Golden scenarios: frozen situations with the tier, action and family they must produce.

Each scenario builds its own state in a fresh in-process cache through the real FeatureStore,
computes features with the real code, and decides with the same function the live API calls.
Run on every change to rules or models:  python manage.py golden
"""
from __future__ import annotations

import random
import time
import uuid
from datetime import datetime

import fakeredis
import yaml

from app.config import ROOT
from app.core.decide import decide
from app.core.features import IST, AssessInput, RawState, compute_features
from app.core.rules import RuleEngine
from app.core.scoring import ModelBundle, active_model_dir
from app.core.state import FeatureStore
from app.core.textmodel import TextModel
from app.orchestrator import parse_policy
from app.security import vpa_token

SCENARIOS = ROOT / "ml" / "golden" / "scenarios.yaml"
ORDER = ["SAFE", "WARNING", "STEP_UP", "BLOCK"]
DAY = 86400.0
TENANT, USER, PHONE = "bank_a", "00000000-0000-4000-8000-000000000001", "h_golden_payer"
DEVICE = "00000000-0000-4000-8000-0000000000d1"


def load_scenarios() -> list[dict]:
    return yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))


async def build(sc: dict, store: FeatureStore, now: float) -> tuple[AssessInput, RawState]:
    r = store.r
    rng = random.Random(42)
    payer = sc.get("payer") or {}
    balance = float(payer.get("balance", 200000))
    await store.put_user_profile(USER, first_seen=now - float(payer.get("account_age_days", 800)) * DAY,
                                 phone_hash=PHONE, tenant=TENANT, balance=balance)
    mean = float(payer.get("mean_amount", 800))
    hour_now = datetime.fromtimestamp(now, IST).hour
    for i in range(int(payer.get("history", 40))):          # ordinary history, at this time of day
        ts = now - (i + 1) * 0.68 * DAY + ((hour_now - datetime.fromtimestamp(now - (i + 1) * 0.68 * DAY, IST).hour) * 3600)
        amt = max(20.0, mean * rng.uniform(0.3, 1.9))
        await r.zadd(f"vel:{USER}", {f"{ts:.3f}|{amt:.2f}|shop{i % 6}@banka|0": ts})
    for burst in range(int(payer.get("new_payees_last_10m", 0))):
        ts = now - 120 - burst * 60
        await r.zadd(f"vel:{USER}", {f"{ts:.3f}|500.00|stranger{burst}@bankb|1": ts})
    dev = sc.get("device") or {}
    if not dev.get("new"):
        await r.hset(f"dev:{USER}", DEVICE, now - 300 * DAY)

    p = sc["payee"]
    vpa = p["vpa"]
    first_seen = now - float(p.get("age_days", 400)) * DAY
    await store.put_vpa_dir(vpa, registered_name=p.get("name", "Test Payee"), kind=p.get("kind", "person"),
                            verified=bool(p.get("verified", False)), on_denylist=bool(p.get("denylist", False)),
                            first_seen=first_seen, tenant=p.get("tenant", "bank_b"))
    await r.hset(f"vpa_rep:{TENANT}:{vpa}", mapping={"recv": float(p.get("recv", 0)), "bad": float(p.get("bad", 0)),
                                                    "at": now, "lifetime": int(p.get("lifetime", p.get("recv", 0)))})
    for kind in ("confirmed", "report", "nearmiss"):
        for k in range(int(p.get(kind, 0))):
            await r.zadd(f"vpa_ev:{TENANT}:{vpa}:{kind}", {f"{kind}-{k}": now - DAY})
    for k in range(int(p.get("fan_in_1h", 0))):
        await r.zadd(f"vpa_in:{TENANT}:{vpa}", {f"payer-{k}": now - 60 * (k % 50 + 1)})
        if k < int(p.get("fan_in_1h", 0)) * float(p.get("first_time_ratio", 1.0)):
            await r.zadd(f"vpa_in1:{TENANT}:{vpa}", {f"payer-{k}": now - 60 * (k % 50 + 1)})
    if p.get("graph"):
        await store.put_graph(vpa, p["graph"])
    if p.get("consortium"):
        await store.put_consortium(TENANT, vpa_token(vpa), p_bad=0.2, source_tenant="bank_b",
                                   confirmed=bool(p["consortium"].get("confirmed", False)))
    k = sc.get("known")
    if k:
        await r.hset(f"known_payee:{USER}", vpa, f"{int(k['count'])}|{now - float(k['first_paid_days_ago']) * DAY}")

    call = sc.get("call")
    if call:
        cid = str(uuid.uuid4())
        started = now - float(call.get("started_min_ago", 10)) * 60
        await store.call_event(phone_hash=PHONE, call_id=cid, event="started", caller_known=bool(call.get("known", False)),
                               caller_risk=int(call.get("risk", 0)), ts=started)
        if not call.get("active", True):
            await store.call_event(phone_hash=PHONE, call_id=cid, event="ended", caller_known=bool(call.get("known", False)),
                                   caller_risk=int(call.get("risk", 0)), ts=now - float(call.get("ended_min_ago", 5)) * 60)
    msg = sc.get("msg")
    if msg:
        await store.message_flag(user_id=USER, check_id=str(uuid.uuid4()), scam_prob=float(msg.get("prob", 0.93)),
                                 label=msg.get("label", "refund_bait"), family=msg.get("family"),
                                 vpas=[vpa] if msg.get("payee_in_msg") else [], ts=now - float(msg.get("mins_ago", 20)) * 60)

    q = sc["request"]
    inp = AssessInput(
        txn_id=f"GOLD-{sc['id']}", tenant=TENANT, user_id=USER, payer_vpa="golden@banka", payee_vpa=vpa,
        amount=float(q["amount"]), device_id=str(uuid.uuid4()) if dev.get("new") else DEVICE,
        claimed_name=q.get("claimed"), note=q.get("note"),
        session={"vpa_entry_method": q.get("entry", "typed"), "app_switch_count": q.get("app_switches", 0),
                 "time_on_pay_screen_s": q.get("dwell", 20), "amount_edit_count": q.get("edits", 0)},
        parent_decision_id=str(uuid.uuid4()) if sc.get("parent") else None)
    if sc.get("cache_down"):                 # only what the PostgreSQL fallback can supply
        st = RawState(degraded=True)
        st.vdir = await r.hgetall(f"vpa_dir:{vpa}")
        st.known = await r.hget(f"known_payee:{USER}", vpa)
    else:
        st = await store.read(tenant=TENANT, user_id=USER, phone_hash=PHONE, payee=vpa, device_id=inp.device_id,
                              txn_id=inp.txn_id, now=now)
    return inp, st


def check(sc: dict, d, f: dict) -> list[str]:
    e, problems = sc["expect"], []
    rank = ORDER.index(d.tier)
    if "tier" in e and d.tier != e["tier"]:
        problems.append(f"tier {d.tier}, expected {e['tier']}")
    if "tier_in" in e and d.tier not in e["tier_in"]:
        problems.append(f"tier {d.tier}, expected one of {e['tier_in']}")
    if "min_tier" in e and rank < ORDER.index(e["min_tier"]):
        problems.append(f"tier {d.tier}, expected at least {e['min_tier']}")
    if "max_tier" in e and rank > ORDER.index(e["max_tier"]):
        problems.append(f"tier {d.tier}, expected at most {e['max_tier']}")
    if "action" in e and d.action != e["action"]:
        problems.append(f"action {d.action}, expected {e['action']}")
    if "action_in" in e and d.action not in e["action_in"]:
        problems.append(f"action {d.action}, expected one of {e['action_in']}")
    if "family" in e and d.family != e["family"]:
        problems.append(f"family {d.family}, expected {e['family']}")
    for rule in e.get("rules_include", []):
        if rule not in d.fired:
            problems.append(f"rule {rule} did not fire (fired: {d.fired})")
    for rule in e.get("rules_exclude", []):
        if rule in d.fired:
            problems.append(f"rule {rule} fired but should not")
    codes = [x["code"] for x in d.reasons_raw]
    if e.get("reasons_include_any") and not set(e["reasons_include_any"]) & set(codes):
        problems.append(f"reasons {codes} include none of {e['reasons_include_any']}")
    for code in e.get("reasons_include_all", []):
        if code not in codes:
            problems.append(f"reason {code} missing (got {codes})")
    if "degraded" in e and d.degraded != e["degraded"]:
        problems.append(f"degraded {d.degraded}, expected {e['degraded']}")
    if d.tier != "SAFE" and not codes:
        problems.append("a non-Safe decision has no reason")
    return problems


async def run_one(sc: dict, engine: RuleEngine, bundle: ModelBundle | None, text: TextModel | None, now: float):
    store = FeatureStore(fakeredis.FakeAsyncRedis(decode_responses=True))
    inp, st = await build(sc, store, now)
    classify = text.classify if text else None
    f, aux = compute_features(inp, st, now, classify)
    policy = parse_policy(sc.get("policy"))
    return decide(f, aux, engine, bundle, policy, has_parent=bool(inp.parent_decision_id),
                  model_down=bool(sc.get("model_down")), state_degraded=st.degraded, msg_state=st.msg), f


async def run_golden(verbose: bool = True, rules_only: bool = False) -> bool:
    engine = RuleEngine()
    art = ROOT / "ml" / "artifacts"
    d = active_model_dir(art)
    bundle = ModelBundle.load(d) if (d and not rules_only) else None
    text = TextModel.load(art / "text.joblib") if (art / "text.joblib").exists() else None
    now = time.time()
    scenarios = load_scenarios()
    failed = skipped = 0
    for sc in scenarios:
        if sc.get("needs_model") and bundle is None:
            skipped += 1
            continue
        dec, f = await run_one(sc, engine, bundle, text, now)
        problems = check(sc, dec, f)
        if problems:
            failed += 1
        if verbose:
            mark = "FAIL" if problems else "ok  "
            print(f"{mark} {sc['id']:7s} {dec.tier:8s} {dec.action:8s} {dec.score:.2f} {str(dec.family or '-'):16s} {sc['title']}")
            for p in problems:
                print(f"         -> {p}")
    if verbose:
        ran = len(scenarios) - skipped
        print(f"\n{ran - failed}/{ran} golden scenarios pass"
              + (f", {skipped} skipped (they need a trained model)" if skipped else "")
              + f"  (model {bundle.version if bundle else 'rules only'}, {engine.version})")
    return failed == 0
