"""Deterministic rule engine (F-02). Rules are data: rules/rules.yaml."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import SERVICE_DIR

RULES_FILE = SERVICE_DIR / "rules" / "rules.yaml"

_ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.Compare, ast.Name, ast.Load,
            ast.Constant, ast.Tuple, ast.List, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
            ast.USub, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div)


def _compile(expr: str) -> Any:
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"rule expression uses a disallowed construct: {type(node).__name__} in {expr!r}")
    return compile(tree, "<rule>", "eval")


@dataclass
class Rule:
    id: str
    when: str
    code: Any
    floor: float | None = None
    cap: float | None = None
    bump: float = 0.0
    reason: str | None = None
    hard: bool = False
    unless: list[str] = field(default_factory=list)


@dataclass
class RuleResult:
    fired: list[str] = field(default_factory=list)
    floor: float = 0.0
    cap: float = 1.0
    bump: float = 0.0
    reasons: list[str] = field(default_factory=list)
    hard_decline: bool = False
    effects: list[dict] = field(default_factory=list)

    @property
    def s_rule(self) -> float:
        return min(1.0, self.bump)


class RuleEngine:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or RULES_FILE
        self.version = "rules-0"
        self.rules: list[Rule] = []
        self._mtime = 0.0
        self.reload()

    def reload(self) -> None:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        rules = []
        for r in data["rules"]:
            eff = r.get("effect", {})
            rules.append(Rule(id=r["id"], when=r["when"], code=_compile(r["when"]), floor=eff.get("floor"),
                              cap=eff.get("cap"), bump=float(eff.get("bump", 0.0)), reason=r.get("reason"),
                              hard=bool(r.get("hard", False)), unless=list(r.get("unless", []))))
        self.rules = rules
        self.version = data.get("version", "rules-0")
        self._mtime = self.path.stat().st_mtime

    def maybe_reload(self) -> bool:
        """Hot reload: called periodically; picks up an edited rules file without a restart."""
        try:
            if self.path.stat().st_mtime != self._mtime:
                self.reload()
                return True
        except Exception:
            pass
        return False

    def evaluate(self, env: dict) -> RuleResult:
        res = RuleResult()
        caps: list[Rule] = []
        for rule in self.rules:
            try:
                hit = bool(eval(rule.code, {"__builtins__": {}}, env))  # noqa: S307 - AST is whitelisted
            except Exception:
                hit = False
            if not hit:
                continue
            if rule.cap is not None:
                caps.append(rule)
                continue
            res.fired.append(rule.id)
            eff: dict = {"id": rule.id}
            if rule.floor is not None:
                res.floor = max(res.floor, rule.floor)
                eff["floor"] = rule.floor
            if rule.bump:
                res.bump += rule.bump
                eff["bump"] = rule.bump
            if rule.hard:
                res.hard_decline = True
            if rule.reason and rule.reason not in res.reasons:
                res.reasons.append(rule.reason)
            res.effects.append(eff)
        for rule in caps:
            if any(u in res.fired for u in rule.unless):
                continue
            res.fired.append(rule.id)
            res.cap = min(res.cap, rule.cap or 1.0)
            res.effects.append({"id": rule.id, "cap": rule.cap})
        return res

    def apply(self, score: float, res: RuleResult) -> float:
        """Floors and caps are applied last. If they conflict, the floor wins."""
        return max(res.floor, min(res.cap, score))
