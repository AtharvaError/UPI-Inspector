"""AI-written case summaries (F-27). Generated after the decision; never an input to one.

Providers are tried in order (free hosted model, second free provider, local model) and a fixed
template always succeeds. Only tokenised, synthetic decision data is ever sent.
"""
from __future__ import annotations

import asyncio
import time
import json
import logging
import re
import uuid

import httpx

from .. import db
from ..realtime import emit_analyst
from ..runtime import rt

log = logging.getLogger("summariser")
_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
ACTIONS = ["confirm as fraud", "mark as false positive", "contact the customer", "monitor the payee",
           "no action needed"]
PROMPT = """You are assisting a bank fraud analyst. Using ONLY the facts in the JSON below, write:
1. "summary": 3 to 4 plain sentences describing what happened and why the payment was flagged.
2. "key_evidence": up to 4 short bullet strings, each a fact from the JSON.
3. "suggested_action": one sentence, chosen from: confirm as fraud, mark as false positive, contact the customer, monitor the payee, no action needed.
Do not add facts, numbers, names or causes that are not in the JSON. Do not speculate about identity or intent.
Reply with a JSON object with exactly those three keys.

"""


async def build_input(decision_id: str) -> dict | None:
    d = await db.fetchrow("SELECT * FROM risk_decisions WHERE decision_id=$1", uuid.UUID(decision_id))
    if not d:
        return None
    det, f = d["detail"] or {}, d["features"] or {}
    out = await db.fetchrow("SELECT user_action, final_status FROM outcomes WHERE decision_id=$1", d["decision_id"])
    links = await db.fetch("SELECT signal_type, signal_id FROM signal_links WHERE decision_id=$1", d["decision_id"])
    calls, messages = [], []
    for ln in links:
        if ln["signal_type"] == "call":
            c = await db.fetchrow("SELECT caller_risk, caller_known, started_at, ended_at FROM call_events WHERE call_id=$1",
                                  ln["signal_id"])
            if c:
                calls.append({"caller_risk_level": c["caller_risk"], "caller_in_contacts": c["caller_known"],
                              "minutes_before_payment": round((d["created_at"] - c["started_at"]).total_seconds() / 60),
                              "still_active": c["ended_at"] is None or c["ended_at"] > d["created_at"]})
        else:
            m = await db.fetchrow("SELECT verdict, label, created_at FROM message_checks WHERE check_id=$1", ln["signal_id"])
            if m:
                messages.append({"verdict": m["verdict"], "kind": m["label"],
                                 "minutes_before_payment": round((d["created_at"] - m["created_at"]).total_seconds() / 60)})
    return {
        "payer": "PAYER_1", "payee": "PAYEE_1", "amount_rupees": float(d["amount"]), "tier": d["tier"],
        "action": d["action"], "score": round(d["score"], 2),
        "reasons": [r["message"] for r in (det.get("reasons_en") or d["reasons"] or [])],
        "rules_fired": list(d["rules_fired"] or []), "scam_family": det.get("family_en") or d["scam_family"],
        "payee_reputation_band": (det.get("payee") or {}).get("band"),
        "payee_trust_score": (det.get("payee") or {}).get("trust_score"),
        "payee_account_age_days": round(float(f.get("vpa_age_days", 0))),
        "payers_in_last_hour": int(f.get("fan_in_1h", 0)), "payer_has_paid_this_payee_before": not f.get("is_new_payee", 1),
        "session": {"entry_method": f.get("vpa_entry_method"), "app_switches": int(f.get("app_switch_count", 0))},
        "name_match_score": round(float(f.get("name_match_score", 1)), 2),
        "linked_calls": calls, "linked_messages": messages,
        "outcome": out["user_action"] if out else "not recorded yet",
    }


def _readable(text: str) -> str:
    """The input names the parties PAYER_1 and PAYEE_1 so that no identity reaches a language model.
    What the analyst reads says "the payer" and "the payee"."""
    return str(text).replace("PAYER_1", "the payer").replace("PAYEE_1", "the payee")


def template_summary(data: dict) -> dict:
    reasons = data.get("reasons") or []
    fam = data.get("scam_family") or "an unusual payment"
    s = [f"A payment of ₹{data['amount_rupees']:,.0f} from {data['payer']} to {data['payee']} was scored "
         f"{data['score']} and placed in the {data['tier'].replace('_', '-').title()} tier ({data['action'].lower()})."]
    if reasons:
        s.append("The payer was shown these reasons: " + " ".join(f"“{r}”" for r in reasons))
    s.append(f"The pattern most closely matches {fam}.")
    if data.get("linked_calls") or data.get("linked_messages"):
        s.append(f"{len(data.get('linked_calls', []))} call(s) and {len(data.get('linked_messages', []))} flagged "
                 f"message(s) are linked to this payment.")
    tier = data["tier"]
    action = ("contact the customer" if tier == "BLOCK" else "monitor the payee" if tier == "STEP_UP"
              else "no action needed")
    return {"summary": " ".join(s), "key_evidence": reasons[:4],
            "suggested_action": f"Suggested: {action}.", "provider": "template", "model": None}


