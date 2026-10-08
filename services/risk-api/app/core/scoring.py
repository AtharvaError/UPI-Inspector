"""Model bundle: load versioned artefacts and score (F-03). Docs/06, sections 3 to 5.

The same class scores one payment in the hot path and whole frames offline, so training,
evaluation, backfill and serving cannot drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from .features import COLUMNS

W_XGB, W_RF = 0.7, 0.3
# The calibrated model score is capped just below the decline threshold. A statistical model on its
# own can pause a payment (Hold) but never hard-stop it; only deterministic evidence (rule floors)
# can lift a score to 0.95 or above.
MODEL_CAP = 0.94


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


class FlatForest:
    """A fitted scikit-learn forest as flat arrays.

    scikit-learn spends several milliseconds per call on input checks and job dispatch, which is most of
    the cost when scoring one payment. Walking the same trees with a handful of array operations gives the
    same numbers about thirty times faster. `ModelBundle.load` checks the two paths agree before using this.
    """

    def __init__(self, trees: list, values: list[np.ndarray], features: list[np.ndarray] | None = None) -> None:
        n, m = len(trees), max(t.node_count for t in trees)
        self.n = n
        self.left = np.full((n, m), -1, dtype=np.int64)
        self.right = np.full((n, m), -1, dtype=np.int64)
        self.feat = np.zeros((n, m), dtype=np.int64)
        self.thr = np.zeros((n, m), dtype=np.float64)
        self.val = np.zeros((n, m), dtype=np.float64)
        for i, (t, v) in enumerate(zip(trees, values)):
            k = t.node_count
            inner = t.children_left >= 0
            cols = np.where(inner, t.feature, 0)
            self.left[i, :k], self.right[i, :k] = t.children_left, t.children_right
            self.feat[i, :k] = features[i][cols] if features is not None else cols
            self.thr[i, :k] = t.threshold
            self.val[i, :k] = v
        self.rows = np.arange(n)
        self.depth = int(max(t.max_depth for t in trees)) + 1

    def leaf_values(self, x: np.ndarray) -> np.ndarray:
        """The value at the leaf each tree sends this row to. `x` is one float32 row in COLUMNS order."""
        rows, idx = self.rows, np.zeros(self.n, dtype=np.int64)
        for _ in range(self.depth):
            left = self.left[rows, idx]
            inner = left >= 0
            if not inner.any():
                break
            go_left = x[self.feat[rows, idx]] <= self.thr[rows, idx]
            idx = np.where(inner, np.where(go_left, left, self.right[rows, idx]), idx)
        return self.val[rows, idx]


class ModelBundle:
    def __init__(self) -> None:
        self.version = "rules-only"
        self.booster: Any = None
        self.rf: Any = None
        self.iforest: Any = None
        self.if_cols: np.ndarray | None = None
        self.if_sorted: np.ndarray | None = None
        self.stacker: dict | None = None
        self.iso_x: np.ndarray | None = None
        self.iso_y: np.ndarray | None = None
        self.prototypes: dict[str, list[float]] = {}
        self.attr_weight = 0.5
        self.attr_story = 1.0
        self.baseline: dict = {}
        self.metrics: dict = {}
        self.columns = COLUMNS
        self._rf_flat: FlatForest | None = None
        self._if_flat: FlatForest | None = None
        self._if_denominator = 1.0

    @property
    def available(self) -> bool:
        return self.booster is not None

    @classmethod
    def load(cls, directory: Path) -> "ModelBundle":
        import xgboost as xgb

        b = cls()
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        if meta["columns"] != COLUMNS:
            raise ValueError("model was trained on a different feature schema")
        b.version = meta["version"]
        b.booster = xgb.Booster()
        b.booster.load_model(str(directory / "xgb.json"))
        if (directory / "rf.joblib").exists():
            b.rf = joblib.load(directory / "rf.joblib")
            b.rf.n_jobs = 1
        if (directory / "iforest.joblib").exists():
            blob = joblib.load(directory / "iforest.joblib")
            b.iforest, b.if_cols, b.if_sorted = blob["model"], np.asarray(blob["cols"]), np.asarray(blob["sorted"])
        b.stacker = meta.get("stacker")
        iso = meta.get("isotonic")
        if iso:
            b.iso_x, b.iso_y = np.asarray(iso["x"]), np.asarray(iso["y"])
        b.prototypes = meta.get("prototypes", {})
        b.attr_weight = float(meta.get("attr_weight", 0.5))
        b.attr_story = float(meta.get("attr_story", 1.0))
        b.baseline = meta.get("baseline", {})
        b.metrics = meta.get("metrics", {})
        b.build_fast_paths()
        return b

    def for_serving(self) -> "ModelBundle":
        """One payment at a time: a single thread is faster than waking a thread pool for one row."""
        if self.booster is not None:
            self.booster.set_param({"nthread": 1})
        return self

    def build_fast_paths(self) -> None:
        """Flatten the two scikit-learn forests for single-row scoring, and keep the flat version only if it
        reproduces scikit-learn's own output on a set of probe rows."""
        self._rf_flat = self._if_flat = None
        rng = np.random.default_rng(11)
        probe = rng.normal(0, 3, size=(48, len(COLUMNS))).astype(np.float32)
        probe[:, ::3] = np.abs(probe[:, ::3]) * 500
        try:
            if self.rf is not None:
                trees = [e.tree_ for e in self.rf.estimators_]
                vals = [t.value[:, 0, 1] / np.maximum(t.value[:, 0, :].sum(axis=1), 1e-12) for t in trees]
                flat = FlatForest(trees, vals)
                want = self.rf.predict_proba(probe)[:, 1]
                got = np.array([flat.leaf_values(r).mean() for r in probe])
                if np.allclose(want, got, atol=1e-9):
                    self._rf_flat = flat
        except Exception:
            self._rf_flat = None
        try:
            if self.iforest is not None:
                from sklearn.ensemble._iforest import _average_path_length

                trees = [e.tree_ for e in self.iforest.estimators_]
                vals = [t.compute_node_depths() + _average_path_length(t.n_node_samples) - 1.0 for t in trees]
                feats = [self.if_cols[np.asarray(f)] for f in self.iforest.estimators_features_]
                flat = FlatForest(trees, vals, feats)
                denom = len(trees) * float(_average_path_length([self.iforest._max_samples])[0])
                want = -self.iforest.score_samples(probe[:, self.if_cols])
                got = np.array([2.0 ** (-flat.leaf_values(r).sum() / denom) for r in probe])
                if denom > 0 and np.allclose(want, got, atol=1e-9):
                    self._if_flat, self._if_denominator = flat, denom
        except Exception:
            self._if_flat = None

    @property
    def fast_paths(self) -> dict:
        return {"rf": self._rf_flat is not None, "iforest": self._if_flat is not None}

    # ------------------------------------------------------------------ parts --
    def contributions(self, X: np.ndarray) -> np.ndarray:
        """Per-feature contributions to the XGBoost output, in log-odds (TreeSHAP), without the bias column."""
        import xgboost as xgb

        X = np.atleast_2d(np.asarray(X, dtype=np.float32))
        return self.booster.predict(xgb.DMatrix(X, feature_names=COLUMNS), pred_contribs=True)[:, :-1]

    def parts(self, X: np.ndarray, with_contribs: bool = True) -> dict:
        """Per-row model outputs for a 2-D float32 matrix in COLUMNS order (frames: training, backfill)."""
        X = np.atleast_2d(np.asarray(X, dtype=np.float32))
        p_xgb = _sigmoid(np.asarray(self.booster.inplace_predict(X, predict_type="margin"), dtype=np.float64))
        out = {"p_xgb": p_xgb}
        if with_contribs:
            out["contribs"] = self.contributions(X)
        if self.rf is not None:
            out["p_rf"] = self.rf.predict_proba(X)[:, 1]
        if self.iforest is not None:
            raw = -self.iforest.score_samples(X[:, self.if_cols])       # higher = more anomalous
            out["s_anom"] = np.searchsorted(self.if_sorted, raw, side="right") / len(self.if_sorted)
        return out

    def combine(self, parts: dict, s_rule: np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
        """Return (raw, calibrated score) before rule floors and caps."""
        s_rule = np.atleast_1d(np.asarray(s_rule, dtype=float))
        p_xgb = parts["p_xgb"]
        s_ml = W_XGB * p_xgb + W_RF * parts["p_rf"] if "p_rf" in parts else p_xgb
        if self.stacker and "s_anom" in parts:
            st = self.stacker
            raw = _sigmoid(st["b0"] + st["b1"] * _logit(s_ml) + st["b2"] * parts["s_anom"] + st["b3"] * s_rule)
        else:
            raw = np.clip(0.75 * s_ml + 0.25 * s_rule, 0, 1)            # fixed weights until the stacker exists
        score = np.interp(raw, self.iso_x, self.iso_y) if self.iso_x is not None else raw
        return raw, np.minimum(score, MODEL_CAP)

    def score_one(self, vec: np.ndarray, s_rule: float) -> dict:
        """Score one payment (the hot path). Contributions are not computed here: most payments are Safe
        and need no explanation; `contribs_one` is called only for the ones that are flagged."""
        x = np.ascontiguousarray(vec, dtype=np.float32)
        row = x[None, :]
        p: dict = {"p_xgb": _sigmoid(np.asarray(self.booster.inplace_predict(row, predict_type="margin"), dtype=np.float64))}
        if self.rf is not None:
            p["p_rf"] = (np.array([self._rf_flat.leaf_values(x).mean()]) if self._rf_flat is not None
                         else self.rf.predict_proba(row)[:, 1])
        if self.iforest is not None:
            anom = (np.array([2.0 ** (-self._if_flat.leaf_values(x).sum() / self._if_denominator)])
                    if self._if_flat is not None else -self.iforest.score_samples(row[:, self.if_cols]))
            p["s_anom"] = np.searchsorted(self.if_sorted, anom, side="right") / len(self.if_sorted)
        raw, score = self.combine(p, s_rule)
        return {
            "score": float(score[0]), "raw": float(raw[0]), "p_xgb": float(p["p_xgb"][0]),
            "p_rf": float(p["p_rf"][0]) if "p_rf" in p else None,
            "s_anom": float(p["s_anom"][0]) if "s_anom" in p else None, "s_rule": float(s_rule),
        }

    def contribs_one(self, vec: np.ndarray) -> np.ndarray:
        return self.contributions(np.asarray(vec, dtype=np.float32)[None, :])[0]


def rules_only_score(s_rule: float) -> float:
    """Used when no model is available: a low base plus the rule score. Floors and caps follow."""
    return float(min(MODEL_CAP, 0.05 + s_rule))


def tier_for(score: float, policy: dict) -> str:
    if score >= float(policy.get("t_block", 0.85)):
        return "BLOCK"
    if score >= float(policy.get("t_stepup", 0.60)):
        return "STEP_UP"
    if score >= float(policy.get("t_warning", 0.30)):
        return "WARNING"
    return "SAFE"


def action_for(tier: str, score: float, hard_decline: bool, policy: dict, has_parent: bool = False) -> str:
    """Docs/06, section 5. A Block is a timed hold unless something makes it a hard decline."""
    if tier == "SAFE":
        return "ALLOW"
    if tier == "WARNING":
        return "WARN"
    if tier == "STEP_UP":
        return "STEP_UP"
    if (score >= float(policy.get("t_decline", 0.95)) or hard_decline or policy.get("block_mode") == "decline"
            or has_parent):
        return "DECLINE"
    return "HOLD"


def active_model_dir(model_root: Path) -> Path | None:
    """The directory named by the ACTIVE pointer file, if it holds a model."""
    ptr = model_root / "ACTIVE"
    if not ptr.exists():
        return None
    d = model_root / ptr.read_text(encoding="utf-8").strip()
    return d if (d / "meta.json").exists() else None
