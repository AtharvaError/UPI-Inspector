"""Server-rendered strings in the payer's language (F-29): reasons, family messages, advice."""
from __future__ import annotations

import json
from functools import lru_cache

from .config import SERVICE_DIR

LOCALES = ("en", "hi")


@lru_cache
def _load(locale: str) -> dict:
    return json.loads((SERVICE_DIR / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))


def catalog(locale: str) -> dict:
    return _load(locale if locale in LOCALES else "en")


def norm_locale(locale: str | None) -> str:
    loc = (locale or "en").split("-")[0].split(",")[0].strip().lower()
    return loc if loc in LOCALES else "en"


def indian_number(n: float) -> str:
    """12,34,567 grouping."""
    n = int(round(n))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if n < 0 else "") + s


def duration(locale: str, seconds: float) -> str:
    d = catalog(locale)["durations"]
    if seconds < 90:
        return d["moments"]
    if seconds < 2 * 3600:
        return d["minutes"].format(n=int(round(seconds / 60)))
    if seconds < 36 * 3600:
        return d["hours"].format(n=int(round(seconds / 3600)))
    days = int(round(seconds / 86400))
    return d["day"] if days == 1 else d["days"].format(n=days)


def ordinal(locale: str, n: int) -> str:
    words = catalog(locale)["ordinals"]
    return words[n - 1] if 1 <= n <= len(words) else str(n)


def render_reason(locale: str, code: str, variant: str | None, evidence: dict) -> str:
    cat = catalog(locale)["reasons"]
    key = f"{code}.{variant}" if variant and f"{code}.{variant}" in cat else code
    tpl = cat.get(key, code)
    try:
        return tpl.format(**evidence)
    except (KeyError, IndexError):
        return tpl


def family(locale: str, code: str, variant: str | None = None) -> dict:
    """Text for a scam family. A variant replaces the headline or body when the usual wording would
    state something that was not observed (see core.explain.family_variant)."""
    fams = catalog(locale)["families"]
    f = fams.get(code) or fams["UNCLASSIFIED"]
    v = (f.get("variants") or {}).get(variant) if variant else None
    return {**f, **v} if v else f


def band_reason(locale: str, key: str | None) -> str | None:
    if not key:
        return None
    return catalog(locale)["band_reasons"].get(key)
