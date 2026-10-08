"""Turn raw reasons and family codes into what the payer reads, in their language."""
from __future__ import annotations

from .. import i18n

DAY = 86400.0


def render_reasons(locale: str, raw: list[dict], family_code: str | None) -> list[dict]:
    out = []
    for r in raw or []:
        code, ev = r["code"], dict(r.get("evidence") or {})
        fmt = dict(ev)
        if "vpa_age_days" in ev:
            fmt["vpa_age"] = i18n.duration(locale, ev["vpa_age_days"] * DAY)
        if "device_age_days" in ev:
            fmt["device_age"] = i18n.duration(locale, ev["device_age_days"] * DAY)
        if "count" in ev:
            fmt["n"] = i18n.ordinal(locale, int(ev["count"]))
        if "source_key" in ev:
            fmt["source"] = i18n.catalog(locale)["sources"].get(ev["source_key"], "")
        if code == "NOTE_SCAM_LANGUAGE":
            fmt["family_name"] = i18n.family(locale, family_code or "UNCLASSIFIED")["noun"]
        out.append({"code": code, "group": r.get("group", ""), "evidence": ev,
                    "message": i18n.render_reason(locale, code, r.get("variant"), fmt)})
    return out


def family_payload(locale: str, code: str | None, sim: float | None, with_question: bool = False,
                   variant: str | None = None) -> dict | None:
    if not code:
        return None
    f = i18n.family(locale, code, variant)
    out = {"code": code, "name": f["name"], "similarity": round(float(sim or 0.0), 3), "headline": f["headline"],
           "body": f["body"], "audio_key": code}
    if with_question:
        out["question"] = f["question"]
    return out
