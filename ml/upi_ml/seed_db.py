"""Load the simulated world and the replay's final state into PostgreSQL."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app import db, i18n
from app.config import ROOT, get_settings
from app.core import explain
from app.core.textcheck import extract_entities, red_flags
from app.core.textmodel import CLASS_FAMILY, verdict_for
from app.security import hash_secret, phone_hash, text_hash

from .replay import ReplayResult
from .simulator import DEMO_CALLERS, TENANTS, World, uid

STAFF = [
    ("bank_a", "Asha Kulkarni", "admin@banka.demo", "admin"),
    ("bank_a", "Dev Menon", "analyst@banka.demo", "analyst"),
    ("bank_a", "Ritu Bose", "auditor@banka.demo", "auditor"),
    ("bank_b", "Farid Khan", "admin@bankb.demo", "admin"),
    ("bank_b", "Neha Pillai", "analyst@bankb.demo", "analyst"),
]
FAMILY_CODES = ["DIGITAL_ARREST", "FAKE_REFUND", "MULE_TRANSFER", "IMPERSONATION", "KYC_BILL_THREAT",
                "INVESTMENT_TASK", "UNCLASSIFIED"]


def dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


async def reset_schema() -> None:
    await db.execute("DROP SCHEMA public CASCADE")
    await db.execute("CREATE SCHEMA public")
    sql = (ROOT / "infra" / "postgres" / "001_schema.sql").read_text(encoding="utf-8")
    async with db.pool().acquire() as conn:
        await conn.execute(sql)
    await db.apply_additions()


async def load_catalogues(prototypes: dict | None = None) -> None:
    protos = prototypes or explain.default_prototypes()
    fam_rows = []
    for code in FAMILY_CODES:
        msgs = {loc: {k: i18n.family(loc, code)[k] for k in ("name", "headline", "body", "question", "risky_answer")}
                for loc in i18n.LOCALES}
        fam_rows.append((code, msgs["en"]["name"], protos.get(code, [0.0] * len(explain.DIMS)), msgs, code))
    await db.execute("DELETE FROM scam_families")
    await db.executemany("INSERT INTO scam_families(code,name,prototype,messages,audio_key) VALUES($1,$2,$3,$4,$5)",
                         fam_rows)
    await db.execute("DELETE FROM reason_codes")
    await db.executemany(
        "INSERT INTO reason_codes(code,signal_group,priority,templates) VALUES($1,$2,$3,$4)",
        [(code, g, prio, {loc: i18n.catalog(loc)["reasons"].get(code, code) for loc in i18n.LOCALES})
         for code, (g, prio) in explain.REASON_META.items()])
    scripts = json.loads((ROOT / "infra" / "seed" / "rehearsals.json").read_text(encoding="utf-8"))
    await db.execute("DELETE FROM rehearsal_scripts")
    await db.executemany(
        "INSERT INTO rehearsal_scripts(code,locale,family,title,minutes,script) VALUES($1,$2,$3,$4,$5,$6)",
        [(s["code"], s["locale"], s["family"], s["title"], s["minutes"], s["script"]) for s in scripts])


async def load_world(w: World, events: list[dict], res: ReplayResult) -> dict:
    s = get_settings()
    await db.init_pool()
    await reset_schema()
    end = w.cfg.end_ts

    await db.executemany("INSERT INTO tenants(tenant_id,name) VALUES($1,$2)", [(t, n) for t, n, _ in TENANTS])
    await db.executemany("INSERT INTO policy_config(tenant_id) VALUES($1)", [(t,) for t, _, _ in TENANTS])

    pin = hash_secret(s.seed_user_pin)
    await db.executemany(
        """INSERT INTO users(user_id,tenant_id,display_name,phone_hash,phone_last4,pin_hash,primary_vpa,segment,age_band,
                             locale,balance,seed_balance,is_demo,is_synthetic,first_seen)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11,$12,$13,$14)""",
        [(uuid.UUID(u["user_id"]), u["tenant"], u["name"], u["phone_hash"], u["phone"][-4:],
          pin if u["is_demo"] else None, u["vpa"], u["segment"], u["age_band"], u["locale"], u["balance"],
          u["is_demo"], not u["is_demo"], dt(u["first_seen"])) for u in w.users])

    devs = []
    for u in w.users:
        devs.append((uuid.UUID(u["device_id"]), uuid.UUID(u["user_id"]), u["fingerprint"], dt(u["first_seen"]), dt(end)))
        if u.get("new_device_id"):
            devs.append((uuid.UUID(u["new_device_id"]), uuid.UUID(u["user_id"]), u["fingerprint"] + "-b",
                         dt(u["device_switch_ts"]), dt(end)))
    await db.executemany("INSERT INTO devices(device_id,user_id,fingerprint,first_seen,last_seen) VALUES($1,$2,$3,$4,$5)", devs)

    await db.executemany(
        """INSERT INTO vpas(vpa_id,tenant_id,registered_name,kind,verified,owner_user_id,on_denylist,is_synthetic,first_seen)
           VALUES($1,$2,$3,$4,$5,$6,$7,true,$8)""",
        [(v["vpa"], v["tenant"], v["registered_name"], v["kind"], v["verified"],
          uuid.UUID(v["owner_user_id"]) if v["owner_user_id"] else None, v.get("on_denylist", False), dt(v["first_seen"]))
         for v in w.vpas.values()])
    await db.executemany("INSERT INTO vpa_devices(vpa_id,fingerprint) VALUES($1,$2)",
                         [(vpa, fp) for vpa, fps in w.vpa_fps.items() for fp in fps])

    pays = [e for e in events if e["t"] == "pay"]
    await db.executemany(
        """INSERT INTO transactions(txn_id,tenant_id,user_id,payer_vpa,payee_vpa,claimed_name,amount,note,device_id,session,
                                    status,truth_is_fraud,truth_family,campaign_id,is_synthetic,created_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'SUCCESS',$11,$12,$13,true,$14)""",
        [(e["txn_id"], e["user"]["tenant"], uuid.UUID(e["user"]["user_id"]), e["user"]["vpa"], e["payee"], e["claimed"],
          e["amount"], e["note"] or None, uuid.UUID(e["device_id"]), e["session"],
          (None if (e["exclude"] and not e["fraud"]) else bool(e["fraud"])), e["family"], e["campaign"], dt(e["ts"]))
         for e in pays])

    # reputation: this bank's final view of every UPI ID its customers touched
    store = res.store
    rep_rows = []
    async for key in store.r.scan_iter(match="vpa_rep:*", count=2000):
        _, tenant, vpa = key.split(":", 2)
        if vpa not in w.vpas:
            continue
        d = await store.rep_snapshot(tenant, vpa, end)
        rep_rows.append((tenant, vpa, d["recv"], d["bad"], d["lifetime_recv"], d["reports_30d"], d["confirmed_90d"],
                         d["near_miss_14d"], d["fan_in_1h"], d["fan_in_24h"], d["first_time_payer_ratio_24h"],
                         d["p_bad"], d["maturity"], d["trust_score"], d["band"], d["band_reason"],
                         dt(d["counters_at"]), dt(end)))
    await db.executemany(
        """INSERT INTO vpa_reputation(tenant_id,vpa_id,recv_decayed,bad_decayed,lifetime_recv,reports_30d,confirmed_90d,
              near_miss_14d,fan_in_1h,fan_in_24h,first_time_payer_ratio_24h,p_bad,maturity,trust_score,band,band_reason,
              counters_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)""", rep_rows)
    await db.execute("INSERT INTO vpa_reputation_seed SELECT * FROM vpa_reputation")
    await db.executemany("INSERT INTO vpa_events(tenant_id,vpa_id,kind,weight,ref,at) VALUES($1,$2,$3,$4,$5,$6)",
                         [(b["tenant"], b["vpa"], b["kind"], b["weight"], b["ref"], dt(b["ts"])) for b in res.bad_events])
    await db.executemany(
        "INSERT INTO vpa_reputation_history(tenant_id,vpa_id,at,trust_score,band,cause) VALUES($1,$2,$3,$4,$5,'seed')",
        [(r[0], r[1], dt(end), r[13], r[14]) for r in rep_rows if r[14] in ("CAUTION", "REPORTED")])

    calls: dict[str, dict] = {}
    for e in events:
        if e["t"] != "call":
            continue
        c = calls.setdefault(e["call_id"], {"callee": e["user"]["phone_hash"], "caller": e["caller_hash"],
                                            "risk": e["risk"], "known": e["known"], "start": None, "end": None})
        c["start" if e["event"] == "started" else "end"] = e["ts"]
    await db.executemany(
        """INSERT INTO call_events(call_id,callee_hash,caller_hash,caller_risk,caller_known,started_at,ended_at,source)
           VALUES($1,$2,$3,$4,$5,$6,$7,'simulated')""",
        [(uuid.UUID(cid), c["callee"], c["caller"], c["risk"], c["known"], dt(c["start"]),
          dt(c["end"]) if c["end"] else None) for cid, c in calls.items() if c["start"]])

    risk_rows = [(h, r, "seed", dt(end)) for h, r in w.caller_risk.items()]
    for i in range(50):     # extra seeded numbers across the three levels
        risk_rows.append((phone_hash(f"9222{i:06d}"), 1 + i % 3, "seed", dt(end)))
    await db.executemany("INSERT INTO caller_risk_list(caller_hash,risk,source,updated_at) VALUES($1,$2,$3,$4) "
                         "ON CONFLICT DO NOTHING", risk_rows)

    msg_rows, ind = [], {}
    for m in res.msg_rows:
        u = m["user"]
        ents = extract_entities(m["text"])
        ents["phones"] = [phone_hash(p) for p in ents["phones"]]
        msg_rows.append((uuid.UUID(m["check_id"]), uuid.UUID(u["user_id"]), u["tenant"], m["text"], text_hash(m["text"]),
                         m["label"], m["scam_prob"], verdict_for(m["scam_prob"]), CLASS_FAMILY.get(m["label"]), ents,
                         red_flags(m["text"]), m["probs"], m["flagged"], dt(m["ts"])))
        if m["flagged"]:
            for v in ents["vpas"]:
                ind.setdefault((u["tenant"], "vpa", v), {"users": set(), "first": m["ts"], "last": m["ts"]})
                rec = ind[(u["tenant"], "vpa", v)]
                rec["users"].add(u["user_id"])
                rec["last"] = max(rec["last"], m["ts"])
    await db.executemany(
        """INSERT INTO message_checks(check_id,user_id,tenant_id,text,text_hash,label,scam_prob,verdict,family,entities,
              red_flags,class_probs,flag_set,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""", msg_rows)
    await db.executemany(
        "INSERT INTO flagged_indicators(tenant_id,indicator_type,value,reporter_count,first_seen,last_seen) "
        "VALUES($1,$2,$3,$4,$5,$6)",
        [(k[0], k[1], k[2], len(v["users"]), dt(v["first"]), dt(v["last"])) for k, v in ind.items()])
    await db.executemany(
        "INSERT INTO flagged_indicator_reporters(tenant_id,indicator_type,value,user_id) VALUES($1,$2,$3,$4)",
        [(k[0], k[1], k[2], uuid.UUID(uid_)) for k, v in ind.items() for uid_ in v["users"]])

    await db.executemany(
        """INSERT INTO consortium_signals(vpa_token,source_tenant,bad_decayed,recv_decayed,p_bad,band,confirmed,updated_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8)""",
        [(k[0], k[1], v["bad"], v["recv"], v["p_bad"], v["band"], v["confirmed"], dt(v["ts"]))
         for k, v in res.consortium.items()])
    await db.executemany(
        """INSERT INTO graph_features(vpa_id,downstream_hop,shares_device_with_reported,passthrough_ratio,community_id,
              community_risk_share,computed_at) VALUES($1,$2,$3,$4,$5,$6,$7)""",
        [(vpa, g["downstream_hop"], bool(g["shares_device_with_reported"]), g["passthrough_ratio"], g["community_id"],
          g["community_risk_share"], dt(end)) for vpa, g in res.graph.items() if vpa in w.vpas])

    pw = hash_secret(s.seed_staff_password)
    await db.executemany(
        "INSERT INTO staff_users(staff_id,tenant_id,display_name,email,password_hash,role) VALUES($1,$2,$3,$4,$5,$6)",
        [(uuid.UUID(uid("staff", email)), tenant, name, email, pw, role) for tenant, name, email, role in STAFF])

    meera, arjun = w.demo["meera"], w.demo["arjun"]
    await db.execute(
        """INSERT INTO guardians(guardian_link_id,user_id,guardian_user_id,mode,amount_threshold,status,is_seed)
           VALUES($1,$2,$3,'co_approve',20000,'ACTIVE',true)""",
        uuid.UUID(uid("guardian", "meera-arjun")), uuid.UUID(meera["user_id"]), uuid.UUID(arjun["user_id"]))

    await load_catalogues()
    counts = {"users": len(w.users), "vpas": len(w.vpas), "transactions": len(pays), "calls": len(calls),
              "messages": len(msg_rows), "campaigns": len(w.campaigns), "reputation_rows": len(rep_rows)}
    await db.execute(
        "INSERT INTO meta(key,value) VALUES('seed',$1) ON CONFLICT (key) DO UPDATE SET value=$1, updated_at=now()",
        {"seeded_at": end, "config": {"n_users": w.cfg.n_users, "days": w.cfg.days, "seed": w.cfg.seed},
         "counts": counts, "demo_callers": {str(k): phone_hash(v) for k, v in DEMO_CALLERS.items()}})
    return counts
