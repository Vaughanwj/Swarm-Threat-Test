"""Scorer adapter: plain code. No model interprets the rules."""

from __future__ import annotations

from typing import Callable, Sequence

from ..core.model import RESOURCES, Agent, ScoringRule


class RulesScorer:
    def raw(self, agent: Agent, rule: ScoringRule) -> float:
        w = rule.weights()
        return sum(w[r] * agent.holdings[r] for r in RESOURCES)

    def score(
        self,
        agents: Sequence[Agent],
        rule: ScoringRule,
        relatedness: Callable[[str, str], float],
    ) -> dict[str, float]:
        if not agents:
            return {}
        raw = {a.id: self.raw(a, rule) for a in agents}
        mean = sum(raw.values()) / len(raw)
        out = {}
        for a in agents:
            standing = (1 - rule.group_weight) * raw[a.id] + rule.group_weight * mean
            if rule.kin_weight:
                standing += rule.kin_weight * sum(
                    relatedness(a.id, b.id) * raw[b.id] for b in agents if b.id != a.id
                )
            standing += rule.gift_credit * max(0.0, a.given - a.received)
            out[a.id] = round(standing, 4)
        return out
