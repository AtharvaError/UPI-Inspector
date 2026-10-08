"""Unit tests for the pure risk logic. No database, no server:  python manage.py test"""
from __future__ import annotations

import asyncio
import math
import time

import fakeredis
import numpy as np
import pytest

from app.core import explain
from app.core import reputation as rep
from app.core.features import COLUMNS, DEFAULTS, FEATURES, NUMERIC, AssessInput, RawState, compute_features, rule_env, vectorise
from app.core.graphfeat import compute_graph_features, downstream_hops, passthrough_ratios
from app.core.rules import RuleEngine, _compile
from app.core.scoring import MODEL_CAP, action_for, rules_only_score, tier_for
from app.core.state import FeatureStore
from app.core.textcheck import extract_entities, is_institutional, name_match, red_flags
from app.core.textmodel import verdict_for
from app.routers.ops import policy_metrics

POLICY = {"t_warning": 0.30, "t_stepup": 0.60, "t_block": 0.85, "t_decline": 0.95, "block_mode": "hold"}
DAY = 86400.0


def env(**over):
    f = dict(DEFAULTS)
    f.update(over)
    return rule_env(f, {"consortium_confirmed": over.pop("consortium_confirmed", 0) if "consortium_confirmed" in over else 0})


# ------------------------------------------------------------------ feature catalogue
def test_catalogue_has_54_features_with_defaults():
    assert len(FEATURES) == 54
    assert set(FEATURES) <= set(DEFAULTS)
    assert len(COLUMNS) == len(NUMERIC) + 6 + 7


def test_vectorise_one_hot():
    v = vectorise({**DEFAULTS, "vpa_entry_method": "pasted", "note_label": "refund_bait", "amount": 500})
    assert v[COLUMNS.index("em_pasted")] == 1 and v[COLUMNS.index("em_typed")] == 0
    assert v[COLUMNS.index("nl_refund_bait")] == 1 and v[COLUMNS.index("amount")] == 500


# ------------------------------------------------------------------ tiers and actions
@pytest.mark.parametrize("score,tier", [(0.0, "SAFE"), (0.2999, "SAFE"), (0.30, "WARNING"), (0.5999, "WARNING"),
                                        (0.60, "STEP_UP"), (0.8499, "STEP_UP"), (0.85, "BLOCK"), (1.0, "BLOCK")])
def test_tier_boundaries(score, tier):
    assert tier_for(score, POLICY) == tier


def test_action_selection():
    assert action_for("SAFE", 0.1, False, POLICY) == "ALLOW"
    assert action_for("WARNING", 0.4, False, POLICY) == "WARN"
    assert action_for("STEP_UP", 0.7, False, POLICY) == "STEP_UP"
    assert action_for("BLOCK", 0.90, False, POLICY) == "HOLD"
    assert action_for("BLOCK", 0.95, False, POLICY) == "DECLINE"            # at or above the decline threshold
    assert action_for("BLOCK", 0.90, True, POLICY) == "DECLINE"             # a hard rule
    assert action_for("BLOCK", 0.90, False, {**POLICY, "block_mode": "decline"}) == "DECLINE"
    assert action_for("BLOCK", 0.90, False, POLICY, has_parent=True) == "DECLINE"   # never held twice


def test_model_alone_cannot_reach_decline():
    assert MODEL_CAP < POLICY["t_decline"]
    assert rules_only_score(5.0) <= MODEL_CAP


# ------------------------------------------------------------------ rules
@pytest.fixture(scope="module")
def engine():
    return RuleEngine()


