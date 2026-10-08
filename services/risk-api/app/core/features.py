"""Feature catalogue v1 and the one function that computes features.

The service and the training code both import this module (docs/09, "one feature definition").
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np

from . import reputation as rep
from .textcheck import is_institutional, name_match

IST = timezone(timedelta(hours=5, minutes=30))
HOUR = 3600.0
DAY = 86400.0

ENTRY_METHODS = ["typed", "pasted", "contact", "qr", "link", "merchant_collect"]
TEXT_CLASSES = ["benign", "refund_bait", "authority_threat", "kyc_bill_threat", "investment_task",
                "impersonation_request", "prize"]

NUMERIC = [
    # transaction
    "amount", "log_amount", "hour_of_day", "day_of_week", "has_note",
    # payer behaviour
    "amount_z", "amount_over_p95", "txn_count_1h", "txn_count_24h", "new_payees_24h", "new_payees_10m",
    "account_age_days", "balance_fraction",
    # relationship
    "is_new_payee", "prior_txn_count", "days_since_first_paid",
    # payee reputation
    "vpa_age_days", "p_bad", "maturity", "fan_in_1h", "fan_in_24h", "first_time_payer_ratio_24h",
    "reports_30d", "confirmed_90d", "near_miss_14d", "is_verified_merchant", "payee_is_individual",
    "payee_on_denylist",
    # network
    "downstream_hop", "shares_device_with_reported", "passthrough_ratio", "community_risk_share",
    # consortium
    "consortium_reported", "consortium_p_bad",
    # device
    "device_is_new", "device_age_days",
    # session
    "app_switch_count", "time_on_pay_screen_s", "amount_edit_count",
    # call
    "call_active", "mins_since_call_end", "call_duration_s", "caller_unknown", "caller_risk", "unknown_calls_24h",
    # message
    "msg_flag_active", "msg_scam_prob", "mins_since_msg_flag", "payee_in_flagged_msg",
    # name
    "name_match_score", "claimed_name_institutional",
    # note
    "note_scam_prob",
]
CATEGORICAL = {"vpa_entry_method": ENTRY_METHODS, "note_label": TEXT_CLASSES}
FEATURES = NUMERIC + list(CATEGORICAL)                      # the 54 logical features
COLUMNS = NUMERIC + [f"em_{m}" for m in ENTRY_METHODS] + [f"nl_{c}" for c in TEXT_CLASSES]

# Neutral defaults, used when a source is unavailable (docs/05, section 4).
DEFAULTS: dict[str, Any] = {
    "amount": 0.0, "log_amount": 0.0, "hour_of_day": 12, "day_of_week": 0, "has_note": 0,
    "amount_z": 0.0, "amount_over_p95": 1.0, "txn_count_1h": 0, "txn_count_24h": 0, "new_payees_24h": 0,
    "new_payees_10m": 0, "account_age_days": 365.0, "balance_fraction": 0.0,
    "is_new_payee": 1, "prior_txn_count": 0, "days_since_first_paid": 0.0,
    "vpa_age_days": 180.0, "p_bad": 0.01, "maturity": 0.0, "fan_in_1h": 0, "fan_in_24h": 0,
    "first_time_payer_ratio_24h": 0.5, "reports_30d": 0, "confirmed_90d": 0, "near_miss_14d": 0,
    "is_verified_merchant": 0, "payee_is_individual": 1, "payee_on_denylist": 0,
    "downstream_hop": 9, "shares_device_with_reported": 0, "passthrough_ratio": 0.0, "community_risk_share": 0.0,
    "consortium_reported": 0, "consortium_p_bad": 0.0,
    "device_is_new": 0, "device_age_days": 365.0,
    "app_switch_count": 0, "time_on_pay_screen_s": 30.0, "amount_edit_count": 0,
    "call_active": 0, "mins_since_call_end": 999.0, "call_duration_s": 0.0, "caller_unknown": 0, "caller_risk": 0,
    "unknown_calls_24h": 0,
    "msg_flag_active": 0, "msg_scam_prob": 0.0, "mins_since_msg_flag": 9999.0, "payee_in_flagged_msg": 0,
    "name_match_score": 1.0, "claimed_name_institutional": 0,
    "note_scam_prob": 0.0,
    "vpa_entry_method": "typed", "note_label": "benign",
}

CALL_WINDOW_S = 30 * 60          # a call matters while active and for 30 minutes after it ends
MSG_WINDOW_S = 24 * HOUR
MSG_VPA_WINDOW_S = 7 * DAY


def vectorise(features: dict) -> np.ndarray:
    v = np.zeros(len(COLUMNS), dtype=np.float32)
    for i, name in enumerate(NUMERIC):
        v[i] = float(features.get(name, DEFAULTS[name]))
    base = len(NUMERIC)
    em = features.get("vpa_entry_method", "typed")
    if em in ENTRY_METHODS:
        v[base + ENTRY_METHODS.index(em)] = 1.0
    nl = features.get("note_label", "benign")
    if nl in TEXT_CLASSES:
        v[base + len(ENTRY_METHODS) + TEXT_CLASSES.index(nl)] = 1.0
    return v


@dataclass
class AssessInput:
    txn_id: str
    tenant: str
    user_id: str
    payer_vpa: str
    payee_vpa: str
    amount: float
    device_id: str | None = None
    claimed_name: str | None = None
    note: str | None = None
    session: dict = field(default_factory=dict)
    parent_decision_id: str | None = None
    locale: str = "en"


@dataclass
class RawState:
    """Everything enrichment read from the cache in one round trip."""
    cached_decision: str | None = None
    policy: dict = field(default_factory=dict)
    user_prof: dict = field(default_factory=dict)
    known: str | None = None
    vel: list = field(default_factory=list)
    dev_first: str | None = None
    vdir: dict = field(default_factory=dict)
    vrep: dict = field(default_factory=dict)
    fan_1h: int = 0
    fan_24h: int = 0
    first_24h: int = 0
    reports_30d: int = 0
    confirmed_90d: int = 0
    near_miss_14d: int = 0
    graph: dict = field(default_factory=dict)
    cons: dict = field(default_factory=dict)
    call: dict = field(default_factory=dict)
    unknown_calls_24h: int = 0
    msg: dict = field(default_factory=dict)
    msg_vpa_ts: float | None = None
    pair_block: str | None = None
    guardian: dict = field(default_factory=dict)
    demo_ovr: dict = field(default_factory=dict)
    user_block: str | None = None       # set if the payer put this payee on their "never pay" list (F-33)
    degraded: bool = False


def _f(d: dict, k: str, default: float = 0.0) -> float:
    try:
        return float(d.get(k, default))
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def parse_vel(members: list) -> list[tuple[float, float, str, int]]:
    out = []
    for m in members:
        try:
            ts, amt, payee, is_new = m.split("|")
            out.append((float(ts), float(amt), payee, int(is_new)))
        except ValueError:
            continue
    return out


Classifier = Callable[[str], tuple[str, dict, float]]


def compute_features(req: AssessInput, st: RawState, now: float,
                     classify: Classifier | None = None) -> tuple[dict, dict]:
    """Return (features, aux). `aux` carries values the rules, reasons and response need
    but which are not model features."""
    f: dict[str, Any] = dict(DEFAULTS)
    aux: dict[str, Any] = {}

    # --- transaction ---------------------------------------------------------
    amount = float(req.amount)
    local = datetime.fromtimestamp(now, IST)
    note = (req.note or "").strip()
    f["amount"] = amount
    f["log_amount"] = math.log1p(amount)
    f["hour_of_day"] = local.hour
    f["day_of_week"] = local.weekday()
    f["has_note"] = 1 if note else 0

    # --- payer behaviour (last 30 days of completed payments) -------------------
    vel = parse_vel(st.vel)
    amts = [a for _, a, _, _ in vel]
    n = len(amts)
    mean_amt = 0.0
    if n >= 5:
        arr = np.asarray(amts)
        mean_amt = float(arr.mean())
        std = float(arr.std()) or 1.0
        p95 = float(np.percentile(arr, 95))
        f["amount_z"] = _clamp((amount - mean_amt) / max(std, 1.0), -5.0, 50.0)
        f["amount_over_p95"] = _clamp(amount / max(p95, 1.0), 0.0, 500.0)
    f["txn_count_1h"] = sum(1 for t, *_ in vel if t >= now - HOUR)
    f["txn_count_24h"] = sum(1 for t, *_ in vel if t >= now - DAY)
    f["new_payees_24h"] = sum(1 for t, _, _, nw in vel if nw and t >= now - DAY)
    f["new_payees_10m"] = sum(1 for t, _, _, nw in vel if nw and t >= now - 600)
    first_seen = _f(st.user_prof, "first_seen", now - 365 * DAY)
    f["account_age_days"] = max(0.0, (now - first_seen) / DAY)
    balance = _f(st.user_prof, "balance", 0.0)
    f["balance_fraction"] = _clamp(amount / balance, 0.0, 1.0) if balance > 0 else 0.0
    aux["mean_amount"] = mean_amt
    aux["n_history"] = n
    # unusual hour: the payer has history and makes under 2% of payments in this hour
    if n >= 20:
        same_hour = sum(1 for t, *_ in vel if datetime.fromtimestamp(t, IST).hour == local.hour)
        aux["odd_hour"] = 1 if same_hour / n < 0.02 else 0
    else:
        aux["odd_hour"] = 0

    # --- relationship ----------------------------------------------------------
    if st.known:
        try:
            cnt, first_paid = st.known.split("|")
            f["is_new_payee"] = 0
            f["prior_txn_count"] = int(cnt)
            f["days_since_first_paid"] = max(0.0, (now - float(first_paid)) / DAY)
        except ValueError:
            pass

    # --- payee: directory and this bank's reputation view ---------------------------
    exists = bool(st.vdir)
    aux["vpa_exists"] = exists
    aux["registered_name"] = st.vdir.get("registered_name", "")
    aux["payee_kind"] = st.vdir.get("kind", "person")
    vpa_first = _f(st.vdir, "first_seen", now - 180 * DAY)
    verified = st.vdir.get("verified") in ("1", "true", "True")
    if exists:
        f["vpa_age_days"] = max(0.0, (now - vpa_first) / DAY)
        f["is_verified_merchant"] = 1 if (verified and aux["payee_kind"] == "merchant") else 0
        f["payee_is_individual"] = 1 if aux["payee_kind"] == "person" else 0
        f["payee_on_denylist"] = 1 if st.vdir.get("on_denylist") in ("1", "true", "True") else 0
    f["fan_in_1h"] = st.fan_1h
    f["fan_in_24h"] = st.fan_24h
    f["first_time_payer_ratio_24h"] = (st.first_24h / st.fan_24h) if st.fan_24h > 0 else 0.5
    f["reports_30d"] = st.reports_30d
    f["confirmed_90d"] = st.confirmed_90d
    f["near_miss_14d"] = st.near_miss_14d
    d = rep.derive(
        recv=_f(st.vrep, "recv"), bad=_f(st.vrep, "bad"), counters_at=_f(st.vrep, "at", now),
        lifetime_recv=int(_f(st.vrep, "lifetime")), confirmed_90d=st.confirmed_90d, first_seen=vpa_first,
        now=now, reports_30d=st.reports_30d, near_miss_14d=st.near_miss_14d, fan_in_1h=st.fan_1h,
        first_time_ratio=f["first_time_payer_ratio_24h"], verified=verified, exists=exists,
        individual=aux["payee_kind"] == "person",
    )
    f["p_bad"] = d["p_bad"]
    f["maturity"] = d["maturity"]
    aux["band"] = d["band"]
    aux["trust_score"] = d["trust_score"]
    aux["band_reason"] = d["band_reason"]

    # --- network -----------------------------------------------------------------
    if st.graph:
        f["downstream_hop"] = int(_f(st.graph, "downstream_hop", 9))
        f["shares_device_with_reported"] = int(_f(st.graph, "shares_device_with_reported"))
        f["passthrough_ratio"] = _clamp(_f(st.graph, "passthrough_ratio"), 0.0, 1.0)
        f["community_risk_share"] = _clamp(_f(st.graph, "community_risk_share"), 0.0, 1.0)

    # --- consortium ----------------------------------------------------------------
    if st.cons:
        f["consortium_reported"] = int(_f(st.cons, "reported"))
        f["consortium_p_bad"] = _clamp(_f(st.cons, "p_bad"), 0.0, 1.0)
        aux["consortium_confirmed"] = int(_f(st.cons, "confirmed"))
        aux["consortium_source"] = st.cons.get("source_tenant", "")
    else:
        aux["consortium_confirmed"] = 0

    # --- device --------------------------------------------------------------------
    if req.device_id:
        if st.dev_first is None:
            f["device_is_new"] = 1
            f["device_age_days"] = 0.0
        else:
            age = max(0.0, (now - float(st.dev_first)) / DAY)
            f["device_age_days"] = age
            f["device_is_new"] = 1 if age < 1.0 else 0

    # --- session (from the client; clamped) ---------------------------------------
    s = dict(req.session or {})
    for k, v in (st.demo_ovr or {}).items():        # demo staging may raise session values
        if k == "vpa_entry_method":
            s[k] = v
        elif k in ("app_switch_count", "time_on_pay_screen_s", "amount_edit_count"):
            s[k] = max(float(s.get(k, 0) or 0), float(v))
    em = s.get("vpa_entry_method", "typed")
    f["vpa_entry_method"] = em if em in ENTRY_METHODS else "typed"
    f["app_switch_count"] = int(_clamp(float(s.get("app_switch_count", 0) or 0), 0, 20))
    f["time_on_pay_screen_s"] = _clamp(float(s.get("time_on_pay_screen_s", 30) or 0), 0, 3600)
    f["amount_edit_count"] = int(_clamp(float(s.get("amount_edit_count", 0) or 0), 0, 20))

    # --- call context -----------------------------------------------------------------
    aux["call_id"] = None
    if st.call:
        started = _f(st.call, "started_at", 0)
        ended = _f(st.call, "ended_at", 0)
        active = st.call.get("state") == "active" and now - started < 6 * HOUR
        recent = (not active) and ended > 0 and 0 <= now - ended <= CALL_WINDOW_S
        if active or recent:
            aux["call_id"] = st.call.get("call_id")
            f["call_active"] = 1 if active else 0
            f["mins_since_call_end"] = 0.0 if active else (now - ended) / 60.0
            f["call_duration_s"] = max(0.0, (now - started) if active else (ended - started))
            f["caller_unknown"] = 0 if st.call.get("caller_known") in ("1", "true", "True") else 1
            f["caller_risk"] = int(_f(st.call, "caller_risk"))
    f["unknown_calls_24h"] = st.unknown_calls_24h

    # --- message flag -----------------------------------------------------------------
    aux["msg_check_id"] = None
    msg_probs: dict = {}
    if st.msg:
        set_at = _f(st.msg, "set_at", 0)
        if 0 <= now - set_at <= MSG_WINDOW_S:
            aux["msg_check_id"] = st.msg.get("check_id")
            f["msg_flag_active"] = 1
            f["msg_scam_prob"] = _clamp(_f(st.msg, "scam_prob"), 0.0, 1.0)
            f["mins_since_msg_flag"] = (now - set_at) / 60.0
            label = st.msg.get("label", "")
            if label in TEXT_CLASSES:
                msg_probs[label] = f["msg_scam_prob"]
    if st.msg_vpa_ts is not None and 0 <= now - float(st.msg_vpa_ts) <= MSG_VPA_WINDOW_S:
        f["payee_in_flagged_msg"] = 1
    aux["msg_probs"] = msg_probs

    # --- name match -----------------------------------------------------------------
    claimed = (req.claimed_name or "").strip()
    aux["claimed_name"] = claimed
    if claimed and exists and aux["registered_name"]:
        f["name_match_score"] = name_match(claimed, aux["registered_name"])
        f["claimed_name_institutional"] = 1 if is_institutional(claimed) else 0
    elif claimed:
        f["claimed_name_institutional"] = 1 if is_institutional(claimed) else 0

    # --- note text -----------------------------------------------------------------
    note_probs: dict = {}
    if note and classify is not None:
        label, probs, scam_prob = classify(note)
        f["note_label"] = label
        f["note_scam_prob"] = scam_prob
        note_probs = probs
    aux["note_probs"] = note_probs

    aux["pair_block"] = st.pair_block
    aux["has_parent"] = 1 if req.parent_decision_id else 0
    aux["degraded"] = st.degraded
    return f, aux


def rule_env(features: dict, aux: dict) -> dict:
    """Names available to rule expressions: every feature plus a few aux values."""
    env = dict(features)
    env["consortium_confirmed"] = aux.get("consortium_confirmed", 0)
    env["odd_hour"] = aux.get("odd_hour", 0)
    return env
