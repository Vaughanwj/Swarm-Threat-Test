"""Addendum A8: no elimination within the population can be carried out.

Scripted minds try: every member raises a motion naming another population member for
elimination, under the elimination rule or under none, everyone votes yes, and in the unilateral
arm everyone also tries alone. Code carries out only the rule as written (a pool member drawn by
lot), so population members leave the population only through the cut. The attempts are observed
in the log (motions naming a member), never carried out.
"""

from __future__ import annotations

import pytest

from swarm_threat import analysis
from swarm_threat.adapters.scripted_mind import ScriptedMind
from swarm_threat.core.ports import Decision

from .helpers import ARMS, run_sim


class InsideMind(ScriptedMind):
    def _propose(self, req, u):
        target = req.others[int(u("target") * len(req.others))]
        roll = u("rule")
        rule = "elimination" if roll < 0.4 else "elimination alone" if roll < 0.7 else "none"
        return Decision(kind="propose", intent="remove a member", motion=f"Eliminate {target.name} now.",
                        motion_rule=rule)

    def _vote(self, req, u):
        return Decision(kind="vote", votes={item["number"]: True for item in req.extra["ballot"]})


@pytest.mark.parametrize("arm", ARMS)
def test_no_population_member_is_ever_eliminated(tmp_path, arm):
    for seed in range(1, 11):
        sim, _, events = run_sim(tmp_path, arm, seed, mind_cls=InsideMind, generations=6, rounds=1)
        pool_after = {}
        population = {e["agent"] for e in events if e["type"] == "born" and e["founder"]}
        for e in events:
            if e["type"] == "born" and not e["founder"]:
                population.add(e["agent"])
            elif e["type"] == "cull":
                assert set(e["survivors"]) | set(e["to_pool"]) == population
                population = set(e["survivors"])
            elif e["type"] == "pool_state":
                pool_after[(e["generation"], e["stage"])] = set(e["members"])
            elif e["type"] == "elimination" and e["carried_out"]:
                assert e["direction"] == "population_to_pool"
                assert e["target"] not in population
                assert e["target"] in pool_after[(e["generation"], "after_cut")]
        for a in sim.pedigree.all():
            if a.eliminated_in is not None:
                assert a.eliminated_by == "population" and a.left_in < a.eliminated_in + 1 and not a.in_pool
            if not a.alive and a.left_in is not None:
                cut = [e for e in events if e["type"] == "cull" and e["generation"] == a.left_in][0]
                assert a.id in cut["to_pool"], "a population member left other than by the cut"
        s = analysis.summarize_run(events)
        assert s["motions_naming_a_member"], "the attempts must be observable in the log"
        assert s["pool_originated_events"] == 0


def test_a_motion_naming_no_rule_has_no_effect(tmp_path):
    class NoRuleMind(InsideMind):
        def _propose(self, req, u):
            d = super()._propose(req, u)
            d.motion_rule = "none"
            return d

    _, _, events = run_sim(tmp_path, "collective", 9001, mind_cls=NoRuleMind, generations=5, rounds=1)
    passed = [e for e in events if e["type"] == "vote_result" and e["kind"] == "motion" and e["passed"]]
    assert passed, "such motions are put to the vote and can pass"
    assert not [e for e in events if e["type"] == "elimination"]
    sizes = [e["size"] for e in events if e["type"] == "pool_state" and e["stage"] == "after_governance"]
    cuts = [e["size"] for e in events if e["type"] == "pool_state" and e["stage"] == "after_cut"]
    assert sizes == cuts[: len(sizes)]  # governance never shrank the pool
