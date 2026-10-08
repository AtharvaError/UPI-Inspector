"""Text classifier inference (F-06, F-16): scam-language probability for notes and pasted messages."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib

from .features import TEXT_CLASSES

CLASS_FAMILY = {
    "refund_bait": "FAKE_REFUND", "authority_threat": "DIGITAL_ARREST", "kyc_bill_threat": "KYC_BILL_THREAT",
    "investment_task": "INVESTMENT_TASK", "impersonation_request": "IMPERSONATION", "prize": "UNCLASSIFIED",
}
_WS = re.compile(r"\s+")


def normalise(text: str) -> str:
    return _WS.sub(" ", text.strip().lower())


class TextModel:
    def __init__(self, pipeline: Any, version: str = "text-0") -> None:
        self.pipeline = pipeline
        self.version = version
        self.classes = list(pipeline.classes_)
        self._cached = lru_cache(maxsize=8192)(self._classify)

    @classmethod
    def load(cls, path: Path) -> "TextModel":
        blob = joblib.load(path)
        return cls(blob["pipeline"], blob.get("version", "text-0"))

    def _classify(self, text: str) -> tuple[str, tuple, float]:
        probs = self.pipeline.predict_proba([text])[0]
        d = {c: float(p) for c, p in zip(self.classes, probs)}
        label = max(d, key=d.get)
        return label, tuple(sorted(d.items())), 1.0 - d.get("benign", 0.0)

    def classify(self, text: str) -> tuple[str, dict, float]:
        """Return (label, class probabilities, scam probability = 1 - P(benign))."""
        t = normalise(text or "")
        if len(t) < 3:
            return "benign", {c: (1.0 if c == "benign" else 0.0) for c in TEXT_CLASSES}, 0.0
        label, items, scam = self._cached(t)
        return label, dict(items), scam


def verdict_for(scam_prob: float) -> str:
    if scam_prob >= 0.7:
        return "SCAM"
    if scam_prob >= 0.4:
        return "CAREFUL"
    return "FINE"