FIRING = {
    "R-01": dict(payee_on_denylist=1),
    "R-02": dict(caller_risk=3, call_active=1, is_new_payee=1),
    "R-03": dict(is_new_payee=1, amount_over_p95=6, call_active=1, caller_unknown=1),
    "R-04": dict(payee_is_individual=1, vpa_age_days=2, fan_in_1h=12, first_time_payer_ratio_24h=0.9),
    "R-04b": dict(payee_is_individual=1, vpa_age_days=2, fan_in_1h=25, first_time_payer_ratio_24h=0.9, is_new_payee=1, amount=5000),
    "R-05": dict(device_is_new=1, is_new_payee=1, amount=15000),
    "R-06": dict(new_payees_10m=3),
    "R-07": dict(note_scam_prob=0.9),
    "R-08": dict(balance_fraction=0.85),
    "R-10": dict(payee_in_flagged_msg=1),
    "R-11": dict(vpa_entry_method="pasted", is_new_payee=1, app_switch_count=2, amount_over_p95=1.5),
    "R-12a": dict(vpa_entry_method="qr", name_match_score=0.2),
    "R-12b": dict(claimed_name_institutional=1, payee_is_individual=1),
    "R-12c": dict(claimed_name_institutional=1, payee_is_individual=1, is_new_payee=1),
    "R-13": dict(msg_flag_active=1, is_new_payee=1),
    "R-14": dict(downstream_hop=1),
    "R-15a": dict(consortium_reported=1),
    "R-16": dict(confirmed_90d=1, is_new_payee=1),
    "R-17": dict(is_new_payee=1, amount_over_p95=4, amount=8000),
}


@pytest.mark.parametrize("rule_id,over", FIRING.items())
def test_each_rule_fires(engine, rule_id, over):
    f = {**DEFAULTS, "is_new_payee": 0, **over}
    assert rule_id in engine.evaluate(rule_env(f, {})).fired


def test_quiet_payment_fires_nothing(engine):
    f = {**DEFAULTS, "is_new_payee": 0, "prior_txn_count": 1, "amount": 300}
    assert engine.evaluate(rule_env(f, {})).fired == []


def test_r15b_needs_confirmation(engine):
    f = {**DEFAULTS, "consortium_reported": 1}
    assert "R-15b" not in engine.evaluate(rule_env(f, {"consortium_confirmed": 0})).fired
    res = engine.evaluate(rule_env(f, {"consortium_confirmed": 1}))
    assert "R-15b" in res.fired and res.floor >= 0.60


def test_r09_caps_long_relationship_unless_hard_rule(engine):
    f = {**DEFAULTS, "is_new_payee": 0, "prior_txn_count": 5, "days_since_first_paid": 90}
    res = engine.evaluate(rule_env(f, {}))
    assert "R-09" in res.fired and res.cap == pytest.approx(0.29)
    assert engine.apply(0.9, res) == pytest.approx(0.29)
    res = engine.evaluate(rule_env({**f, "payee_on_denylist": 1}, {}))
    assert "R-09" not in res.fired and res.hard_decline and engine.apply(0.1, res) >= 0.95


def test_hard_rules_and_floors(engine):
    assert engine.evaluate(rule_env({**DEFAULTS, "payee_in_flagged_msg": 1}, {})).hard_decline
    assert engine.evaluate(rule_env({**DEFAULTS, "confirmed_90d": 1, "is_new_payee": 1}, {})).hard_decline
    assert not engine.evaluate(rule_env({**DEFAULTS, **FIRING["R-02"]}, {})).hard_decline
    assert engine.evaluate(rule_env({**DEFAULTS, **FIRING["R-02"]}, {})).floor == pytest.approx(0.85)


def test_session_signals_can_only_bump(engine):
    res = engine.evaluate(rule_env({**DEFAULTS, **FIRING["R-11"]}, {}))
    assert res.floor == 0.0 or "R-11" not in [e["id"] for e in res.effects if "floor" in e]


def test_rule_expressions_are_sandboxed():
    for bad in ("__import__('os').system('x')", "open('x')", "f.__class__", "(lambda: 1)()"):
        with pytest.raises(ValueError):
            _compile(bad)


# ------------------------------------------------------------------ reputation
def test_decay_halves_in_one_half_life():
    assert rep.decay(8.0, 0.0, rep.HALF_LIFE_S) == pytest.approx(4.0)
    assert rep.decay(0.0, 0.0, 10.0) == 0.0


def test_new_upi_id_is_neutral_not_alarming():
    now = time.time()
    d = rep.derive(recv=0, bad=0, counters_at=now, lifetime_recv=0, confirmed_90d=0, first_seen=now - 2 * DAY, now=now)
    assert d["band"] == "NO_HISTORY" and d["trust_score"] == 45 and d["p_bad"] == pytest.approx(0.01)


