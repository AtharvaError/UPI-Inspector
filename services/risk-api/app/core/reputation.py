"""Payee reputation maths (docs/06, section 6). Pure functions."""
from __future__ import annotations

import math

DAY = 86400.0
HALF_LIFE_S = 30 * DAY          # one half-life for both counters, on purpose

W_CONFIRMED = 5.0
W_REPORT = 1.0
W_FLAGGED_MESSAGE = 1.0
W_NEAR_MISS = 0.3
W_NEAR_MISS_STRONG = 0.6        # the payer admitted being coached, or the guardian refused

BANDS = ["TRUSTED", "NEUTRAL", "NO_HISTORY", "CAUTION", "REPORTED"]


def decay(value: float, last_ts: float, now: float) -> float:
    if value <= 0:
        return 0.0
    return value * 2.0 ** (-(max(0.0, now - last_ts)) / HALF_LIFE_S)


def derive(*, recv: float, bad: float, counters_at: float, lifetime_recv: int, confirmed_90d: int,
           first_seen: float, now: float, reports_30d: int = 0, near_miss_14d: int = 0, fan_in_1h: int = 0,
           first_time_ratio: float = 0.0, verified: bool = False, exists: bool = True,
           individual: bool = True) -> dict:
    recv_now = decay(recv, counters_at, now)
    bad_now = decay(bad, counters_at, now)
    p_bad = (0.5 + bad_now) / (50.0 + recv_now)                 # smoothed rate, prior mean 1%
    maturity = 1.0 - math.exp(-recv_now / 20.0)
    trust = round(100 * (1 - min(1.0, p_bad / 0.10)) * (0.5 + 0.5 * maturity))
    age_days = max(0.0, (now - first_seen) / DAY)

    # a young personal UPI ID collecting from many first-time payers (the R-04 condition).
    # A new shop with a genuine rush of customers is not treated this way.
    burst = individual and (not verified) and age_days < 7 and fan_in_1h >= 10 and first_time_ratio >= 0.8

    reason = None
    if confirmed_90d >= 1:
        band = "REPORTED"                      # needs confirmed fraud; a high rate alone stays at CAUTION
        reason = "reported"
    elif p_bad >= 0.02 or burst:
        band = "CAUTION"
        if reports_30d > 0:
            reason = "reports"
        elif burst or fan_in_1h >= 5:
            reason = "fan_in"
        elif near_miss_14d >= 1:
            reason = "near_miss"
        else:
            reason = "activity"
        if burst:
            trust = min(trust, 30)
    elif (not exists) or age_days < 30 or lifetime_recv < 3:
        band = "NO_HISTORY"
    elif bad_now < 0.05 and maturity >= 0.8 and age_days >= 90:
        band = "TRUSTED"
    else:
        band = "NEUTRAL"
    return {"p_bad": p_bad, "maturity": maturity, "trust_score": int(trust), "band": band,
            "band_reason": reason, "recv_now": recv_now, "bad_now": bad_now}


def near_miss_weight(user_action: str) -> float:
    return {
        "ABORTED": W_NEAR_MISS, "STEPUP_ABANDONED": W_NEAR_MISS, "HOLD_CANCELLED": W_NEAR_MISS,
        "STEPUP_FAILED": W_NEAR_MISS_STRONG, "GUARDIAN_DENIED": W_NEAR_MISS_STRONG,
    }.get(user_action, 0.0)
