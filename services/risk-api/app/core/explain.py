"""Explainability (F-07) and scam-family attribution (F-08). Docs/06, sections 10 and 11."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .features import COLUMNS, ENTRY_METHODS, NUMERIC, TEXT_CLASSES

DISPLAY_GROUPS = ["payee", "network", "call", "message", "amount", "relationship", "device", "session", "name",
                  "note", "time"]

_GROUP_OF = {
    "amount": ["amount", "log_amount", "amount_z", "amount_over_p95", "balance_fraction"],
    "time": ["hour_of_day", "day_of_week"],
    "note": ["has_note", "note_scam_prob"] + [f"nl_{c}" for c in TEXT_CLASSES],
    "relationship": ["txn_count_1h", "txn_count_24h", "new_payees_24h", "new_payees_10m", "is_new_payee",
                     "prior_txn_count", "days_since_first_paid", "account_age_days"],
    "payee": ["vpa_age_days", "p_bad", "maturity", "fan_in_1h", "fan_in_24h", "first_time_payer_ratio_24h",
              "reports_30d", "confirmed_90d", "near_miss_14d", "is_verified_merchant", "payee_is_individual",
              "payee_on_denylist", "consortium_reported", "consortium_p_bad"],
    "network": ["downstream_hop", "shares_device_with_reported", "passthrough_ratio", "community_risk_share"],
    "device": ["device_is_new", "device_age_days"],
    "session": ["app_switch_count", "time_on_pay_screen_s", "amount_edit_count"] + [f"em_{m}" for m in ENTRY_METHODS],
    "call": ["call_active", "mins_since_call_end", "call_duration_s", "caller_unknown", "caller_risk",
             "unknown_calls_24h"],
    "message": ["msg_flag_active", "msg_scam_prob", "mins_since_msg_flag", "payee_in_flagged_msg"],
    "name": ["name_match_score", "claimed_name_institutional"],
}
COL_GROUP = {col: g for g, cols in _GROUP_OF.items() for col in cols}
GROUP_IDX = {g: np.array([COLUMNS.index(c) for c in cols]) for g, cols in _GROUP_OF.items()}
assert set(COL_GROUP) == set(COLUMNS), set(COLUMNS) ^ set(COL_GROUP)

# --- the 24-dimension signal space used for attribution ------------------------------
DIMS = ["call_active", "call_recent", "caller_risk", "caller_unknown", "msg_flag", "payee_in_msg", "new_payee",
        "payee_young", "payee_fan_in", "payee_reports", "payee_network", "consortium", "amount_anomaly",
        "balance_drain", "velocity", "new_device", "coaching", "name_mismatch", "note_refund", "note_authority",
        "note_kyc", "note_invest", "note_impersonation", "odd_hour"]
_DIM_OF = {
    "call_active": ["call_active", "call_duration_s"],
    "call_recent": ["mins_since_call_end"],
    "caller_risk": ["caller_risk"],
    "caller_unknown": ["caller_unknown", "unknown_calls_24h"],
    "msg_flag": ["msg_flag_active", "msg_scam_prob", "mins_since_msg_flag"],
    "payee_in_msg": ["payee_in_flagged_msg"],
    "new_payee": ["is_new_payee", "prior_txn_count", "days_since_first_paid"],
    "payee_young": ["vpa_age_days", "maturity"],
    "payee_fan_in": ["fan_in_1h", "fan_in_24h", "first_time_payer_ratio_24h"],
    "payee_reports": ["p_bad", "reports_30d", "confirmed_90d", "near_miss_14d", "payee_on_denylist"],
    "payee_network": ["downstream_hop", "shares_device_with_reported", "passthrough_ratio", "community_risk_share"],
    "consortium": ["consortium_reported", "consortium_p_bad"],
    "amount_anomaly": ["amount", "log_amount", "amount_z", "amount_over_p95"],
    "balance_drain": ["balance_fraction"],
    "velocity": ["txn_count_1h", "txn_count_24h", "new_payees_24h", "new_payees_10m"],
    "new_device": ["device_is_new", "device_age_days"],
    "coaching": ["app_switch_count", "time_on_pay_screen_s", "amount_edit_count"] + [f"em_{m}" for m in ENTRY_METHODS],
    "name_mismatch": ["name_match_score", "claimed_name_institutional"],
    "note_refund": ["nl_refund_bait"], "note_authority": ["nl_authority_threat"], "note_kyc": ["nl_kyc_bill_threat"],
    "note_invest": ["nl_investment_task"], "note_impersonation": ["nl_impersonation_request"],
    "odd_hour": ["hour_of_day", "day_of_week"],
}
DIM_IDX = {d: np.array([COLUMNS.index(c) for c in cols]) for d, cols in _DIM_OF.items()}
_NOTE_DIM = {"refund_bait": "note_refund", "authority_threat": "note_authority", "kyc_bill_threat": "note_kyc",
             "investment_task": "note_invest", "impersonation_request": "note_impersonation"}
# The name the payer was given often says which story they were told.
_NAME_HINT = {
    "note_refund": ("refund", "cashback", "customer care", "return"),
    "note_authority": ("cyber", "police", "cbi", "customs", "court", "safe account", "verification", "narcotics"),
    "note_kyc": ("kyc", "bill", "electricity", "bijli", "recharge", "sim"),
    "note_invest": ("trading", "task", "earnings", "capital", "invest", "profit", "advisor"),
    "note_impersonation": ("suppliers", "traders", "logistics", "associates", "vendor", "enterprises"),
}

# Starting prototypes: 1.0 defining, 0.5 supporting (docs/06, section 11). Replaced by learned centroids.
_PROTO = {
    "DIGITAL_ARREST": (["call_active", "caller_risk", "caller_unknown", "new_payee", "amount_anomaly", "balance_drain"],
                       ["note_authority", "coaching", "payee_young"]),
    "FAKE_REFUND": (["call_recent", "note_refund", "name_mismatch", "new_payee"],
                    ["caller_unknown", "msg_flag", "coaching", "payee_young"]),
    "MULE_TRANSFER": (["payee_young", "payee_fan_in", "payee_reports", "payee_network"], ["consortium", "new_payee"]),
    "IMPERSONATION": (["new_payee", "amount_anomaly", "note_impersonation"], ["name_mismatch", "msg_flag", "coaching"]),
    "KYC_BILL_THREAT": (["msg_flag", "note_kyc", "new_payee"], ["caller_risk", "call_recent", "name_mismatch"]),
    "INVESTMENT_TASK": (["note_invest", "velocity"], ["msg_flag", "new_payee", "amount_anomaly", "payee_fan_in"]),
}
SIM_THRESHOLD = 0.6


def default_prototypes() -> dict[str, list[float]]:
    out = {}
    for fam, (defining, supporting) in _PROTO.items():
        v = np.zeros(len(DIMS))
        for d in defining:
            v[DIMS.index(d)] = 1.0
        for d in supporting:
            v[DIMS.index(d)] = 0.5
        out[fam] = v.tolist()
    return out


def group_contributions(contribs: np.ndarray | None) -> dict[str, float]:
    """Sum of model contributions per display group (log-odds units)."""
    if contribs is None:
        return {}
    return {g: float(contribs[idx].sum()) for g, idx in GROUP_IDX.items()}


def indicators(f: dict, aux: dict) -> np.ndarray:
    """0..1 indicator of whether each dimension's underlying condition holds."""
    v = dict.fromkeys(DIMS, 0.0)
    active = f["call_active"] == 1
    recent = (not active) and f["mins_since_call_end"] < 30
    # a long active call is the mark of a digital arrest; a short one is not
    v["call_active"] = (0.4 + 0.6 * min(1.0, f["call_duration_s"] / 1200.0)) if active else 0.0
    v["call_recent"] = 1.0 if recent else 0.0
    v["caller_risk"] = f["caller_risk"] / 3.0 if (active or recent) else 0.0
    v["caller_unknown"] = 1.0 if (f["caller_unknown"] == 1 and (active or recent)) else 0.0
    v["msg_flag"] = float(f["msg_flag_active"]) * float(f["msg_scam_prob"])
    v["payee_in_msg"] = float(f["payee_in_flagged_msg"])
    v["new_payee"] = float(f["is_new_payee"])
    v["payee_young"] = 1.0 if f["vpa_age_days"] < 30 else 0.0
    v["payee_fan_in"] = min(1.0, f["fan_in_1h"] / 10.0) if f["first_time_payer_ratio_24h"] >= 0.6 else 0.0
    v["payee_reports"] = 1.0 if (f["reports_30d"] or f["confirmed_90d"] or f["payee_on_denylist"]) else (
        0.6 if f["p_bad"] >= 0.02 else 0.0)
    v["payee_network"] = 1.0 if (f["downstream_hop"] == 1 or f["shares_device_with_reported"]) else (
        0.7 if f["passthrough_ratio"] >= 0.8 else (0.4 if f["community_risk_share"] >= 0.2 else 0.0))
    v["consortium"] = float(f["consortium_reported"])
    v["amount_anomaly"] = max(0.0, min(1.0, (f["amount_over_p95"] - 1.0) / 4.0))
    v["balance_drain"] = max(0.0, min(1.0, (f["balance_fraction"] - 0.4) / 0.5))
    v["velocity"] = min(1.0, f["new_payees_10m"] / 2.0 + (0.5 if f["txn_count_1h"] >= 3 else 0.0)
                        + (0.5 if (f["is_new_payee"] == 0 and f["days_since_first_paid"] < 7) else 0.0))
    v["new_device"] = float(f["device_is_new"])
    v["coaching"] = min(1.0, (0.5 if f["vpa_entry_method"] in ("pasted", "link") else 0.0)
                        + min(0.5, f["app_switch_count"] / 6.0))
    v["name_mismatch"] = max(1.0 - f["name_match_score"],
                             1.0 if (f["claimed_name_institutional"] and f["payee_is_individual"]) else 0.0)
    probs: dict = {}
    for src in (aux.get("note_probs") or {}, aux.get("msg_probs") or {}):
        for k, p in src.items():
            probs[k] = max(probs.get(k, 0.0), float(p))
    for cls, dim in _NOTE_DIM.items():
        v[dim] = probs.get(cls, 0.0)
    claimed = (aux.get("claimed_name") or "").lower()
    if claimed:
        for dim, words in _NAME_HINT.items():
            if any(w in claimed for w in words):
                v[dim] = max(v[dim], 0.8)
    v["odd_hour"] = float(aux.get("odd_hour", 0))
    return np.array([v[d] for d in DIMS])