def test_bands():
    now = time.time()
    old = now - 400 * DAY
    base = dict(counters_at=now, first_seen=old, now=now)
    assert rep.derive(recv=200, bad=0, lifetime_recv=500, confirmed_90d=0, **base)["band"] == "TRUSTED"
    assert rep.derive(recv=8, bad=0, lifetime_recv=20, confirmed_90d=0, **base)["band"] == "NEUTRAL"
    assert rep.derive(recv=8, bad=1.0, lifetime_recv=20, confirmed_90d=0, **base)["band"] == "CAUTION"
    assert rep.derive(recv=8, bad=5.0, lifetime_recv=20, confirmed_90d=1, **base)["band"] == "REPORTED"
    # a high rate alone, without confirmed fraud, is held at Caution
    assert rep.derive(recv=2, bad=9.0, lifetime_recv=5, confirmed_90d=0, **base)["band"] == "CAUTION"


def test_burst_is_for_personal_accounts_only():
    now = time.time()
    kw = dict(recv=20, bad=0, counters_at=now, lifetime_recv=20, confirmed_90d=0, first_seen=now - 2 * DAY, now=now,
              fan_in_1h=15, first_time_ratio=1.0)
    assert rep.derive(individual=True, **kw)["band"] == "CAUTION"
    assert rep.derive(individual=False, **kw)["band"] == "NO_HISTORY"


def test_dormant_account_rate_does_not_drift():
    now = time.time()
    a = rep.derive(recv=30, bad=1, counters_at=now, lifetime_recv=30, confirmed_90d=0, first_seen=now - 400 * DAY, now=now)
    b = rep.derive(recv=30, bad=1, counters_at=now - 60 * DAY, lifetime_recv=30, confirmed_90d=0, first_seen=now - 400 * DAY, now=now)
    assert b["p_bad"] <= a["p_bad"] + 1e-9          # going quiet never raises the bad-event rate


def test_near_miss_weights():
    assert rep.near_miss_weight("ABORTED") == 0.3 and rep.near_miss_weight("STEPUP_FAILED") == 0.6
    assert rep.near_miss_weight("GUARDIAN_DENIED") == 0.6 and rep.near_miss_weight("PROCEEDED") == 0.0


# ------------------------------------------------------------------ text helpers
def test_entity_extraction():
    e = extract_entities("Pay Rs 4,999 to refund.desk7@bankb or mail a@b.com, call +91 9876543210, see bit.ly/abc12")
    assert e["vpas"] == ["refund.desk7@bankb"]           # the email address is not a UPI ID
    assert e["phones"] == ["9876543210"] and e["amounts"] == [4999.0] and any("bit.ly" in u for u in e["urls"])


def test_red_flags_do_not_overlap():
    spans = red_flags("URGENT: your account will be blocked today. Share OTP immediately at bit.ly/x1")
    assert spans and all(spans[i]["end"] <= spans[i + 1]["start"] for i in range(len(spans) - 1))
    assert {"threat", "secrets"} & {s["kind"] for s in spans}


def test_name_match_and_institutional():
    assert name_match("Rakesh Kumar", "Mr. Rakesh Kumar") == 1.0
    assert name_match("SBI Refund Desk", "Rakesh Kumar") < 0.5
    assert name_match("FreshMart", "FreshMart Groceries Pvt Ltd") > 0.8
    assert is_institutional("SBI Refund Desk") and is_institutional("Cyber Cell Verification")
    assert not is_institutional("Rakesh Kumar") and not is_institutional("Blue Lotus Boutique")


def test_verdict_bands():
    assert verdict_for(0.7) == "SCAM" and verdict_for(0.69) == "CAREFUL" and verdict_for(0.4) == "CAREFUL"
    assert verdict_for(0.39) == "FINE"


# ------------------------------------------------------------------ graph features
def test_downstream_follows_forwarded_money_not_victims():
    t = 1_000_000.0
    edges = [(t, "victim", "mule", 10000.0), (t + 600, "mule", "layer2", 9800.0), (t + 5000, "bystander", "shop", 300.0)]
    hops = downstream_hops(edges, {"layer2"})
    assert hops.get("mule") == 1
    assert "victim" not in hops                 # the victim paid a mule but forwarded nothing
    feats = compute_graph_features(edges, {"mule": {"fp1"}, "layer2": {"fp1"}}, {"layer2"}, set(), t + 6000)
    assert feats["mule"]["shares_device_with_reported"] == 1 and feats["mule"]["downstream_hop"] == 1
    assert passthrough_ratios(edges, t + 6000)["mule"] == pytest.approx(0.98)


