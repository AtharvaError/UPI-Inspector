"""One-tap complaint pack (F-25): a PDF and a JSON file with the evidence. Nothing is sent anywhere."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from .. import db
from ..common import clean
from ..config import ROOT
from ..security import ApiError

PACK_DIR = ROOT / ".data" / "packs"
RISK_WORD = {0: "not rated", 1: "medium risk", 2: "high risk", 3: "very high risk"}


def _latin(s: object) -> str:
    return str(s if s is not None else "").replace("₹", "Rs. ").replace("—", "-").replace("’", "'") \
        .encode("latin-1", "replace").decode("latin-1")


def _fmt(d) -> str:
    return d.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y, %H:%M:%S IST") if d else ""


async def build_payload(*, decision_id: str | None, txn_id: str | None, check_id: str | None, description: str | None,
                        owner_user_id: str | None) -> dict:
    d = t = None
    if decision_id:
        d = await db.fetchrow("SELECT * FROM risk_decisions WHERE decision_id=$1", uuid.UUID(decision_id))
    elif txn_id:
        d = await db.fetchrow("SELECT * FROM risk_decisions WHERE txn_id=$1", txn_id)
    if d:
        t = await db.fetchrow("SELECT * FROM transactions WHERE txn_id=$1", d["txn_id"])
    elif txn_id:
        t = await db.fetchrow("SELECT * FROM transactions WHERE txn_id=$1", txn_id)
    m = await db.fetchrow("SELECT * FROM message_checks WHERE check_id=$1", uuid.UUID(check_id)) if check_id else None
    if not (d or t or m):
        raise ApiError(404, "NOT_FOUND", "Nothing to build a pack from")
    user_id = (t or d or m)["user_id"]
    if owner_user_id and str(user_id) != owner_user_id:
        raise ApiError(403, "FORBIDDEN", "Not your payment")
    u = await db.fetchrow("SELECT display_name, primary_vpa, phone_hash FROM users WHERE user_id=$1", user_id)
    at = (t or d or m)["created_at"]
    timeline = []
    seen: set = set()
    if d:
        for ln in await db.fetch("SELECT signal_type, signal_id FROM signal_links WHERE decision_id=$1", d["decision_id"]):
            seen.add(ln["signal_id"])
    calls = await db.fetch(
        "SELECT * FROM call_events WHERE callee_hash=$1 AND started_at BETWEEN $2 AND $3 ORDER BY started_at",
        u["phone_hash"], at - timedelta(hours=6), at + timedelta(minutes=5))
    for c in calls:
        dur = round(((c["ended_at"] or at) - c["started_at"]).total_seconds() / 60)
        timeline.append({"at": clean(c["started_at"]), "type": "call", "linked": c["call_id"] in seen,
                         "detail": f"Incoming call, about {max(1, dur)} min, caller "
                                   f"{'in contacts' if c['caller_known'] else 'not in contacts'}, "
                                   f"{RISK_WORD.get(c['caller_risk'], 'not rated')}"})
    msgs = await db.fetch(
        "SELECT * FROM message_checks WHERE user_id=$1 AND created_at BETWEEN $2 AND $3 ORDER BY created_at",
        user_id, at - timedelta(days=7), at + timedelta(minutes=5))
    for x in msgs:
        if x["verdict"] == "FINE" and x["check_id"] not in seen and not (m and x["check_id"] == m["check_id"]):
            continue
        timeline.append({"at": clean(x["created_at"]), "type": "message", "linked": x["check_id"] in seen,
                         "detail": f"Message checked: {x['verdict'].lower()} ({x['label'].replace('_', ' ')})",
                         "text": x["text"][:400]})
    payee = None
    if t:
        v = await db.fetchrow("SELECT registered_name, kind FROM vpas WHERE vpa_id=$1", t["payee_vpa"])
        out = await db.fetchrow("SELECT user_action, final_status FROM outcomes WHERE decision_id=$1",
                                d["decision_id"]) if d else None
        status = (out["final_status"] if out and out["final_status"] else t["status"])
        if d and d["action"] in ("DECLINE", "HOLD") and d["enforced"]:
            status = "STOPPED" if d["action"] == "DECLINE" else "PAUSED"
        payee = {"txn_id": t["txn_id"], "at": clean(t["created_at"]), "amount": float(t["amount"]), "status": status,
                 "payee_upi_id": t["payee_vpa"], "payee_registered_name": v["registered_name"] if v else "",
                 "claimed_name": t["claimed_name"], "note": t["note"]}
        timeline.append({"at": clean(t["created_at"]), "type": "payment", "linked": True,
                         "detail": f"Payment of Rs. {float(t['amount']):,.2f} to {t['payee_vpa']} - {status.lower()}"})
    timeline.sort(key=lambda e: e["at"])
    det = (d["detail"] or {}) if d else {}
    return {
        "pack_id": str(uuid.uuid4()), "generated_at": clean(datetime.now(timezone.utc)),
        "complainant": {"name": u["display_name"], "upi_id": u["primary_vpa"]},
        "transaction": payee,
        "assessment": ({"scam_family": det.get("family_en") or d["scam_family"],
                        "reasons": [r["message"] for r in (det.get("reasons_en") or d["reasons"] or [])],
                        "tier": d["tier"], "action": d["action"]} if d else None),
        "timeline": timeline, "description": (description or "").strip()[:1000],
        "how_to_file": ["Call 1930, the national cybercrime helpline, as soon as possible.",
                        "File a complaint at cybercrime.gov.in and attach this document.",
                        "Tell your bank through its official app or branch."],
        "disclaimer": "Prototype document generated from synthetic data. UPI Inspector does not file complaints.",
        "_refs": {"decision_id": str(d["decision_id"]) if d else None, "txn_id": t["txn_id"] if t else None,
                  "check_id": check_id, "tenant_id": (d or t or m)["tenant_id"], "user_id": str(user_id)},
    }


def render_pdf(p: dict) -> bytes:
    # Imported here, not at the top: the PDF library pulls in compiled font code, and on this laptop Windows
    # has at times refused to load such files. That must cost a complaint pack, never the whole service.
    try:
        from fpdf import FPDF
    except ImportError as exc:
        raise ApiError(503, "UNAVAILABLE", "The PDF writer could not be loaded on this machine") from exc
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, "Cyber fraud complaint pack", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 5, _latin(f"Pack {p['pack_id']}  |  generated {p['generated_at']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    def heading(text: str) -> None:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, _latin(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)

    def row(k: str, v: object) -> None:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(48, 6, _latin(k))
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(0, 6, _latin(v), new_x="LMARGIN", new_y="NEXT")

    heading("Complainant")
    row("Name", p["complainant"]["name"])
    row("UPI ID", p["complainant"]["upi_id"])
    if p.get("transaction"):
        t = p["transaction"]
        heading("Transaction")
        row("Transaction ID", t["txn_id"])
        row("Date and time", t["at"])
        row("Amount", f"Rs. {t['amount']:,.2f}")
        row("Status", t["status"])
        row("Paid to (UPI ID)", t["payee_upi_id"])
        row("Registered name", t["payee_registered_name"])
        if t.get("claimed_name"):
            row("Name I was given", t["claimed_name"])
        if t.get("note"):
            row("Payment note", t["note"])
    if p.get("assessment"):
        a = p["assessment"]
        heading("What the bank's fraud check found")
        row("Pattern", a["scam_family"] or "Unclassified")
        for r in a["reasons"]:
            pdf.multi_cell(0, 6, _latin("-  " + r), new_x="LMARGIN", new_y="NEXT")
    heading("Timeline")
    if not p["timeline"]:
        pdf.multi_cell(0, 6, "No related calls or messages were recorded.", new_x="LMARGIN", new_y="NEXT")
    for e in p["timeline"]:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, _latin(f"{e['at']}   [{e['type'].upper()}]"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5.5, _latin(e["detail"]), new_x="LMARGIN", new_y="NEXT")
        if e.get("text"):
            pdf.set_text_color(90, 90, 90)
            pdf.multi_cell(0, 5, _latin('"' + e["text"] + '"'), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        pdf.ln(1)
    if p.get("description"):
        heading("What happened, in my words")
        pdf.multi_cell(0, 6, _latin(p["description"]), new_x="LMARGIN", new_y="NEXT")
    heading("How to file")
    for i, step in enumerate(p["how_to_file"], 1):
        pdf.multi_cell(0, 6, _latin(f"{i}. {step}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 4.5, _latin(p["disclaimer"]), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


async def create_pack(*, decision_id: str | None, txn_id: str | None, check_id: str | None, description: str | None,
                      requested_by: str, owner_user_id: str | None) -> dict:
    payload = await build_payload(decision_id=decision_id, txn_id=txn_id, check_id=check_id, description=description,
                                  owner_user_id=owner_user_id)
    refs = payload.pop("_refs")
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    path = PACK_DIR / f"{payload['pack_id']}.pdf"
    path.write_bytes(render_pdf(payload))
    await db.execute(
        "INSERT INTO complaint_packs(pack_id,decision_id,txn_id,check_id,requested_by,payload,pdf_path) "
        "VALUES($1,$2,$3,$4,$5,$6,$7)",
        uuid.UUID(payload["pack_id"]), uuid.UUID(refs["decision_id"]) if refs["decision_id"] else None, refs["txn_id"],
        uuid.UUID(refs["check_id"]) if refs["check_id"] else None, requested_by, payload, str(path))
    return {"payload": payload, "refs": refs, "path": path}