def _check(reply: dict, data: dict) -> bool:
    """Reject a reply that introduces numbers or party tokens that are not in the input."""
    if not isinstance(reply, dict) or not {"summary", "key_evidence", "suggested_action"} <= set(reply):
        return False
    source = json.dumps(data)
    allowed = set(re.findall(r"\d+(?:\.\d+)?", source.replace(",", "")))
    allowed |= {str(int(float(n))) for n in allowed if "." in n}
    text = json.dumps(reply).replace(",", "")
    for n in re.findall(r"\d+(?:\.\d+)?", text):
        if n not in allowed and n.rstrip("0").rstrip(".") not in allowed:
            return False
    return not re.search(r"(PAYER|PAYEE)_(?!1\b)\d+", text)


def _parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


async def _gemini(prompt: str) -> tuple[str, str] | None:
    s = rt.settings
    if not s.gemini_api_key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}:generateContent"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, headers={"x-goog-api-key": s.gemini_api_key},
                         json={"contents": [{"parts": [{"text": prompt}]}],
                               "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}})
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"], s.gemini_model


async def _groq(prompt: str) -> tuple[str, str] | None:
    s = rt.settings
    if not s.groq_api_key:
        return None
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.groq.com/openai/v1/chat/completions",
                         headers={"Authorization": f"Bearer {s.groq_api_key}"},
                         json={"model": s.groq_model, "temperature": 0.2,
                               "messages": [{"role": "user", "content": prompt}],
                               "response_format": {"type": "json_object"}})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"], s.groq_model


async def _ollama(prompt: str) -> tuple[str, str] | None:
    s = rt.settings
    if not s.ollama_url:
        return None
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{s.ollama_url.rstrip('/')}/api/generate",
                         json={"model": s.ollama_model, "prompt": prompt, "stream": False, "format": "json"})
        r.raise_for_status()
        return r.json()["response"], s.ollama_model


PROVIDERS = {"gemini": _gemini, "groq": _groq, "ollama": _ollama}


async def generate(decision_id: str) -> dict | None:
    data = await build_input(decision_id)
    if data is None:
        return None
    result = None
    prompt = PROMPT + json.dumps(data, indent=1)
    for name in [p.strip() for p in rt.settings.llm_providers.split(",")]:
        fn = PROVIDERS.get(name)
        if fn is None:
            continue
        try:
            got = await fn(prompt)
            if not got:
                continue
            reply = _parse(got[0])
            if reply and _check(reply, data):
                ev = reply["key_evidence"]
                result = {"summary": str(reply["summary"]), "key_evidence": [str(x) for x in ev][:4] if isinstance(ev, list) else [],
                          "suggested_action": str(reply["suggested_action"]), "provider": name, "model": got[1]}
                break
            log.warning("summary from %s rejected by the checks", name)
        except Exception as exc:
            log.warning("summary provider %s failed: %s", name, exc)
    if result is None:
        result = template_summary(data)
    result["summary"] = _readable(result["summary"])
    result["key_evidence"] = [_readable(x) for x in result["key_evidence"]]
    result["suggested_action"] = _readable(result["suggested_action"])
    await db.execute(
        """INSERT INTO case_summaries(decision_id,summary,key_evidence,suggested_action,provider,model)
           VALUES($1,$2,$3,$4,$5,$6)
           ON CONFLICT (decision_id) DO UPDATE SET summary=EXCLUDED.summary, key_evidence=EXCLUDED.key_evidence,
             suggested_action=EXCLUDED.suggested_action, provider=EXCLUDED.provider, model=EXCLUDED.model,
             generated_at=now()""",
        uuid.UUID(decision_id), result["summary"], result["key_evidence"], result["suggested_action"],
        result["provider"], result["model"])
    return result


async def on_assessed(ev: dict) -> None:
    dec = ev["decision"]
    if ev.get("synthetic") or not dec.get("enforced") or dec["tier"] not in ("STEP_UP", "BLOCK"):
        return
    try:
        _queue.put_nowait((dec["decision_id"], ev["tenant_id"], time.time()))
    except asyncio.QueueFull:
        pass                                         # a summary is also written on demand when the page asks


def clear_queue() -> int:
    """Forget the summaries waiting to be written (a demo reset has deleted their decisions)."""
    n = 0
    while not _queue.empty():
        _queue.get_nowait()
        n += 1
    return n


async def summariser_loop() -> None:
    while True:
        decision_id, tenant, queued = await _queue.get()
        try:
            wait = 1.0 - (time.time() - queued)      # let the logger write the decision and its links first
            if wait > 0:
                await asyncio.sleep(wait)
            res = await generate(decision_id)
            if res:
                await emit_analyst(tenant, "case.summary_ready", {"decision_id": decision_id})
            # Free tiers are rate-limited, so pause after a call to an outside model. The built-in template
            # costs nothing, and pausing after it made a burst of flagged payments wait minutes for summaries.
            if res and res.get("provider") != "template":
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("summary failed for %s", decision_id)
