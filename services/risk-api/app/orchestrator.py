"""The hot path: gateway checks, enrichment, scoring and the risk orchestrator (F-01).

One call, four logical stages (docs/04, section 4). Nothing here writes to PostgreSQL: the decision
is cached and published, and the outcome logger persists it.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from time import perf_counter

from . import i18n
from .bus import bus
from .core import warm
from .core.decide import decide
from .core.explain import family_variant
from .core.features import AssessInput, RawState, compute_features
from .core.present import family_payload, render_reasons
from .runtime import rt

DEC_TTL = 24 * 3600
POLICY_DEFAULTS = {"mode": "enforce", "t_warning": 0.30, "t_stepup": 0.60, "t_block": 0.85, "t_decline": 0.95,
                   "block_mode": "hold", "hold_seconds": 1800, "cooloff_seconds": 30, "guardian_timeout_s": 600,
                   "pair_block_hours": 24}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def parse_policy(p: dict | None) -> dict:
    d = dict(POLICY_DEFAULTS)
    for k, v in (p or {}).items():
        if k in ("mode", "block_mode"):
            d[k] = str(v)
        elif k in d:
            try:
                d[k] = float(v)
            except (TypeError, ValueError):
                pass
    return d


def _ms(t: float) -> float:
    return round((perf_counter() - t) * 1000, 3)


def _safe_default(inp: AssessInput, why: str) -> dict:
    """Returned when the hot path cannot finish in time. An outage never blocks a payment."""
    return {"decision_id": str(uuid.uuid4()), "txn_id": inp.txn_id, "score": 0.0, "tier": "SAFE", "action": "ALLOW",
            "enforced": True, "reasons": [], "scam_family": None, "payee": {"vpa": inp.payee_vpa}, "step_up": None,
            "hold": None, "guardian": None, "links": {"calls": [], "messages": []}, "model_version": rt.model_version,
            "rules_version": rt.engine.version if rt.engine else "", "degraded": True, "degraded_reason": why,
            "latency_ms": rt.settings.hot_path_timeout_ms}


async def assess(inp: AssessInput, now: float | None = None, synthetic: bool = False) -> dict:
    # Admission control. Past a point, queueing more work only makes every answer late. A request that
    # arrives when too many are already in flight gets the safe default at once: overload, like any other
    # outage, never blocks a payment and never makes the payer wait.
    if rt.inflight >= rt.settings.max_inflight:
        rt.metrics.shed += 1
        return _safe_default(inp, "overload")
    rt.inflight += 1
    try:
        return await asyncio.wait_for(_assess(inp, now, synthetic), timeout=rt.settings.hot_path_timeout_ms / 1000)
    except asyncio.TimeoutError:
        rt.metrics.timeouts += 1
        return _safe_default(inp, "timeout")
    finally:
        rt.inflight -= 1


async def _read_state(inp: AssessInput, phone_hash: str, now: float) -> RawState:
    s = rt.settings
    try:
        if rt.chaos["cache_down"]:
            raise ConnectionError("cache unavailable")
        st = await rt.store.read(tenant=inp.tenant, user_id=inp.user_id, phone_hash=phone_hash, payee=inp.payee_vpa,
                                 device_id=inp.device_id, txn_id=inp.txn_id, now=now)
        if not st.vdir:                       # payee not in the cache: one bounded look in PostgreSQL
            rt.metrics.fallbacks += 1
            try:
                if await asyncio.wait_for(warm.load_payee(inp.tenant, inp.payee_vpa), s.pg_fallback_timeout_ms / 1000):
                    st = await rt.store.read(tenant=inp.tenant, user_id=inp.user_id, phone_hash=phone_hash,
                                             payee=inp.payee_vpa, device_id=inp.device_id, txn_id=inp.txn_id, now=now)
            except asyncio.TimeoutError:
                st.degraded = True
        return st
    except Exception:
        rt.metrics.fallbacks += 1
        try:
            return await asyncio.wait_for(warm.pg_state(inp.tenant, inp.user_id, inp.payee_vpa),
                                          s.pg_fallback_timeout_ms / 1000)
        except Exception:
            return RawState(degraded=True)


async def _assess(inp: AssessInput, now: float | None, synthetic: bool) -> dict:
    t0 = perf_counter()
    now = now or time.time()
    r = rt.store.r
    stage: dict = {}

    ui = rt.user_index.get(inp.user_id)
    if ui is None and await warm.load_user(inp.user_id):
        ui = rt.user_index.get(inp.user_id)
    st = await _read_state(inp, ui["phone_hash"] if ui else "-", now)
    stage["read"] = _ms(t0)
    if st.cached_decision:
        return json.loads(st.cached_decision)

    policy = parse_policy(st.policy)
    enforced = policy["mode"] == "enforce"
    locale = i18n.norm_locale(inp.locale or (ui or {}).get("locale"))
    cache_ok = not rt.chaos["cache_down"]

    # --- features -------------------------------------------------------------
    t = perf_counter()
    f, aux = compute_features(inp, st, now, rt.classify)
    stage["features"] = _ms(t)

    # --- a Decline in the last 24 hours for this payer and payee stands -------------------
    pair = None
    if st.pair_block and enforced:
        try:
            pb = json.loads(st.pair_block)
            if float(pb.get("until", 0)) > now:
                pair = pb
        except ValueError:
            pair = None

    # --- rules, models, tier, action, reasons, family: one shared function ----------------
    d = decide(f, aux, rt.engine, rt.bundle, policy, has_parent=bool(inp.parent_decision_id),
               model_down=rt.chaos["model_down"], state_degraded=st.degraded, pair_block=pair, msg_state=st.msg)
    stage.update(d.stage_ms)
    rr, score, tier, action, degraded, fired = d.rules, d.score, d.tier, d.action, d.degraded, d.fired
    contribs, parts, reasons_raw, fam, sim, gc = d.contribs, d.parts, d.reasons_raw, d.family, d.family_sim, d.groups

    # --- the payer's own "never pay this UPI ID" list (F-33) -------------------------------
    # Not a risk judgement, so the score and tier are left as the models gave them; only the action changes.
    # It is the payer's own instruction, so it is honoured in shadow mode as well.
    blocked = None
    if st.user_block is not None:
        try:
            lifts = float(st.user_block or 0)
        except ValueError:
            lifts = 0.0
        if lifts == 0 or lifts > now:
            blocked = {"blocked": True, "unblock_at": iso(lifts) if lifts else None}
            action = "DECLINE"
            fired = fired + ["USER_BLOCK"]

    fam_var = (pair or {}).get("family_variant") if pair is not None else family_variant(fam, f)
    codes = (["USER_BLOCKED"] if blocked else []) + [x["code"] for x in reasons_raw]
    calls, messages = [], []
    if tier != "SAFE":
        if aux.get("call_id") and (any(x in fired for x in ("R-02", "R-03"))
                                   or any(c in codes for c in ("CALL_ACTIVE_UNKNOWN", "CALLER_FLAGGED"))
                                   or gc.get("call", 0) > 0.3):
            calls.append(aux["call_id"])
        if aux.get("msg_check_id") and (any(x in fired for x in ("R-10", "R-13"))
                                        or any(c in codes for c in ("MESSAGE_FLAGGED", "PAYEE_IN_SCAM_MESSAGE"))
                                        or gc.get("message", 0) > 0.3):
            messages.append(aux["msg_check_id"])

    decision_id = str(uuid.uuid4())
    reasons = render_reasons(locale, reasons_raw, fam)
    if pair is not None:
        reasons = [{"code": "PAIR_BLOCKED", "group": "relationship", "evidence": {},
                    "message": i18n.render_reason(locale, "PAIR_BLOCKED", None, {})}] + reasons[:2]
    reasons_en = reasons if locale == "en" else render_reasons("en", reasons_raw, fam)
    if blocked:
        mine = {"code": "USER_BLOCKED", "group": "relationship", "evidence": {}}
        reasons = [{**mine, "message": i18n.render_reason(locale, "USER_BLOCKED", None, {})}] + reasons[:2]
        reasons_en = [{**mine, "message": i18n.render_reason("en", "USER_BLOCKED", None, {})}] + reasons_en[:2]
    payee = {"vpa": inp.payee_vpa, "exists": aux["vpa_exists"], "registered_name": aux["registered_name"],
             "kind": aux["payee_kind"], "band": aux["band"], "trust_score": aux["trust_score"],
             "band_reason": i18n.band_reason(locale, aux["band_reason"]),
             "name_match_score": f["name_match_score"]}

    step_up = hold = guardian = None
    side: list[tuple[str, dict]] = []
    pipe = r.pipeline(transaction=False) if cache_ok else None

    if enforced and action == "STEP_UP":
        fam_text = i18n.family(locale, fam or "UNCLASSIFIED", fam_var)
        cooloff = rt.demo_seconds("cooloff_seconds", policy)
        chal_id = str(uuid.uuid4())
        step_up = {"challenge_id": chal_id, "cooloff_s": cooloff,
                   "question": {"text": fam_text["question"], "answers": ["yes", "no"]},
                   "auth_methods": ["biometric", "otp"]}
        chal = {"challenge_id": chal_id, "decision_id": decision_id, "user_id": inp.user_id,
                "question_code": fam or "UNCLASSIFIED", "risky_answer": fam_text["risky_answer"],
                "cooloff_until": now + cooloff, "status": "OPEN", "tries": 0}
        if pipe is not None:
            pipe.set(f"chal:{chal_id}", json.dumps(chal), ex=DEC_TTL)
    if enforced and action == "HOLD":
        hs = rt.demo_seconds("hold_seconds", policy)
        hold = {"hold_id": str(uuid.uuid4()), "release_at": iso(now + hs), "confirm_by": iso(now + hs + 86400)}
        side.append(("hold.events", {"type": "created", "hold_id": hold["hold_id"], "decision_id": decision_id,
                                     "txn_id": inp.txn_id, "user_id": inp.user_id, "payee_vpa": inp.payee_vpa,
                                     "payee_name": aux["registered_name"], "claimed_name": inp.claimed_name,
                                     "amount": inp.amount, "note": inp.note, "release_at": now + hs,
                                     "confirm_by": now + hs + 86400, "tenant_id": inp.tenant}))
    g = st.guardian
    if enforced and g and g.get("guardian_user_id"):
        thr = float(g.get("amount_threshold", 0) or 0)
        need = g.get("mode") == "co_approve" and (
            action == "STEP_UP" or (action in ("ALLOW", "WARN") and f["is_new_payee"] == 1 and thr > 0
                                    and inp.amount >= thr))
        if need or tier in ("STEP_UP", "BLOCK"):
            kind = "APPROVAL" if need else "NOTIFY"
            req_id = str(uuid.uuid4())
            timeout = rt.demo_seconds("guardian_timeout_s", policy)
            guardian = {"required": bool(need), "kind": kind, "request_id": req_id,
                        "guardian_name": g.get("guardian_name", ""), "status": "PENDING",
                        "expires_at": iso(now + timeout) if need else None}
            g_locale = (rt.user_index.get(g["guardian_user_id"]) or {}).get("locale", "en")
            summary = {"protected_name": (ui or {}).get("name", ""), "amount": inp.amount,
                       "payee": {"vpa": inp.payee_vpa, "registered_name": aux["registered_name"]},
                       "tier": tier, "action": action,
                       "scam_family": family_payload(g_locale, fam or "UNCLASSIFIED", sim, variant=fam_var),
                       "reasons": [x["message"] for x in render_reasons(g_locale, reasons_raw, fam)]}
            greq = {"request_id": req_id, "guardian_link_id": g.get("guardian_link_id"),
                    "guardian_user_id": g["guardian_user_id"], "user_id": inp.user_id, "decision_id": decision_id,
                    "kind": kind, "status": "PENDING", "summary": summary,
                    "expires_at": (now + timeout) if need else None, "tenant_id": inp.tenant}
            if pipe is not None:
                pipe.set(f"greq:{req_id}", json.dumps(greq), ex=DEC_TTL)
            side.append(("guardian.events", {"type": "request_created", **greq}))

    latency = _ms(t0)
    stage["total"] = latency
    real = {"decision_id": decision_id, "txn_id": inp.txn_id, "score": round(score, 4), "tier": tier, "action": action,
            "enforced": enforced, "reasons": reasons,
            "scam_family": family_payload(locale, fam, sim, variant=fam_var) if tier != "SAFE" else None,
            "payee": payee, "step_up": step_up, "hold": hold, "guardian": guardian,
            "links": {"calls": calls, "messages": messages}, "model_version": rt.model_version,
            "rules_version": rt.engine.version, "degraded": degraded, "latency_ms": round(latency),
            "user_block": blocked}
    if enforced:
        response = real
    else:                                     # shadow mode: scored and stored, but nothing is enforced or shown
        response = {**real, "tier": "SAFE", "action": "ALLOW", "reasons": [], "scam_family": None, "step_up": None,
                    "hold": None, "guardian": None, "links": {"calls": [], "messages": []}}
        if blocked:                           # ...except the payer's own block, which is theirs to enforce
            response.update({"action": "DECLINE", "reasons": reasons[:1]})

    snapshot = {"decision_id": decision_id, "txn_id": inp.txn_id, "tenant_id": inp.tenant, "user_id": inp.user_id,
                "payer_vpa": inp.payer_vpa, "payee_vpa": inp.payee_vpa, "payee_name": aux["registered_name"],
                "amount": inp.amount, "device_id": inp.device_id, "note": inp.note, "claimed_name": inp.claimed_name,
                "tier": tier, "action": action, "enforced": enforced, "locale": locale, "score": round(score, 4),
                "challenge_id": step_up["challenge_id"] if step_up else None,
                "guardian_request_id": guardian["request_id"] if guardian else None,
                "guardian_required": bool(guardian and guardian["required"]),
                "hold_id": hold["hold_id"] if hold else None, "scam_family": fam, "family_variant": fam_var,
                "user_block": bool(blocked), "created_at": now, "paid": False}
    if pipe is not None:
        # keyed by payer as well as txn_id: one payer can never be handed another's cached decision
        pipe.set(f"decision:{inp.user_id}:{inp.txn_id}", json.dumps(response), ex=600)
        pipe.set(f"dec:{decision_id}", json.dumps(snapshot), ex=DEC_TTL)
        if st.demo_ovr:
            pipe.delete(f"demo_ovr:{inp.user_id}")
        try:
            await pipe.execute()
        except Exception:
            pass
    if enforced and action == "DECLINE" and pair is None and cache_ok and not blocked:
        until = now + policy["pair_block_hours"] * 3600
        await rt.store.set_pair_block(inp.user_id, inp.payee_vpa, until, {
            "decision_id": decision_id, "scam_family": fam, "family_sim": sim, "family_variant": fam_var})
        snapshot["pair_block_until"] = until

    await bus().publish("txn.assessed", {
        "tenant_id": inp.tenant, "synthetic": synthetic, "at": now,
        "request": {"txn_id": inp.txn_id, "user_id": inp.user_id, "payer_vpa": inp.payer_vpa,
                    "payee_vpa": inp.payee_vpa, "claimed_name": inp.claimed_name, "amount": inp.amount,
                    "note": inp.note, "device_id": inp.device_id, "session": inp.session,
                    "parent_decision_id": inp.parent_decision_id, "locale": locale},
        "decision": {**real, "reason_codes": codes, "rules_fired": fired, "family_sim": sim, "scam_family_code": fam,
                     "contributions": {k: round(v, 4) for k, v in gc.items()}},
        "features": f, "stage_ms": stage,
        "detail": {**{k: (round(v, 5) if isinstance(v, float) else v) for k, v in parts.items()},
                   "rule_effects": rr.effects, "payee": payee, "claimed_name": inp.claimed_name,
                   "reasons_en": reasons_en, "step_up": step_up, "hold": hold, "guardian": guardian,
                   "odd_hour": aux.get("odd_hour"), "consortium_source": aux.get("consortium_source"),
                   "pair_block_until": snapshot.get("pair_block_until"), "family_variant": fam_var, "family_en": i18n.family("en", fam or "UNCLASSIFIED")["name"] if fam else None},
    })
    for topic, payload in side:
        await bus().publish(topic, payload)
    rt.metrics.observe(stage, tier, action, degraded)
    return response


async def get_snapshot(decision_id: str) -> dict | None:
    """The working copy of a decision: cache first, PostgreSQL if it has aged out."""
    from . import db

    raw = await rt.store.r.get(f"dec:{decision_id}")
    if raw:
        return json.loads(raw)
    d = await db.fetchrow(
        """SELECT d.*, t.payer_vpa, t.device_id, t.note, t.claimed_name FROM risk_decisions d
           LEFT JOIN transactions t ON t.txn_id=d.txn_id WHERE d.decision_id=$1""", decision_id)
    if not d:
        return None
    det = d["detail"] or {}
    return {"decision_id": str(d["decision_id"]), "txn_id": d["txn_id"], "tenant_id": d["tenant_id"],
            "user_id": str(d["user_id"]), "payer_vpa": d["payer_vpa"], "payee_vpa": d["payee_vpa"],
            "payee_name": (det.get("payee") or {}).get("registered_name", ""), "amount": float(d["amount"]),
            "device_id": str(d["device_id"]) if d["device_id"] else None, "note": d["note"],
            "claimed_name": d["claimed_name"], "tier": d["tier"], "action": d["action"], "enforced": d["enforced"],
            "locale": "en", "score": d["score"], "challenge_id": (det.get("step_up") or {}).get("challenge_id"),
            "guardian_request_id": (det.get("guardian") or {}).get("request_id"),
            "guardian_required": bool((det.get("guardian") or {}).get("required")),
            "hold_id": (det.get("hold") or {}).get("hold_id"), "scam_family": d["scam_family"],
            "family_variant": det.get("family_variant"),
            "created_at": d["created_at"].timestamp(), "paid": False}


async def save_snapshot(snap: dict) -> None:
    await rt.store.r.set(f"dec:{snap['decision_id']}", json.dumps(snap), ex=DEC_TTL)


async def publish_outcome(snap: dict, user_action: str, final_status: str, debited: bool = False) -> None:
    """`debited` says the payer's cached balance has already been reduced (the mock PSP does it at the
    moment of payment), so the worker must not reduce it a second time."""
    await bus().publish("txn.outcome", {
        "tenant_id": snap["tenant_id"], "decision_id": snap["decision_id"], "txn_id": snap["txn_id"],
        "user_id": snap["user_id"], "payer_vpa": snap.get("payer_vpa"), "payee_vpa": snap["payee_vpa"],
        "amount": snap["amount"], "device_id": snap.get("device_id"), "tier": snap["tier"],
        "action": snap["action"], "enforced": snap["enforced"], "user_action": user_action,
        "final_status": final_status, "debited": debited, "user_block": bool(snap.get("user_block")),
        "at": time.time()})
