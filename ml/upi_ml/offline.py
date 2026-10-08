"""Offline scoring of feature frames with the same code the service runs.

Used by training (stacker inputs, evaluation), the decision backfill and the golden scenarios.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from app.core import explain
from app.core.features import COLUMNS, ENTRY_METHODS, FEATURES, NUMERIC, TEXT_CLASSES, rule_env
from app.core.rules import RuleEngine, RuleResult
from app.core.scoring import ModelBundle, action_for, rules_only_score, tier_for

DEFAULT_POLICY = {"t_warning": 0.30, "t_stepup": 0.60, "t_block": 0.85, "t_decline": 0.95, "block_mode": "hold"}


def frame_to_matrix(df: pd.DataFrame) -> np.ndarray:
    X = np.zeros((len(df), len(COLUMNS)), dtype=np.float32)
    for i, name in enumerate(NUMERIC):
        X[:, i] = df[name].to_numpy(dtype=np.float32)
    base = len(NUMERIC)
    em = df["vpa_entry_method"].to_numpy()
    for j, m in enumerate(ENTRY_METHODS):
        X[:, base + j] = (em == m)
    nl = df["note_label"].to_numpy()
    for j, c in enumerate(TEXT_CLASSES):
        X[:, base + len(ENTRY_METHODS) + j] = (nl == c)
    return X


def row_features(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    feats = df[FEATURES].to_dict("records")
    auxes = [json.loads(a) for a in df["aux_json"]]
    return feats, auxes


def run_rules(feats: list[dict], auxes: list[dict], engine: RuleEngine) -> list[RuleResult]:
    return [engine.evaluate(rule_env(f, a)) for f, a in zip(feats, auxes)]


def score_frame(df: pd.DataFrame, bundle: ModelBundle | None, engine: RuleEngine, policy: dict | None = None,
                explain_rows: bool = False) -> dict:
    """Score every row. With explain_rows, also return reasons and family for rows at Warning or above."""
    policy = policy or DEFAULT_POLICY
    feats, auxes = row_features(df)
    rrs = run_rules(feats, auxes, engine)
    s_rule = np.array([r.s_rule for r in rrs])
    floors = np.array([r.floor for r in rrs])
    caps = np.array([r.cap for r in rrs])
    X = frame_to_matrix(df)
    if bundle is not None and bundle.available:
        parts = bundle.parts(X)
        raw, cal = bundle.combine(parts, s_rule)
    else:
        parts = {}
        raw = cal = np.array([rules_only_score(s) for s in s_rule])
    score = np.maximum(floors, np.minimum(caps, cal))
    tiers = [tier_for(float(s), policy) for s in score]
    out = {"score": score, "raw": raw, "calibrated": cal, "s_rule": s_rule, "tier": tiers, "rules": rrs,
           "parts": parts, "X": X, "feats": feats, "auxes": auxes,
           "action": [action_for(t, float(s), r.hard_decline, policy) for t, s, r in zip(tiers, score, rrs)]}
    if explain_rows:
        protos = (bundle.prototypes if bundle and bundle.prototypes else explain.default_prototypes())
        contribs = parts.get("contribs")
        reasons: list = [None] * len(df)
        fams: list = [None] * len(df)
        sims: list = [None] * len(df)
        groups: list = [None] * len(df)
        for i in np.where(score >= float(policy["t_warning"]))[0]:
            c = contribs[i] if contribs is not None else None
            gc = explain.group_contributions(c)
            reasons[i] = explain.select_reasons(feats[i], auxes[i], rrs[i].reasons, gc)
            z, ind = explain.signal_parts(feats[i], auxes[i], c, bundle.attr_weight if bundle else 0.5)
            fams[i], sims[i] = explain.attribute(z, protos, explain.dim_weights(bundle.attr_story if bundle else 1.0), ind)
            groups[i] = gc
        out.update({"reasons": reasons, "family": fams, "family_sim": sims, "groups": groups})
    return out