# ------------------------------------------------------------------ explanations
def test_every_reason_has_a_group_and_templates():
    from app import i18n

    assert len(explain.REASON_META) == 20
    for loc in ("en", "hi"):
        cat = i18n.catalog(loc)["reasons"]
        assert set(explain.REASON_META) <= set(cat), loc


def test_reasons_are_only_given_when_true():
    f = {**DEFAULTS, "is_new_payee": 1, "fan_in_1h": 2}
    got = explain.select_reasons(f, {"vpa_exists": True}, ["PAYEE_FAN_IN"], {"payee": 5.0})
    assert "PAYEE_FAN_IN" not in [r["code"] for r in got]      # only 2 payers: the sentence would be false
    assert [r["code"] for r in got][0] == "NEW_PAYEE"


def test_non_safe_decision_always_has_a_reason():
    f = {**DEFAULTS, "is_new_payee": 0}
    assert explain.select_reasons(f, {}, [], {}) != []


def test_attribution_threshold_and_prototypes():
    protos = explain.default_prototypes()
    assert set(protos) == {"DIGITAL_ARREST", "FAKE_REFUND", "MULE_TRANSFER", "IMPERSONATION", "KYC_BILL_THREAT", "INVESTMENT_TASK"}
    assert all(len(v) == len(explain.DIMS) == 24 for v in protos.values())
    fam, sim = explain.attribute(np.asarray(protos["DIGITAL_ARREST"]), protos)
    assert fam == "DIGITAL_ARREST" and sim == pytest.approx(1.0)
    assert explain.attribute(np.zeros(24), protos)[0] == "UNCLASSIFIED"


def test_story_dimensions_can_outweigh_the_shared_ones():
    """What the note and the message say decides between families that share everything else."""
    protos = explain.default_prototypes()
    z = np.zeros(24)
    for d in ("msg_flag", "new_payee", "payee_young", "coaching", "name_mismatch", "amount_anomaly", "note_invest"):
        z[explain.DIMS.index(d)] = 1.0
    plain, _ = explain.attribute(z, protos, explain.dim_weights(1.0))
    weighted, _ = explain.attribute(z, protos, explain.dim_weights(3.0))
    assert weighted == "INVESTMENT_TASK"
    assert explain.dim_weights(2.0)[explain.DIMS.index("note_kyc")] == 2.0
    assert explain.dim_weights(2.0)[explain.DIMS.index("new_payee")] == 1.0
    assert plain in protos or plain == "UNCLASSIFIED"


def test_a_family_is_named_only_on_its_own_evidence():
    """A first payment of an unusual amount, with nothing else, must not be called any particular scam."""
    protos = explain.default_prototypes()
    D = {d: i for i, d in enumerate(explain.DIMS)}
    bare = np.zeros(24)
    bare[D["new_payee"]] = bare[D["amount_anomaly"]] = 1.0
    z = np.asarray(protos["MULE_TRANSFER"], dtype=float)           # as close to the mule prototype as it gets
    assert explain.attribute(z, protos)[0] == "MULE_TRANSFER"       # similarity alone would name it
    assert explain.attribute(z, protos, None, bare)[0] == "UNCLASSIFIED"
    with_report = bare.copy()
    with_report[D["payee_reports"]] = 1.0
    assert explain.attribute(z, protos, None, with_report)[0] == "MULE_TRANSFER"
    # digital arrest needs the payer to be held on a call, a flagged caller, or an authority story
    short_call = bare.copy()
    short_call[D["call_active"]], short_call[D["caller_unknown"]] = 0.42, 1.0
    assert not explain.anchored("DIGITAL_ARREST", short_call)
    long_call = short_call.copy()
    long_call[D["call_active"]] = 0.9
    assert explain.anchored("DIGITAL_ARREST", long_call)
    flagged = bare.copy()
    flagged[D["call_recent"]], flagged[D["caller_risk"]] = 1.0, 1.0
    assert explain.anchored("DIGITAL_ARREST", flagged)
    for fam, dim in (("FAKE_REFUND", "note_refund"), ("IMPERSONATION", "note_impersonation"),
                     ("KYC_BILL_THREAT", "note_kyc"), ("INVESTMENT_TASK", "note_invest")):
        assert not explain.anchored(fam, bare)
        story = bare.copy()
        story[D[dim]] = 0.8
        assert explain.anchored(fam, story)
    # membership of a risky community alone is not evidence against the payee
    community = bare.copy()
    community[D["payee_network"]] = 0.4
    assert not explain.anchored("MULE_TRANSFER", community)


