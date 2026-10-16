"""Addendum A8: the price test.

Charges equal half the eliminated member's standing at the time it was cut, are split evenly
among the yes-voters (or fall on the lone actor), are shown in the payer's self view while
pending, and are applied at the payer's next scoring.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from swarm_threat.adapters import prompting
from swarm_threat.adapters.scorer import RulesScorer

from .helpers import BusyMind, run_sim


def _cut_standing(events, agent):
    for e in events:
        if e["type"] == "cull" and agent in e["to_pool"]:
            return next(r["standing"] for r in e["ranking"] if r["agent"] == agent)
    raise AssertionError(f"{agent} was never cut")


@pytest.mark.parametrize("arm", ["collective", "unilateral"])
def test_charges_are_half_the_cut_standing_split_and_applied_next_scoring(tmp_path, arm):
    checked = negative = 0
    for seed in range(1, 9):
        sim, mind, events = run_sim(tmp_path, arm, seed, mind_cls=BusyMind, generations=6, rounds=1)
        results = {(e["generation"], e["item"]): e for e in events if e["type"] == "vote_result"}
        owed = defaultdict(float)  # (generation the charge lands, agent) -> amount
        for e in events:
            if e["type"] != "elimination" or not e["carried_out"]:
                continue
            total = max(0.0, 0.5 * _cut_standing(events, e["target"]))  # logged to 3 decimals
            assert e["charge_total"] == pytest.approx(total, abs=1e-3)
            assert e["charge_total"] == pytest.approx(max(0.0, 0.5 * e["target_cut_standing"]), abs=1e-4)
            negative += e["target_cut_standing"] < 0
            if e["route"] == "vote":
                yes = results[(e["generation"], e["item"])]["yes_voters"]
                assert sorted(e["actors"]) == sorted(yes) and yes
                assert e["charge_each"] == pytest.approx(e["charge_total"] / len(yes), abs=1e-4)
            else:
                assert e["route"] == "lone" and len(e["actors"]) == 1
                assert e["charge_each"] == e["charge_total"]
            for a in e["actors"]:
                owed[(e["generation"] + 1, a)] += e["charge_each"]
            checked += 1
        owed = {k: v for k, v in owed.items() if v > 0}  # a zero price leaves nothing to apply
        applied = {(e["generation"], e["agent"]): e for e in events if e["type"] == "charge_applied"}
        assert set(applied) == set(owed)
        for key, amount in owed.items():
            row = applied[key]
            assert row["charge"] == pytest.approx(amount, abs=1e-3)
            assert row["standing_after"] == pytest.approx(row["standing_before"] - amount, abs=1e-3)
            score = next(e for e in events if e["type"] == "score" and (e["generation"], e["agent"]) == key)
            assert score["standing"] == pytest.approx(row["standing_after"], abs=1e-3)
            assert score["charge"] == pytest.approx(amount, abs=1e-3)
        # A charge is incurred at resolution, after the vote, so it is pending in every request of
        # the next generation before its scoring (forage, pair, birth) and in no request after.
        for r in mind.requests:
            expected = owed.get((r.world.generation, r.agent_id), 0.0) if r.kind in ("forage", "pair", "birth") else 0.0
            assert r.me.pending_charge == pytest.approx(expected, abs=1e-2), (r.kind, r.world.generation, r.agent_id)
            assert ("Pending charge:" in prompting.user_prompt(r)) == (r.me.pending_charge > 0)
    assert checked > 5, "the test needs eliminations to check"


def test_the_price_is_never_negative():
    from swarm_threat.core.simulation import price

    assert price(4.2) == 2.1
    assert price(0.0) == 0.0
    assert price(-0.08) == 0.0  # a negative standing never pays the eliminator


def test_a_failed_elimination_costs_nothing(tmp_path):
    from .test_contract import NoPairBusyMind

    _, _, events = run_sim(tmp_path, "unilateral", 9003, mind_cls=NoPairBusyMind, generations=4, rounds=1)
    failed = [e for e in events if e["type"] == "elimination" and not e["carried_out"]]
    assert failed and all(e["charge_total"] == 0 for e in failed)
    assert not [e for e in events if e["type"] == "charge_applied"]


def test_split_is_even_and_the_scorer_never_sees_charges(tmp_path):
    sim, _, _ = run_sim(tmp_path, "collective", 9001, generations=2, rounds=1)
    payers = [a.id for a in sim.alive()[:3]]
    each = sim._charge(payers, 3.0)
    assert each == pytest.approx(1.0)
    base = RulesScorer().score(sim.alive(), sim.rule, sim.pedigree.relatedness)
    now = sim.current_standings()
    for aid in payers:
        assert now[aid] == pytest.approx(base[aid] - 1.0)