def signal_parts(f: dict, aux: dict, contribs: np.ndarray | None, w_ind: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """(signal vector, indicators). The signal vector blends the indicators (weight w_ind) with the model's
    positive contributions per dimension; the weight is chosen at training time and stored with the model.
    The indicators alone say what was observed, and decide whether a family may be named at all."""
    ind = indicators(f, aux)
    if contribs is None or w_ind >= 1.0:
        return ind, ind
    pos = np.array([float(np.clip(contribs[DIM_IDX[d]], 0, None).sum()) for d in DIMS])
    mx = pos.max()
    if mx > 0:
        pos = pos / mx
    return w_ind * ind + (1.0 - w_ind) * pos, ind


def signal_vector(f: dict, aux: dict, contribs: np.ndarray | None, w_ind: float = 0.5) -> np.ndarray:
    return signal_parts(f, aux, contribs, w_ind)[0]


_D = {d: i for i, d in enumerate(DIMS)}


def anchored(fam: str, ind: np.ndarray) -> bool:
    """Is the evidence that defines this family present?

    Nearly every scam is a first payment of an unusual amount, so those signals alone say nothing about
    which scam it is, or whether the payee is at fault. Naming a family tells the payer something specific
    ("this looks like a pass-through account"), so each family needs its own evidence: the call, the story
    told in the note, message or claimed name, or what is known about the payee.
    """
    v = lambda d: float(ind[_D[d]])                                              # noqa: E731
    if fam == "DIGITAL_ARREST":
        # kept on a call: at least five minutes into a call with a number that is unknown or flagged
        # (the call_active indicator is 0.4 at the start of a call and reaches 1.0 at twenty minutes),
        # or any call in the window from a number rated high risk, or an authority story
        held_on_call = v("call_active") >= 0.55 and (v("caller_unknown") > 0 or v("caller_risk") > 0)
        flagged_caller = (v("call_active") > 0 or v("call_recent") > 0) and v("caller_risk") >= 0.66
        return held_on_call or flagged_caller or v("note_authority") >= 0.5
    if fam == "FAKE_REFUND":
        return v("note_refund") >= 0.5
    if fam == "MULE_TRANSFER":            # evidence about the payee itself, not about the payment
        return v("payee_fan_in") >= 0.5 or v("payee_reports") > 0 or v("payee_network") >= 0.7 or v("consortium") > 0
    if fam == "IMPERSONATION":
        return v("note_impersonation") >= 0.5
    if fam == "KYC_BILL_THREAT":
        return v("note_kyc") >= 0.5
    if fam == "INVESTMENT_TASK":
        return v("note_invest") >= 0.5
    return False


STORY_DIMS = np.array([d.startswith("note_") for d in DIMS])


def dim_weights(story: float = 1.0) -> np.ndarray:
    """Weights for the comparison with the prototypes. The "story" dimensions (what the note, the message
    and the claimed name say) are the ones that differ most between families; the rest (new payee, young
    account, unusual amount) are shared by nearly every scam. The story weight is chosen at training time."""
    return np.where(STORY_DIMS, float(story), 1.0)


def attribute(z: np.ndarray, prototypes: dict[str, Any], weights: np.ndarray | None = None,
              ind: np.ndarray | None = None) -> tuple[str, float]:
    """The closest family by weighted cosine similarity, if it is close enough and, when the indicators
    are given, its defining evidence is present (see `anchored`). Otherwise Unclassified."""
    if weights is not None:
        z = z * weights
    zn = float(np.linalg.norm(z))
    if zn == 0:
        return "UNCLASSIFIED", 0.0
    sims: list[tuple[float, str]] = []
    for fam, proto in prototypes.items():
        p = np.asarray(proto, dtype=float)
        if weights is not None:
            p = p * weights
        pn = float(np.linalg.norm(p))
        if pn > 0:
            sims.append((float(z @ p) / (zn * pn), fam))
    sims.sort(reverse=True)
    for sim, fam in sims:
        if sim < SIM_THRESHOLD:
            break
        if ind is None or anchored(fam, ind):
            return fam, sim
    return "UNCLASSIFIED", (sims[0][0] if sims else 0.0)


def family_variant(fam: str | None, f: dict) -> str | None:
    """Which wording of the family text fits the evidence. The usual mule-transfer text says many new
    people paid the account in the last hour; the usual impersonation text says the payer has never paid
    it. When that is not what was seen, a variant without the claim is used."""
    if fam == "MULE_TRANSFER" and f.get("fan_in_1h", 0) < 5:
        return "network"
    if fam == "IMPERSONATION" and f.get("is_new_payee", 1) == 0:
        return "known"
    return None


# --- reasons ---------------------------------------------------------------------
# code -> (display group, priority: lower shows first)
REASON_META = {
    "PAYEE_IN_SCAM_MESSAGE": ("message", 1), "PAYEE_REPORTED": ("payee", 2), "NAME_MISMATCH": ("name", 3),
    "CALLER_FLAGGED": ("call", 4), "CALL_ACTIVE_UNKNOWN": ("call", 5), "REPORTED_AT_OTHER_BANK": ("payee", 6),
    "PAYEE_NEAR_REPORTED": ("network", 7), "PAYEE_FAN_IN": ("payee", 8), "MESSAGE_FLAGGED": ("message", 9),
    "PAYEE_PASSTHROUGH": ("network", 10), "AMOUNT_UNUSUAL": ("amount", 11), "BALANCE_DRAIN": ("amount", 12),
    "NOTE_SCAM_LANGUAGE": ("note", 13), "APP_SWITCHING": ("session", 14), "VPA_PASTED": ("session", 15),
    "DEVICE_CHANGED": ("device", 16), "RAPID_NEW_PAYEES": ("relationship", 17), "PAYEE_NEW_VPA": ("payee", 18),
    "NEW_PAYEE": ("relationship", 19), "ODD_HOUR": ("time", 20),
}


def _eligible(code: str, f: dict, aux: dict) -> tuple[bool, str | None, dict]:
    """(is the reason true?, template variant, raw evidence)."""
    active = f["call_active"] == 1
    in_window = active or f["mins_since_call_end"] < 30
    if code == "NEW_PAYEE":
        return f["is_new_payee"] == 1, None, {}
    if code == "PAYEE_NEW_VPA":
        return bool(aux.get("vpa_exists")) and f["vpa_age_days"] < 30, None, {"vpa_age_days": round(f["vpa_age_days"], 2)}
    if code == "PAYEE_FAN_IN":
        return f["fan_in_1h"] >= 5, None, {"fan_in_1h": int(f["fan_in_1h"])}
    if code == "PAYEE_REPORTED":
        return aux.get("band") == "REPORTED" or f["payee_on_denylist"] == 1, None, {}
    if code == "PAYEE_NEAR_REPORTED":
        return f["downstream_hop"] == 1 or f["shares_device_with_reported"] == 1, None, {}
    if code == "PAYEE_PASSTHROUGH":
        return f["passthrough_ratio"] >= 0.8, None, {"passthrough_ratio": round(f["passthrough_ratio"], 2)}
    if code == "REPORTED_AT_OTHER_BANK":
        return f["consortium_reported"] == 1, None, {"source": aux.get("consortium_source", "")}
    if code == "CALL_ACTIVE_UNKNOWN":
        return active and f["caller_unknown"] == 1, None, {"call_duration_s": int(f["call_duration_s"])}
    if code == "CALLER_FLAGGED":
        return in_window and f["caller_risk"] >= 1, ("active" if active else None), {
            "mins_since_call": max(1, int(round(f["mins_since_call_end"]))), "caller_risk": int(f["caller_risk"])}
    if code == "MESSAGE_FLAGGED":
        return f["msg_flag_active"] == 1, None, {}
    if code == "PAYEE_IN_SCAM_MESSAGE":
        return f["payee_in_flagged_msg"] == 1, None, {}
    if code == "AMOUNT_UNUSUAL":
        mean = aux.get("mean_amount") or 0.0
        ok = f["amount_over_p95"] >= 2.0 and mean > 0 and f["amount"] / mean >= 2.0
        return ok, None, {"amount_ratio": int(round(f["amount"] / mean)) if mean > 0 else 0}
    if code == "BALANCE_DRAIN":
        return f["balance_fraction"] >= 0.8, None, {"balance_fraction": round(f["balance_fraction"], 2)}
    if code == "DEVICE_CHANGED":
        return f["device_is_new"] == 1, ("new" if f["device_age_days"] <= 0 else None), {
            "device_age_days": round(f["device_age_days"], 3)}
    if code == "RAPID_NEW_PAYEES":
        return f["new_payees_10m"] >= 2, None, {"count": int(f["new_payees_10m"]) + 1}
    if code == "VPA_PASTED":
        return f["vpa_entry_method"] == "pasted" and f["is_new_payee"] == 1, None, {}
    if code == "APP_SWITCHING":
        return f["app_switch_count"] >= 2, None, {"app_switch_count": int(f["app_switch_count"])}
    if code == "NAME_MISMATCH":
        ok = bool(aux.get("claimed_name")) and bool(aux.get("registered_name")) and (
            f["name_match_score"] < 0.5 or (f["claimed_name_institutional"] == 1 and f["payee_is_individual"] == 1))
        src = f["vpa_entry_method"] if f["vpa_entry_method"] in ("link", "qr") else "typed"
        return ok, None, {"source_key": src, "claimed_name": aux.get("claimed_name", ""),
                          "registered_name": aux.get("registered_name", "")}
    if code == "NOTE_SCAM_LANGUAGE":
        return f["note_scam_prob"] >= 0.6, None, {"note_label": f["note_label"]}
    if code == "ODD_HOUR":
        return aux.get("odd_hour", 0) == 1, None, {}
    return False, None, {}


def select_reasons(f: dict, aux: dict, rule_reasons: list[str], group_contrib: dict[str, float],
                   limit: int = 3) -> list[dict]:
    """Rule reasons first, then the groups the model leaned on most. Every reason must be true."""
    chosen: list[dict] = []
    seen: set[str] = set()

    def add(code: str) -> None:
        if code in seen or len(chosen) >= limit:
            return
        ok, variant, ev = _eligible(code, f, aux)
        if ok:
            seen.add(code)
            chosen.append({"code": code, "group": REASON_META[code][0], "variant": variant, "evidence": ev})

    for code in sorted(rule_reasons, key=lambda c: REASON_META.get(c, ("", 99))[1]):
        add(code)
    by_group: dict[str, list[str]] = {}
    for code, (group, _prio) in sorted(REASON_META.items(), key=lambda kv: kv[1][1]):
        by_group.setdefault(group, []).append(code)
    for group, value in sorted(group_contrib.items(), key=lambda kv: -kv[1]):
        if value <= 0 or len(chosen) >= limit:
            break
        for code in by_group.get(group, []):
            before = len(chosen)
            add(code)
            if len(chosen) > before:
                break                                   # one reason per group in this pass
    if len(chosen) < limit:                             # fill from anything still true, by priority
        for code, _meta in sorted(REASON_META.items(), key=lambda kv: kv[1][1]):
            if code in ("ODD_HOUR", "PAYEE_NEW_VPA", "NEW_PAYEE") and len(chosen) >= 2:
                continue
            add(code)
    if not chosen:                                      # coverage is always 100% (docs/06, section 10)
        chosen.append({"code": "AMOUNT_UNUSUAL", "group": "amount", "variant": "generic", "evidence": {}})
    return chosen


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, x))))
