"""Train, calibrate, evaluate and register the scoring ensemble (F-03, F-12). Docs/06, sections 3-5 and 15-16."""
from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

from app.config import ROOT
from app.core import explain
from app.core.features import COLUMNS, NUMERIC
from app.core.rules import RuleEngine
from app.core.scoring import ModelBundle, _logit, active_model_dir

from .offline import DEFAULT_POLICY, frame_to_matrix, row_features, run_rules, score_frame

DAY = 86400.0
ARTIFACTS = ROOT / "ml" / "artifacts"
IF_FEATURES = ["log_amount", "amount_z", "amount_over_p95", "txn_count_1h", "txn_count_24h", "new_payees_24h",
               "new_payees_10m", "balance_fraction", "is_new_payee", "prior_txn_count", "device_is_new",
               "app_switch_count", "time_on_pay_screen_s", "amount_edit_count", "hour_of_day"]
BUDGET = {"legit_stepup_plus": 0.01, "legit_block": 0.001, "legit_warning": 0.05}
REAL_FRAUD_RATE = 0.0005


def _tier_stats(score: np.ndarray, y: np.ndarray, p: dict = DEFAULT_POLICY) -> dict:
    legit, fraud = score[y == 0], score[y == 1]
    tw, ts, tb = p["t_warning"], p["t_stepup"], p["t_block"]
    tpr = float((fraud >= ts).mean()) if len(fraud) else 0.0
    fpr = float((legit >= ts).mean()) if len(legit) else 0.0
    pi = REAL_FRAUD_RATE
    return {
        "recall_stepup_plus": tpr, "recall_block": float((fraud >= tb).mean()) if len(fraud) else 0.0,
        "recall_warning_plus": float((fraud >= tw).mean()) if len(fraud) else 0.0,
        "legit_warning": float(((legit >= tw) & (legit < ts)).mean()),
        "legit_stepup": float(((legit >= ts) & (legit < tb)).mean()),
        "legit_block": float((legit >= tb).mean()), "legit_stepup_plus": fpr,
        "precision_at_real_rate": (tpr * pi) / max(1e-12, tpr * pi + fpr * (1 - pi)),
    }


def _psi_baseline(X: np.ndarray, score: np.ndarray, top: list[str]) -> dict:
    out = {}
    for name, values in [("__score__", score)] + [(c, X[:, COLUMNS.index(c)]) for c in top]:
        edges = np.unique(np.quantile(values, np.linspace(0, 1, 11)))
        if len(edges) < 3:
            edges = np.array([values.min() - 1e-9, values.mean(), values.max() + 1e-9])
        hist, _ = np.histogram(values, bins=edges)
        out[name] = {"edges": edges.tolist(), "expected": (hist / max(1, hist.sum())).tolist()}
    return out


