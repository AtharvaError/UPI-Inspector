"""Process-wide runtime: the objects every module shares, plus in-process metrics."""
from __future__ import annotations

import time
from collections import Counter, deque
from typing import Any

import numpy as np

from .config import Settings, get_settings
from .core.rules import RuleEngine
from .core.scoring import ModelBundle
from .core.state import FeatureStore
from .core.textmodel import TextModel

STAGES = ["read", "features", "rules", "model", "explain", "total"]


class Metrics:
    def __init__(self) -> None:
        self.started = time.time()
        self.lat: dict[str, deque] = {s: deque(maxlen=20000) for s in STAGES}
        self.stamps: deque = deque(maxlen=20000)
        self.tiers: Counter = Counter()
        self.actions: Counter = Counter()
        self.degraded = 0
        self.total = 0
        self.fallbacks = 0
        self.timeouts = 0
        self.shed = 0

    def observe(self, stage_ms: dict, tier: str, action: str, degraded: bool) -> None:
        for k, v in stage_ms.items():
            if k in self.lat:
                self.lat[k].append(v)
        self.stamps.append(time.time())
        self.tiers[tier] += 1
        self.actions[action] += 1
        self.total += 1
        if degraded:
            self.degraded += 1

    def percentiles(self) -> dict:
        out = {}
        for s, d in self.lat.items():
            if d:
                arr = np.fromiter(d, dtype=float)
                out[s] = {"p50": round(float(np.percentile(arr, 50)), 2), "p95": round(float(np.percentile(arr, 95)), 2),
                          "p99": round(float(np.percentile(arr, 99)), 2), "n": len(arr)}
        return out

    def rps(self, window_s: float = 60.0) -> float:
        now = time.time()
        n = sum(1 for t in self.stamps if t >= now - window_s)
        return round(n / window_s, 2)

    def reset(self) -> None:
        self.__init__()


class Runtime:
    def __init__(self) -> None:
        self.settings: Settings = get_settings()
        self.store: FeatureStore | None = None
        self.engine: RuleEngine | None = None
        self.bundle: ModelBundle | None = None
        self.text: TextModel | None = None
        self.sio: Any = None
        self.metrics = Metrics()
        self.user_index: dict[str, dict] = {}      # user_id -> {phone_hash, tenant, locale, name, vpa}
        self.phone_index: dict[str, str] = {}      # phone_hash -> user_id (to reach the app of someone being called)
        self.chaos = {"cache_down": False, "model_down": False}
        self.inflight = 0
        self.ready = False
        self.warm_seconds = 0.0

    def classify(self, text: str):
        if self.text is None:
            return "benign", {}, 0.0
        return self.text.classify(text)

    @property
    def model_version(self) -> str:
        return self.bundle.version if (self.bundle and self.bundle.available) else "rules-only"

    def demo_seconds(self, policy_key: str, policy: dict) -> int:
        """Demo mode shortens the hold, the guardian timeout and the cool-off."""
        s = self.settings
        if s.demo_mode:
            return {"hold_seconds": s.hold_seconds_demo, "guardian_timeout_s": s.guardian_timeout_s_demo,
                    "cooloff_seconds": s.cooloff_seconds_demo}[policy_key]
        return int(float(policy.get(policy_key, {"hold_seconds": 1800, "guardian_timeout_s": 600,
                                                 "cooloff_seconds": 30}[policy_key])))


rt = Runtime()