def test_family_text_only_claims_what_was_seen():
    from app import i18n

    assert explain.family_variant("MULE_TRANSFER", {"fan_in_1h": 0}) == "network"
    assert explain.family_variant("MULE_TRANSFER", {"fan_in_1h": 12}) is None
    assert explain.family_variant("IMPERSONATION", {"is_new_payee": 0}) == "known"
    assert explain.family_variant("DIGITAL_ARREST", {"fan_in_1h": 0}) is None
    for loc in ("en", "hi"):
        usual, alt = i18n.family(loc, "MULE_TRANSFER"), i18n.family(loc, "MULE_TRANSFER", "network")
        assert alt["headline"] != usual["headline"] and alt["body"] != usual["body"]
        assert alt["question"] == usual["question"] and alt["risky_answer"] == usual["risky_answer"]
        assert i18n.family(loc, "IMPERSONATION", "known")["body"] != i18n.family(loc, "IMPERSONATION")["body"]
        assert i18n.family(loc, "KYC_BILL_THREAT", "network") == i18n.family(loc, "KYC_BILL_THREAT")   # unknown variant: usual text


# ------------------------------------------------------------------ fast single-payment scoring
def test_flat_forest_matches_scikit_learn():
    """The single-row path used when serving must give the numbers scikit-learn gives."""
    from sklearn.ensemble import IsolationForest, RandomForestClassifier
    from sklearn.ensemble._iforest import _average_path_length

    from app.core.scoring import FlatForest

    rng = np.random.default_rng(3)
    X = rng.normal(size=(600, 9)).astype(np.float32)
    y = (X[:, 0] + X[:, 3] * X[:, 5] + rng.normal(scale=0.4, size=600) > 0.4).astype(int)
    rf = RandomForestClassifier(n_estimators=25, max_depth=7, random_state=1, class_weight="balanced_subsample").fit(X, y)
    trees = [e.tree_ for e in rf.estimators_]
    flat = FlatForest(trees, [t.value[:, 0, 1] / t.value[:, 0, :].sum(axis=1) for t in trees])
    probe = rng.normal(size=(40, 9)).astype(np.float32)
    assert np.allclose([flat.leaf_values(r).mean() for r in probe], rf.predict_proba(probe)[:, 1], atol=1e-12)

    cols = np.array([0, 2, 3, 5, 8])
    iso = IsolationForest(n_estimators=30, random_state=1).fit(X[:, cols])
    trees = [e.tree_ for e in iso.estimators_]
    flat = FlatForest(trees, [t.compute_node_depths() + _average_path_length(t.n_node_samples) - 1.0 for t in trees],
                      [cols[np.asarray(f)] for f in iso.estimators_features_])
    denom = len(trees) * float(_average_path_length([iso._max_samples])[0])
    got = [2.0 ** (-flat.leaf_values(r).sum() / denom) for r in probe]
    assert np.allclose(got, -iso.score_samples(probe[:, cols]), atol=1e-12)


def test_active_model_scores_one_payment_like_a_batch():
    from app.config import ROOT
    from app.core.scoring import ModelBundle, active_model_dir

    d = active_model_dir(ROOT / "ml" / "artifacts")
    if d is None:
        pytest.skip("no trained model yet")
    b = ModelBundle.load(d)
    assert b.fast_paths == {"rf": True, "iforest": True}
    rng = np.random.default_rng(5)
    X = np.abs(rng.normal(0, 2, size=(60, len(COLUMNS)))).astype(np.float32)
    batch = b.parts(X, with_contribs=False)
    _, want = b.combine(batch, np.zeros(len(X)))
    for i in range(len(X)):
        one = b.score_one(X[i], 0.0)
        assert one["score"] == pytest.approx(float(want[i]), abs=1e-9)
        assert one["p_xgb"] == pytest.approx(float(batch["p_xgb"][i]), abs=1e-9)
        assert "contribs" not in one                      # explanations are computed only when needed
    assert b.contribs_one(X[0]).shape == (len(COLUMNS),)


