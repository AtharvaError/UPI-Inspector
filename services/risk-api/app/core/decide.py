"""The decision itself: rules, models, tier, action, reasons, scam family.

One function, used by the live orchestrator, the golden scenarios and the tests, so they cannot
disagree about what a set of features means.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

import numpy as np

from . import explain
from .features import rule_env, vectorise
from .rules import RuleEngine, RuleResult
from .scoring import ModelBundle, action_for, rules_only_score, tier_for
from .textmodel import CLASS_FAMILY

FAMILIES = {"DIGITAL_ARREST", "FAKE_REFUND", "MULE_TRANSFER", "IMPERSONATION", "KYC_BILL_THREAT", "INVESTMENT_TASK"}


@dataclass
class Decision:
    score: float
    tier: str
    action: str
    degraded: bool
    rules: RuleResult
    fired: list[str]
    parts: dict = field(default_factory=dict)
    contribs: np.ndarray | None = None
    reasons_raw: list[dict] = field(default_factory=list)
    family: str | None = None
    family_sim: float | None = None
    groups: dict = field(default_factory=dict)
    stage_ms: dict = field(default_factory=dict)


def _ms(t: float) -> float:
    return round((perf_counter() - t) * 1000, 3)


def decide(f: dict, aux: dict, engine: RuleEngine, bundle: ModelBundle | None, policy: dict, *,
           has_parent: bool = False, model_down: bool = False, state_degraded: bool = False,
           pair_block: dict | None = None, msg_state: dict | None = None) -> Decision:
    stage: dict = {}

    t = perf_counter()
    rr = engine.evaluate(rule_env(f, aux))
    stage["rules"] = _ms(t)

    t = perf_counter()
    contribs, parts, vec = None, {}, None
    model_ok = bundle is not None and bundle.available and not model_down
    if model_ok:
        try:
            vec = vectorise(f)
            sc = bundle.score_one(vec, rr.s_rule)
            cal = sc["score"]
            parts = {k: sc[k] for k in ("raw", "p_xgb", "p_rf", "s_anom", "s_rule")}
        except Exception:
            model_ok = False
    if not model_ok:
        cal = rules_only_score(rr.s_rule)
        parts = {"s_rule": rr.s_rule}
    score = float(engine.apply(cal, rr))
    stage["model"] = _ms(t)

    degraded = bool(state_degraded or not model_ok)
    tier = tier_for(score, policy)
    if degraded and not rr.hard_decline and tier in ("STEP_UP", "BLOCK"):
        # An outage never escalates beyond a warning. Hard rules (denylist, a payee the payer flagged,
        # confirmed fraud) are deterministic evidence and still stop the payment.
        tier = "WARNING"
    action = action_for(tier, score, rr.hard_decline, policy, has_parent)
    fired = list(rr.fired)
    if pair_block is not None:                # a Decline in the last 24 hours for this payer and payee stands
        tier, action = "BLOCK", "DECLINE"
        fired.append("PAIR_BLOCK")

    # reasons and scam family, only when there is something to explain
    t = perf_counter()
    reasons_raw, fam, sim, gc = [], None, None, {}
    if tier != "SAFE":
        if model_ok and vec is not None:      # explanations are computed only for payments that need one
            try:
                contribs = bundle.contribs_one(vec)
            except Exception:
                contribs = None
        gc = explain.group_contributions(contribs)
        reasons_raw = explain.select_reasons(f, aux, rr.reasons, gc)
        protos = bundle.prototypes if (bundle and bundle.prototypes) else explain.default_prototypes()
        z, ind = explain.signal_parts(f, aux, contribs, bundle.attr_weight if bundle else 0.5)
        fam, sim = explain.attribute(z, protos, explain.dim_weights(bundle.attr_story if bundle else 1.0), ind)
        if fam == "UNCLASSIFIED" and f["msg_flag_active"] and msg_state:
            mf = msg_state.get("family") or CLASS_FAMILY.get(msg_state.get("label", ""), "")
            if mf in FAMILIES:
                fam = mf
        if pair_block is not None and pair_block.get("scam_family"):
            fam, sim = pair_block["scam_family"], pair_block.get("family_sim")
    stage["explain"] = _ms(t)

    return Decision(score=score, tier=tier, action=action, degraded=degraded, rules=rr, fired=fired, parts=parts,
                    contribs=contribs, reasons_raw=reasons_raw, family=fam, family_sim=sim, groups=gc, stage_ms=stage)
