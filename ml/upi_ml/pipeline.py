"""The offline pipeline: simulate -> replay -> train -> register -> backfill decisions."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app import db
from app.config import ROOT, get_settings
from app.core import explain
from app.core.present import render_reasons
from app.core.rules import RuleEngine
from app.core.scoring import ModelBundle, active_model_dir
from app.core.textmodel import TextModel

from .offline import DEFAULT_POLICY, score_frame
from .replay import run_replay
from .simulator import DAY, SimConfig, build_world, generate_events

ARTIFACTS = ROOT / "ml" / "artifacts"
ROWS = ARTIFACTS / "train_rows.parquet"


def dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


async def simulate(n_users: int = 800, days: int = 90, n_campaigns: int = 66, seed: int = 7,
                   load_db: bool = True) -> dict:
    t0 = time.time()
    if load_db:                      # fail now, not after minutes of replay, if the database cannot start
        await db.init_pool()
    cfg = SimConfig(n_users=n_users, days=days, n_campaigns=n_campaigns, seed=seed, end_ts=time.time())
    text_path = ARTIFACTS / "text.joblib"
    if not text_path.exists():
        from .text_train import train_text

        train_text(verbose=False)
    text_model = TextModel.load(text_path)

    for old in list(ARTIFACTS.glob("ens-*")):        # models from an earlier world cannot be compared with this one
        import shutil

        shutil.rmtree(old, ignore_errors=True)
    (ARTIFACTS / "ACTIVE").unlink(missing_ok=True)

    w = build_world(cfg)
    events = generate_events(w)
    pays = [e for e in events if e["t"] == "pay"]
    scams = [e for e in pays if e["fraud"] and not e["exclude"]]
    print(f"world: {len(w.users):,} users, {len(w.vpas):,} UPI IDs, {len(w.campaigns)} campaigns | "
          f"events: {len(events):,} ({len(pays):,} payments, {len(scams):,} scam payments)")

    res = await run_replay(w, events, text_model)
    df = pd.DataFrame(res.rows)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    df.to_parquet(ROWS, index=False)
    print(f"replay: {len(df):,} training rows in {res.seconds:.0f}s -> {ROWS.relative_to(ROOT)}")

    counts: dict = {}
    if load_db:
        from .seed_db import load_world

        counts = await load_world(w, events, res)
        print("PostgreSQL loaded:", json.dumps(counts))
        await db.close_pool()
    print(f"simulate finished in {time.time() - t0:.0f}s")
    return {"rows": len(df), "counts": counts}


async def train_and_register(promote: bool = True, register: bool = True) -> dict:
    from .train import train

    m = train(promote=promote)
    if register:
        await db.init_pool()
        if m["promoted"]:
            await db.execute("UPDATE model_registry SET stage='archived' WHERE stage='active'")
        await db.execute(
            "INSERT INTO model_registry(version,kind,artifact_path,metrics,gates,stage) VALUES($1,'ensemble',$2,$3,$4,$5)",
            m["version"], f"ml/artifacts/{m['version']}", {k: v for k, v in m.items() if k != "gates"}, m["gates"],
            "active" if m["promoted"] else "candidate")
        if m["promoted"]:
            from .seed_db import load_catalogues

            bundle = ModelBundle.load(ARTIFACTS / m["version"])
            await load_catalogues(bundle.prototypes)        # learned prototypes replace the hand-written ones
        await db.close_pool()
    return m


async def backfill() -> int:
    """Score the last 15 days of simulated history into shadow decisions.

    This is what the dashboard and the policy lab show on first run: "the last two weeks, had the
    system been watching".
    """
    t0 = time.time()
    active = active_model_dir(ARTIFACTS)
    bundle = ModelBundle.load(active) if active else None
    engine = RuleEngine()
    df = pd.read_parquet(ROWS)
    end = float(df["ts"].max())
    df = df[df["ts"] >= end - 15 * DAY].reset_index(drop=True)
    out = score_frame(df, bundle, engine, DEFAULT_POLICY, explain_rows=True)
    version = bundle.version if bundle else "rules-only"

    await db.init_pool()
    await db.execute("DELETE FROM outcomes WHERE decision_id IN (SELECT decision_id FROM risk_decisions WHERE is_synthetic)")
    await db.execute("DELETE FROM risk_decisions WHERE is_synthetic")
    rows, outcomes = [], []
    amounts = df["amount"].to_numpy()
    for i in range(len(df)):
        r = df.iloc[i]
        did = uuid.uuid5(uuid.NAMESPACE_URL, "decision/" + r["txn_id"])
        rr = out["rules"][i]
        raw_reasons = out["reasons"][i] or []
        fam = out["family"][i]
        reasons = render_reasons("en", raw_reasons, fam)
        parts = out["parts"]
        detail = {"s_rule": float(out["s_rule"][i]), "raw": float(out["raw"][i]),
                  "p_xgb": float(parts["p_xgb"][i]) if parts else None,
                  "p_rf": float(parts["p_rf"][i]) if parts and "p_rf" in parts else None,
                  "s_anom": float(parts["s_anom"][i]) if parts and "s_anom" in parts else None,
                  "rule_effects": rr.effects, "payee": {"band": out["auxes"][i].get("band"),
                                                        "registered_name": out["auxes"][i].get("registered_name")},
                  "claimed_name": out["auxes"][i].get("claimed_name")}
        rows.append((did, r["txn_id"], r["tenant"], uuid.UUID(r["user_id"]), r["payee"], float(amounts[i]),
                     float(out["score"][i]), out["tier"][i], out["action"][i], [x["code"] for x in raw_reasons], reasons,
                     rr.fired, {k: round(v, 4) for k, v in (out["groups"][i] or {}).items()}, fam,
                     out["family_sim"][i], out["feats"][i], detail, version, engine.version, dt(float(r["ts"]))))
        outcomes.append((did, dt(float(r["ts"]))))
    await db.executemany(
        """INSERT INTO risk_decisions(decision_id,txn_id,tenant_id,user_id,payee_vpa,amount,score,tier,action,enforced,
              reason_codes,reasons,rules_fired,contributions,scam_family,family_sim,features,detail,model_version,
              rules_version,degraded,latency_ms,is_synthetic,created_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,false,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,false,0,true,$20)""", rows)
    await db.executemany(
        "INSERT INTO outcomes(decision_id,user_action,final_status,created_at) VALUES($1,'SHADOW_ALLOWED','SUCCESS',$2)",
        outcomes)
    tiers = pd.Series(out["tier"]).value_counts().to_dict()
    await db.execute(
        "INSERT INTO meta(key,value) VALUES('backfill',$1) ON CONFLICT (key) DO UPDATE SET value=$1, updated_at=now()",
        {"decisions": len(rows), "tiers": tiers, "model_version": version, "at": time.time()})
    await db.close_pool()
    print(f"backfill: {len(rows):,} shadow decisions ({tiers}) in {time.time() - t0:.0f}s")
    return len(rows)


async def demo_reset(base: str) -> None:
    import httpx

    key = next(iter(get_settings().api_keys), "")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{base}/v1/demo/reset", headers={"X-API-Key": key})
        print(r.status_code, r.text[:300])