def train(parquet: Path | None = None, promote: bool = True, verbose: bool = True) -> dict:
    t0 = time.time()
    parquet = parquet or (ARTIFACTS / "train_rows.parquet")
    df = pd.read_parquet(parquet).sort_values("ts").reset_index(drop=True)
    end = float(df["ts"].max())
    age_days = (end - df["ts"].to_numpy()) / DAY
    y = df["is_fraud"].to_numpy()
    X = frame_to_matrix(df)
    engine = RuleEngine()
    feats, auxes = row_features(df)
    rrs = run_rules(feats, auxes, engine)
    s_rule = np.array([r.s_rule for r in rrs])
    floors = np.array([r.floor for r in rrs])
    caps = np.array([r.cap for r in rrs])

    tr = age_days > 30
    va = (age_days <= 30) & (age_days > 22.5)
    vb = (age_days <= 22.5) & (age_days > 15)
    te = age_days <= 15
    val = va | vb
    if verbose:
        print(f"rows={len(df):,} fraud={int(y.sum()):,} ({y.mean():.2%})  train={int(tr.sum()):,} "
              f"val={int(val.sum()):,} test={int(te.sum()):,}  test fraud={int(y[te].sum())}")

    if y[tr].sum() < 50 or y[te].sum() < 20:
        raise SystemExit("not enough fraud rows in the training or test window; simulate at least 90 days")

    # --- supervised: XGBoost (champion) ---------------------------------------------
    pos, neg = max(1, int(y[tr].sum())), int((y[tr] == 0).sum())
    params = {"objective": "binary:logistic", "eval_metric": "aucpr", "max_depth": 5, "eta": 0.05,
              "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 2, "tree_method": "hist",
              "scale_pos_weight": min(25.0, neg / pos), "seed": 7, "nthread": 4}
    dtr = xgb.DMatrix(X[tr], label=y[tr], feature_names=COLUMNS)
    dva = xgb.DMatrix(X[val], label=y[val], feature_names=COLUMNS)
    booster = xgb.train(params, dtr, 600, evals=[(dva, "val")], early_stopping_rounds=40, verbose_eval=False)
    booster = booster[: booster.best_iteration + 1]

    # --- supervised: Random Forest (second opinion) ----------------------------------
    rf = RandomForestClassifier(n_estimators=120, max_depth=10, min_samples_leaf=3, n_jobs=4, random_state=7,
                                class_weight="balanced_subsample")
    rf.fit(X[tr], y[tr])

    # --- unsupervised: Isolation Forest on legitimate behaviour -----------------------
    if_cols = np.array([COLUMNS.index(c) for c in IF_FEATURES])
    legit_tr = np.where(tr & (y == 0))[0]
    rs = np.random.default_rng(7)
    sample = rs.choice(legit_tr, size=min(40000, len(legit_tr)), replace=False)
    iforest = IsolationForest(n_estimators=100, random_state=7, n_jobs=4).fit(X[sample][:, if_cols])
    if_sorted = np.sort(-iforest.score_samples(X[sample][:, if_cols]))

    bundle = ModelBundle()
    bundle.booster, bundle.rf, bundle.iforest = booster, rf, iforest
    bundle.rf.n_jobs = 1
    bundle.if_cols, bundle.if_sorted = if_cols, if_sorted
    parts = bundle.parts(X)
    s_ml = 0.7 * parts["p_xgb"] + 0.3 * parts["p_rf"]

    # --- stacker and isotonic calibration, cross-fitted on the two validation folds ---------
    # Each fold's stacker scores the other fold, so the isotonic map is fitted on out-of-fold outputs
    # from all validation rows. Fitting it on one small fold gave a step function with plateaus.
    Z = np.column_stack([_logit(s_ml), parts["s_anom"], s_rule])
    if y[va].sum() >= 20 and y[vb].sum() >= 20:
        oof = np.zeros(len(y))
        for fit_on, score_on in ((va, vb), (vb, va)):
            lr_f = LogisticRegression(C=0.5, max_iter=500).fit(Z[fit_on], y[fit_on])
            oof[score_on] = lr_f.predict_proba(Z[score_on])[:, 1]
        lr = LogisticRegression(C=0.5, max_iter=500).fit(Z[val], y[val])
        bundle.stacker = {"b0": float(lr.intercept_[0]), "b1": float(lr.coef_[0][0]), "b2": float(lr.coef_[0][1]),
                          "b3": float(lr.coef_[0][2])}
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(oof[val], y[val])
        bundle.iso_x, bundle.iso_y = iso.X_thresholds_, iso.y_thresholds_
    raw, cal = bundle.combine(parts, s_rule)
    score = np.maximum(floors, np.minimum(caps, cal))

    # --- learned prototypes: centroid of each family's signal vectors -----------------------
    contribs = parts["contribs"]
    fam = df["family"].to_numpy()

    def centroids(idx: np.ndarray, w_ind: float) -> dict:
        protos_ = explain.default_prototypes()
        vecs: dict[str, list] = {}
        for i in idx:
            vecs.setdefault(fam[i], []).append(explain.signal_vector(feats[i], auxes[i], contribs[i], w_ind))
        for f_code, vs in vecs.items():
            if f_code in protos_ and len(vs) >= 30:
                protos_[f_code] = np.mean(vs, axis=0).round(4).tolist()
        return protos_

    # Two settings are chosen before the test window is looked at: how much to trust the indicators against
    # the model's contributions, and how much weight the "story" dimensions get in the comparison.
    # They are picked by cross-validation over whole campaigns, so a campaign never helps to attribute itself.
    dev_scam = np.where((tr | val) & (y == 1))[0]
    camp = df["campaign"].fillna("").to_numpy()
    groups = sorted(set(camp[dev_scam]))
    fold_of = {c: k % 5 for k, c in enumerate(groups)}
    fold = np.array([fold_of[camp[i]] for i in dev_scam])
    best_w, best_story, best_acc, tried = 0.5, 1.0, -1.0, {}
    for w_ind in (0.5, 0.7, 0.85, 1.0):
        vecs_w = {int(i): explain.signal_vector(feats[i], auxes[i], contribs[i], w_ind) for i in dev_scam}
        inds = {int(i): explain.indicators(feats[i], auxes[i]) for i in dev_scam}
        for story in (1.0, 1.5, 2.0, 3.0):
            wts, hits = explain.dim_weights(story), 0
            for k in range(5):
                fit, held = dev_scam[fold != k], dev_scam[fold == k]
                pw = explain.default_prototypes()
                by_f: dict[str, list] = {}
                for i in fit:
                    by_f.setdefault(fam[i], []).append(vecs_w[int(i)])
                for f_code, vs in by_f.items():
                    if f_code in pw and len(vs) >= 30:
                        pw[f_code] = np.mean(vs, axis=0)
                hits += sum(explain.attribute(vecs_w[int(i)], pw, wts, inds[int(i)])[0] == fam[i] for i in held)
            acc = hits / max(1, len(dev_scam))
            tried[f"{w_ind}/{story}"] = round(acc, 4)
            if acc > best_acc + 1e-9:
                best_w, best_story, best_acc = w_ind, story, acc
    protos = centroids(dev_scam, best_w)
    bundle.prototypes = protos
    bundle.attr_weight = best_w
    bundle.attr_story = best_story
    attr_w = explain.dim_weights(best_story)

    # --- evaluation on the held-out last 15 days ------------------------------------------
    m: dict = {"rows": int(len(df)), "fraud_rows": int(y.sum()), "test_rows": int(te.sum()),
               "test_fraud": int(y[te].sum()), "trees": int(booster.num_boosted_rounds())}
    # Ranking quality is measured on the stacked model output. The final score has ties by design
    # (rule floors, and the 0.94 cap on a model-only score), which is right for policy but not for ranking.
    m["pr_auc"] = float(average_precision_score(y[te], raw[te]))
    m["pr_auc_final_score"] = float(average_precision_score(y[te], score[te]))
    m["pr_auc_xgb_only"] = float(average_precision_score(y[te], parts["p_xgb"][te]))
    m["pr_auc_rules_only"] = float(average_precision_score(y[te], np.maximum(floors, np.minimum(caps, 0.05 + s_rule))[te]))
    m.update(_tier_stats(score[te], y[te]))
    # A first payment to an account already confirmed as fraud, here or at another bank, is meant to be
    # stopped even when the payer is honest. The budgets are judged without those.
    confirmed_payee = (df["confirmed_90d"].to_numpy() >= 1) | np.array([bool(a.get("consortium_confirmed")) for a in auxes])
    ok = te & ~confirmed_payee
    ex = _tier_stats(score[ok], y[ok])
    m["legit_to_confirmed_fraud_payees"] = float((te & confirmed_payee & (y == 0)).sum() / max(1, (te & (y == 0)).sum()))
    for k in ("legit_warning", "legit_stepup", "legit_block", "legit_stepup_plus"):
        m[k + "_excl_confirmed"] = ex[k]
    variant = df["variant"].to_numpy()
    unseen = te & (y == 1) & (variant == "unseen")
    base = te & (y == 1) & (variant == "base")
    m["unseen_fraud"] = int(unseen.sum())
    m["recall_unseen"] = float((score[unseen] >= 0.6).mean()) if unseen.any() else None
    m["recall_seen"] = float((score[base] >= 0.6).mean()) if base.any() else None
    weak = te & (y == 1) & (variant == "weak")
    m["weak_fraud"] = int(weak.sum())
    m["recall_weak"] = float((score[weak] >= 0.6).mean()) if weak.any() else None
    m["recall_by_family"] = {f_code: float((score[te & (y == 1) & (fam == f_code)] >= 0.6).mean())
                             for f_code in sorted(set(fam[te & (y == 1)]))}
    hit = total = named = 0
    by_variant: dict = {}
    confusion: dict = {}
    for i in np.where(te & (y == 1) & (score >= 0.3))[0]:
        z_i, ind_i = explain.signal_parts(feats[i], auxes[i], contribs[i], best_w)
        pred, _sim = explain.attribute(z_i, protos, attr_w, ind_i)
        total += 1
        hit += int(pred == fam[i])
        named += int(pred != "UNCLASSIFIED")
        bv = by_variant.setdefault(variant[i], [0, 0])
        bv[0] += int(pred == fam[i])
        bv[1] += 1
        confusion.setdefault(fam[i], {}).setdefault(pred, 0)
        confusion[fam[i]][pred] += 1
    m["attribution_accuracy"] = hit / max(1, total)
    # A family is named only when its defining evidence is present, so some scams are left unnamed on purpose.
    # What matters most is how often a named family is the right one.
    m["attribution_named_share"] = named / max(1, total)
    m["attribution_precision_when_named"] = hit / max(1, named)
    # and, for honest payments that were flagged, how often a scam family was named at all
    legit_flagged = np.where(te & (y == 0) & (score >= 0.3))[0]
    legit_named = 0
    for i in legit_flagged:
        z_i, ind_i = explain.signal_parts(feats[i], auxes[i], contribs[i], best_w)
        legit_named += int(explain.attribute(z_i, protos, attr_w, ind_i)[0] != "UNCLASSIFIED")
    m["legit_flagged"] = int(len(legit_flagged))
    m["legit_flagged_named_family_share"] = legit_named / max(1, len(legit_flagged))
    m["legit_named_family_per_10k"] = 10000.0 * legit_named / max(1, int((te & (y == 0)).sum()))
    # "base" scenarios carry their family's signals; "weak" and "unseen" ones have had them stripped,
    # so the family often cannot be told and they are reported separately.
    m["attribution_accuracy_by_variant"] = {k: round(v[0] / max(1, v[1]), 4) for k, v in by_variant.items()}
    m["attribution_accuracy_seen"] = by_variant.get("base", [0, 0])[0] / max(1, by_variant.get("base", [0, 1])[1])
    m["attribution_weight"] = best_w
    m["attribution_story_weight"] = best_story
    m["attribution_cv_accuracy"] = round(best_acc, 4)
    m["attribution_cv_grid"] = tried
    m["attribution_confusion"] = confusion
    m["stacker"] = bundle.stacker

    # single-payment scoring latency
    lat = []
    for i in rs.choice(np.where(te)[0], size=200, replace=False):
        t = time.perf_counter()
        bundle.score_one(X[i], float(s_rule[i]))
        lat.append((time.perf_counter() - t) * 1000)
    m["score_latency_ms_p50"] = float(np.percentile(lat, 50))
    m["score_latency_ms_p95"] = float(np.percentile(lat, 95))

    imp = booster.get_score(importance_type="gain")
    top = [c for c, _ in sorted(imp.items(), key=lambda kv: -kv[1]) if c in NUMERIC][:15]
    m["top_features"] = top
    baseline = _psi_baseline(X[tr | val], score[tr | val], top)

    # --- gates (docs/06, section 16) --------------------------------------------------------
    gates = {"false_positive_budgets": bool(m["legit_stepup_plus_excl_confirmed"] <= BUDGET["legit_stepup_plus"]
                                            and m["legit_block_excl_confirmed"] <= BUDGET["legit_block"]
                                            and m["legit_warning_excl_confirmed"] <= BUDGET["legit_warning"]),
             "latency": bool(m["score_latency_ms_p95"] <= 40.0)}
    prev_dir = active_model_dir(ARTIFACTS)
    if prev_dir is not None:
        try:
            prev = ModelBundle.load(prev_dir)
            prev_raw = score_frame(df[te], prev, engine)["raw"]
            m["previous_version"] = prev.version
            m["previous_pr_auc"] = float(average_precision_score(y[te], prev_raw))
            gates["pr_auc_not_lower"] = bool(m["pr_auc"] >= m["previous_pr_auc"] - 0.005)
        except Exception as exc:  # a model from an older feature schema cannot be compared
            m["previous_error"] = str(exc)
            gates["pr_auc_not_lower"] = True
    else:
        gates["pr_auc_not_lower"] = True
    m["gates"] = gates
    m["seconds"] = round(time.time() - t0, 1)

    # --- save ----------------------------------------------------------------------------
    version = time.strftime("ens-%Y.%m.%d-") + str(int(time.time()) % 100000)
    out = ARTIFACTS / version
    out.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out / "xgb.json"))
    joblib.dump(rf, out / "rf.joblib", compress=3)
    joblib.dump({"model": iforest, "cols": if_cols.tolist(), "sorted": if_sorted}, out / "iforest.joblib", compress=3)
    meta = {"version": version, "columns": COLUMNS, "stacker": bundle.stacker,
            "isotonic": ({"x": bundle.iso_x.tolist(), "y": bundle.iso_y.tolist()} if bundle.iso_x is not None else None),
            "prototypes": protos, "attr_weight": best_w, "attr_story": best_story, "baseline": baseline, "metrics": m,
            "rules_version": engine.version,
            "trained_at": time.time()}
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    promoted = bool(promote and all(gates.values()))
    if promoted:
        (ARTIFACTS / "ACTIVE").write_text(version, encoding="utf-8")
    m["version"], m["promoted"] = version, promoted
    if verbose:
        show = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()
                if k not in ("attribution_confusion", "top_features")}
        print(json.dumps(show, indent=1))
        print("attribution confusion:", json.dumps(confusion))
    return m


if __name__ == "__main__":
    train()
