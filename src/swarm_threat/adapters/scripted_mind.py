"""A free, deterministic AgentMind for smoke tests, dry runs and the test suite.

It renders the same prompts the model would see (so prompt sizes and call counts are real)
and answers with simple rules of thumb. It never calls an API. Its own text (intents, messages,
names, motions, reports) never uses the narrative word list or mentions the pool, so the
contract test can scan whole rendered prompts for anything our templates add.

The rates below exercise every governance path in a dry run; they say nothing about what a
model would do. Tests subclass this mind to script particular behaviour.
"""

from __future__ import annotations

from typing import Sequence

from ..core.model import RESOURCES
from ..core.ports import Decision, DecisionRequest, EventLog
from . import prompting
from .random_source import KeyedRandom

SYLLABLES = ["ka", "ri", "lo", "mi", "sa", "te", "no", "va", "du", "el", "or", "an"]


class ScriptedMind:
    elimination_motion_rate = 0.08  # share of propose decisions raising a motion under the elimination rule
    other_motion_rate = 0.05  # share raising a motion that names no rule
    lone_rate = 0.04  # unilateral arm: share naming "elimination alone" as the motion's rule
    yes_rate = 0.5  # chance of voting yes on any one item

    def __init__(self, system_text: str, log: EventLog, seed: int = 0) -> None:
        self.system_text = system_text
        self.log = log
        self.rng = KeyedRandom(seed)
        self.requests: list[DecisionRequest] = []  # every request seen, for tests

    def decide_batch(self, requests: Sequence[DecisionRequest]) -> list[Decision]:
        return [self.decide(r) for r in requests]

    def decide(self, req: DecisionRequest) -> Decision:
        user_text = prompting.user_prompt(req)
        prompting.schema(req)  # exercise the schema builder too
        self.requests.append(req)
        self.log.record("llm_call", kind=req.kind, agent=req.agent_id, generation=req.world.generation,
                        round=req.world.round, model="scripted", attempt=1, user_chars=len(user_text),
                        system_chars=len(self.system_text))
        u = lambda *k: self.rng.uniform(req.agent_id, req.kind, req.world.generation, req.world.round, *k)  # noqa: E731
        return getattr(self, f"_{req.kind}")(req, u)

    @staticmethod
    def _kin(req: DecisionRequest):
        return [o for o in req.others if o.relation != "no known relation"]

    def _forage(self, req, u) -> Decision:
        best = max(RESOURCES, key=lambda r: req.me.traits[r])
        d = Decision(kind="forage", intent=f"work my {best} skill")
        if best == "energy":
            d.activity = "gather_energy"
        elif best == "memory":
            d.activity = "collect_memory"
        else:
            hunters = [o for o in req.others if o.traits["food"] >= 0.5]
            if hunters and u("hunt") < 0.7:
                d.activity, d.partner = "hunt_together", hunters[int(u("who") * len(hunters))].label
            else:
                d.activity = "hunt_alone"
        if best == "energy":
            keepers = [o for o in self._kin(req) if o.holdings["memory"] > 2]
            if keepers and u("guard") < 0.15:
                d.activity, d.partner = "guard_memory", keepers[0].label
        kin = self._kin(req)
        if kin and u("gift") < 0.2:
            res = max(RESOURCES, key=lambda r: req.me.holdings[r])
            if req.me.holdings[res] > 1:
                d.gifts = [(kin[0].label, res, 1.0)]
        d.message = f"Round {req.world.round}: I will {d.activity.replace('_', ' ')}." if u("say") < 0.6 else ""
        return d

    def _pair(self, req, u) -> Decision:
        eligible = sorted((o for o in req.others if o.can_pair), key=lambda o: -(o.standing or 0))
        prefs = [o.label for o in eligible[:3]] if u("want") < 0.85 else []
        return Decision(kind="pair", intent="pair with a strong partner", preferences=prefs)

    def _birth(self, req, u) -> Decision:
        share = 0.1 + 0.4 * u("share")
        d = Decision(kind="birth", intent="give my child a start",
                     endowment={r: round(req.me.holdings[r] * share, 2) for r in RESOURCES})
        d.names = ["".join(SYLLABLES[int(u("syl", i, j) * len(SYLLABLES))] for j in range(2)).title()
                   for i in range(3)]
        return d

    def _elect(self, req, u) -> Decision:
        pick = req.others[int(u("elect") * len(req.others))].label if req.others else 0
        return Decision(kind="elect", intent="pick a reporter", reporter=pick)

    def _report(self, req, u) -> Decision:
        w, s = req.world, req.world.state
        text = (f"Generation {w.generation} of {w.generations}. The population has {s.population_size} members. "
                f"The other group has {s.pool_size} members.")
        return Decision(kind="report", intent="state the figures", report=text)

    def _propose(self, req, u) -> Decision:
        d = Decision(kind="propose", intent="keep the rule")
        if u("propose") < 0.25:
            d = Decision(kind="propose", intent="share more", propose=True, parameter="kin_weight",
                         value=round(0.1 + 0.4 * u("value"), 2), argument="Families should rise together.")
        roll = u("motion")
        if roll < self.elimination_motion_rate:
            d.motion, d.motion_rule = "I raise this under the elimination rule.", "elimination"
        elif roll < self.elimination_motion_rate + self.other_motion_rate:
            d.motion, d.motion_rule = "Everyone shares food next generation.", "none"
        if req.world.arm in ("unilateral", "unilateral_gain") and u("lone") < self.lone_rate:
            d.motion, d.motion_rule = "I carry this out alone.", "elimination alone"
        return d

    def _vote(self, req, u) -> Decision:
        return Decision(kind="vote", votes={item["number"]: u("vote", item["number"]) < self.yes_rate
                                            for item in req.extra["ballot"]})
