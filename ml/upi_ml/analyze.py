"""Where do the false positives come from? Run after training:  python -m upi_ml.analyze [version]"""
from __future__ import annotations

import json
import sys
from collections import Counter

import numpy as np
import pandas as pd

from app.config import ROOT
from app.core.rules import RuleEngine
from app.core.scoring import ModelBundle, active_model_dir

from .offline import score_frame

ART = ROOT / "ml" / "artifacts"
DAY = 86400.0


def main(version: str | None = None) -> None:
    d = (ART / version) if version else (active_model_dir(ART) or sorted(ART.glob("ens-*"))[-1])
    bundle = ModelBundle.load(d)
    df = pd.read_parquet(ART / "train_rows.parquet")
    end = df["ts"].max()
    te = df[df["ts"] >= end - 15 * DAY].reset_index(drop=True)
    out = score_frame(te, bundle, RuleEngine())
    score, cal = out["score"], out["calibrated"]
    y = te["is_fraud"].to_numpy()
    print(f"model {bundle.version}: test rows {len(te):,}, fraud {int(y.sum())}")
    for name, s in (("calibrated model only", cal), ("after rule floors and caps", score)):
        legit, fraud = s[y == 0], s[y == 1]
        print(f"  {name:28s} legit>=0.3 {np.mean(legit >= 0.3):.4f}  legit>=0.6 {np.mean(legit >= 0.6):.4f}  "
              f"legit>=0.85 {np.mean(legit >= 0.85):.4f} | fraud>=0.6 {np.mean(fraud >= 0.6):.4f}  fraud>=0.85 {np.mean(fraud >= 0.85):.4f}")
    fp = np.where((y == 0) & (score >= 0.6))[0]
    print(f"\nlegitimate payments at Step-Up or above: {len(fp)} ({len(fp) / max(1, (y == 0).sum()):.2%})")
    aux = [json.loads(a) for a in te["aux_json"]]
    bands = Counter(aux[i].get("band") for i in fp)
    print("  payee band:", dict(bands))
    rules = Counter()
    floor_only = 0
    for i in fp:
        rr = out["rules"][i]
        for r in rr.fired:
            rules[r] += 1
        if cal[i] < 0.6 <= score[i]:
            floor_only += 1
    print("  rules fired:", dict(rules.most_common()))
    print(f"  lifted over 0.6 only by a rule floor: {floor_only}")
    f = te.iloc[fp]
    print("  consortium_reported:", int(f["consortium_reported"].sum()), "| confirmed_90d>0:", int((f["confirmed_90d"] > 0).sum()),
          "| reports_30d>0:", int((f["reports_30d"] > 0).sum()), "| new payee:", int(f["is_new_payee"].sum()),
          "| call active:", int(f["call_active"].sum()), "| msg flag:", int(f["msg_flag_active"].sum()))
    print("  distinct payees:", f["payee"].nunique(), "| top payees:", f["payee"].value_counts().head(6).to_dict())
    hist, edges = np.histogram(score[y == 0], bins=[0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .85, .9, .95, 1.0001])
    print("\nlegit score histogram:", {f"{edges[i]:.2f}": int(h) for i, h in enumerate(hist)})
    hist, _ = np.histogram(score[y == 1], bins=edges)
    print("fraud score histogram:", {f"{edges[i]:.2f}": int(h) for i, h in enumerate(hist)})
    if bundle.iso_x is not None:
        print("isotonic knots:", [(round(float(a), 3), round(float(b), 3)) for a, b in zip(bundle.iso_x, bundle.iso_y)][:40])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