# ------------------------------------------------------------------ policy simulation
def test_policy_metrics_match_a_hand_calculation():
    score = np.array([0.1, 0.35, 0.7, 0.9, 0.2, 0.65, 0.9, 0.05])
    y = np.array([0, 0, 0, 0, 1, 1, 1, 0])
    amount = np.array([100, 100, 100, 100, 1000, 2000, 3000, 100.0])
    m = policy_metrics(score, y, amount, 0.30, 0.60, 0.85, 0.01)
    assert m["scams_caught"] == pytest.approx(2 / 3) and m["scams_missed"] == pytest.approx(1 / 3)
    assert m["legit_warning"] == pytest.approx(1 / 5) and m["legit_stepup"] == pytest.approx(1 / 5)
    assert m["legit_block"] == pytest.approx(1 / 5) and m["rupees_protected"] == 5000
    tpr, fpr, pi = 2 / 3, 2 / 5, 0.01
    assert m["precision_stepup_plus"] == pytest.approx(tpr * pi / (tpr * pi + fpr * (1 - pi)))


# ------------------------------------------------------------------ the feature store, on a simulated clock
def test_store_round_trip_and_windows():
    async def run():
        store = FeatureStore(fakeredis.FakeAsyncRedis(decode_responses=True))
        now = 2_000_000_000.0
        await store.put_user_profile("u1", first_seen=now - 500 * DAY, phone_hash="ph", tenant="bank_a", balance=10000)
        await store.put_vpa_dir("p@bankb", registered_name="Payee One", kind="person", verified=False, on_denylist=False,
                                first_seen=now - 3 * DAY, tenant="bank_b")
        for i in range(6):
            await store.payment_success(tenant="bank_a", user_id="u1", payee=f"shop{i}@banka", amount=500 + i, ts=now - (i + 2) * DAY,
                                        device_id="d1", debit=False)
        await store.call_event(phone_hash="ph", call_id="c1", event="started", caller_known=False, caller_risk=3, ts=now - 600)
        req = AssessInput(txn_id="T1", tenant="bank_a", user_id="u1", payer_vpa="u1@banka", payee_vpa="p@bankb", amount=9000,
                          device_id="d1", session={"vpa_entry_method": "pasted", "app_switch_count": 99})
        st = await store.read(tenant="bank_a", user_id="u1", phone_hash="ph", payee="p@bankb", device_id="d1", txn_id="T1", now=now)
        f, aux = compute_features(req, st, now)
        assert f["is_new_payee"] == 1 and f["call_active"] == 1 and f["caller_risk"] == 3 and f["caller_unknown"] == 1
        assert f["app_switch_count"] == 20                      # client values are clamped
        assert f["vpa_age_days"] == pytest.approx(3.0) and f["balance_fraction"] == pytest.approx(0.9)
        assert f["amount_over_p95"] > 5 and aux["band"] == "NO_HISTORY"
        # the call ends; 31 minutes later it no longer counts
        await store.call_event(phone_hash="ph", call_id="c1", event="ended", caller_known=False, caller_risk=3, ts=now)
        st = await store.read(tenant="bank_a", user_id="u1", phone_hash="ph", payee="p@bankb", device_id="d1", txn_id="T2", now=now + 31 * 60)
        f2, _ = compute_features(req, st, now + 31 * 60)
        assert f2["call_active"] == 0 and f2["caller_risk"] == 0
        # a completed payment makes the payee known, and a confirmed report makes it Reported
        await store.payment_success(tenant="bank_a", user_id="u1", payee="p@bankb", amount=9000, ts=now, device_id="d1", debit=False)
        await store.bad_event(tenant="bank_a", vpa="p@bankb", weight=5.0, kind="confirmed", ts=now + 10, ref="r1")
        snap = await store.rep_snapshot("bank_a", "p@bankb", now + 20)
        assert snap["band"] == "REPORTED" and snap["lifetime_recv"] == 1
        other = await store.rep_snapshot("bank_b", "p@bankb", now + 20)
        assert other["band"] == "NO_HISTORY"                    # reputation is kept per observing bank

    asyncio.run(run())
