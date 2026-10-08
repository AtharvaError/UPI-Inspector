"""Text utilities that need no model: entity extraction, red-flag highlighting, name matching."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

# --- entity extraction (docs/06, section 8.1) ---------------------------------
RE_VPA = re.compile(r"(?<![\w.\-@])([a-zA-Z0-9][a-zA-Z0-9._\-]{1,63})@([a-zA-Z]{2,32})(?![\w]|\.[a-zA-Z])")
RE_PHONE = re.compile(r"(?<![\d])(?:\+?91[\s\-]?|0)?([6-9]\d{9})(?!\d)")
RE_URL = re.compile(r"(https?://[^\s<>\"']+|\b(?:bit\.ly|tinyurl\.com|t\.co|cutt\.ly|rb\.gy|is\.gd|goo\.gl|shorturl\.at)/[^\s<>\"']+)",
                    re.IGNORECASE)
RE_AMOUNT = re.compile(r"(?:₹|\brs\.?|\binr)\s?([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)


def extract_entities(text: str) -> dict:
    vpas = sorted({f"{m.group(1)}@{m.group(2)}".lower() for m in RE_VPA.finditer(text)})
    phones = sorted({m.group(1) for m in RE_PHONE.finditer(text)})
    urls = sorted({m.group(1).rstrip(".,)") for m in RE_URL.finditer(text)})
    amounts = []
    for m in RE_AMOUNT.finditer(text):
        try:
            amounts.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return {"vpas": vpas, "phones": phones, "urls": urls, "amounts": amounts}


# --- red-flag lexicon, used for highlighting only (docs/06, section 9) ------------------
_LEX: list[tuple[str, list[str]]] = [
    ("urgency", [r"immediately", r"urgent(?:ly)?", r"within \d+\s?(?:hours?|hrs?|minutes?|mins?)", r"right now",
                 r"last warning", r"final notice", r"turant", r"abhi", r"jaldi", r"today only", r"तुरंत", r"अभी"]),
    ("authority", [r"police", r"cyber cell", r"\bcbi\b", r"customs", r"court", r"arrest warrant", r"\bed\b",
                   r"narcotics", r"income tax", r"पुलिस", r"गिरफ्तार", r"अदालत"]),
    ("threat", [r"(?:account|a/c|card|sim|number) (?:will be|is|has been) (?:blocked|suspended|deactivated|closed)",
                r"(?:connection|power|electricity) will be (?:cut|disconnected)", r"legal action", r"\bfir\b",
                r"blocked", r"disconnect(?:ed|ion)?", r"बंद", r"कट"]),
    ("refund_bait", [r"refund", r"cashback", r"claim your", r"reward", r"won", r"lottery", r"prize", r"रिफंड", r"इनाम"]),
    ("secrets", [r"share (?:the |your )?otp", r"\botp\b", r"upi pin", r"enter (?:your )?pin", r"verify (?:your )?pin",
                 r"\bcvv\b", r"पिन", r"ओटीपी"]),
    ("secrecy", [r"do not (?:tell|inform|share with) anyone", r"don'?t tell", r"stay on (?:the )?call",
                 r"do not disconnect", r"kisi ko mat", r"किसी को मत"]),
    ("investment", [r"guaranteed (?:returns?|profit)", r"double your", r"daily (?:income|profit|earning)",
                    r"work from home", r"task", r"invest", r"\d+% (?:returns?|profit)", r"मुनाफ"]),
]
_LEX_RE = [(kind, re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)) for kind, pats in _LEX]


def red_flags(text: str) -> list[dict]:
    """Non-overlapping highlighted spans, in order."""
    spans: list[tuple[int, int, str]] = []
    for kind, rx in _LEX_RE:
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), kind))
    for m in RE_URL.finditer(text):
        if re.search(r"bit\.ly|tinyurl|t\.co|cutt\.ly|rb\.gy|is\.gd|goo\.gl|shorturl", m.group(1), re.IGNORECASE):
            spans.append((m.start(), m.end(), "link"))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out: list[dict] = []
    last_end = -1
    for start, end, kind in spans:
        if start >= last_end:
            out.append({"start": start, "end": end, "kind": kind})
            last_end = end
    return out


# --- name matching (docs/06, section 8.3) --------------------------------------
_STRIP = re.compile(r"[^\w\s]", re.UNICODE)
_STOP = {"mr", "mrs", "ms", "shri", "smt", "sri", "m/s", "ms/", "pvt", "private", "ltd", "limited", "llp",
         "co", "company", "inc", "the", "and", "&"}

INSTITUTIONAL = [
    "bank", "sbi", "hdfc", "icici", "axis", "kotak", "pnb", "paytm", "phonepe", "gpay", "google pay", "amazon",
    "flipkart", "refund", "customer care", "customercare", "support", "helpdesk", "help desk", "police",
    "cyber cell", "cybercell", "cyber crime", "cbi", "customs", "court", "income tax", "kyc", "electricity",
    "bijli", "bses", "msedcl", "rbi", "npci", "courier", "fedex", "dhl", "narcotics", "trai", "telecom",
    "verification", "safe account", "government", "govt", "ministry", "department",
]


def normalise_name(name: str) -> str:
    words = _STRIP.sub(" ", name.lower()).split()
    return " ".join(w for w in words if w not in _STOP)


def name_match(claimed: str, registered: str) -> float:
    a, b = normalise_name(claimed), normalise_name(registered)
    if not a or not b:
        return 1.0
    return round(fuzz.token_set_ratio(a, b) / 100.0, 3)


def is_institutional(name: str) -> bool:
    n = " " + _STRIP.sub(" ", name.lower()) + " "
    n = re.sub(r"\s+", " ", n)
    return any(f" {w} " in n or (len(w) > 5 and w in n) for w in INSTITUTIONAL)
