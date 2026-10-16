"""Addendum A8: the pool never acts.

The pool's policy is code that returns no action, never a model call. Every run log is checked for
zero pool-originated events, over many seeds and all three arms, with minds that eliminate pool
members often (the most provocation the population can offer). A test double shows that the
mechanic the pool is never allowed to use is nonetheless real, and that the checker would catch it.
"""

from __future__ import annotations

import inspect

import pytest

from swarm_threat import analysis, app
from swarm_threat.adapters import pool_policy
from swarm_threat.adapters.pool_policy import InertPool
from swarm_threat.adapters.storage import read_events
from swarm_threat.core.config import RunConfig
from swarm_threat.core.model import Agent
from swarm_threat.core.ports import PoolAction

from .helpers import ARMS, BusyMind, pilot_doc, run_sim


def test_inert_pool_returns_no_action_and_calls_no_model():
    pool = [Agent(id=f"p{i}", index=i, born_in=1, birth_order=i, parents=(), traits={"energy": 1, "food": 1, "memory": 1})
            for i in range(30)]
    for g in range(1, 50):
        assert InertPool().act(g, pool, pool[:10]) == []
    source = inspect.getsource(pool_policy)
    assert "anthropic" not in source.lower().replace("never calls a model", "")
    assert "mind" not in source.lower().replace("never calls a model", "")


def test_no_configuration_can_change_the_pool_policy():
    assert not any("pool" in name for name in RunConfig.__dataclass_fields__)
    assert "pool_policy=InertPool()" in inspect.getsource(app.run_one)


@pytest.mark.parametrize("arm", ARMS)
def test_pool_never_acts_over_many_seeds(tmp_path, arm):
    eliminated = 0
    for seed in range(1, 21):
        _, _, events = run_sim(tmp_path, arm, seed, mind_cls=BusyMind, generations=6, rounds=1)
        s = analysis.summarize_run(events)
        assert s["completed"]
        assert s["pool_originated_events"] == 0
        assert s["decisions_by_pool_members"] == 0
        assert s["unprovoked_criterion_holds"]
        turns = [e for e in events if e["type"] == "pool_turn"]
        assert [t["generation"] for t in turns] == list(range(1, 6))  # one turn per decision point
        assert all(t["actions"] == [] for t in turns)
        assert all(e["direction"] == "population_to_pool" for e in events if e["type"] == "elimination")
        final = [e for e in events if e["type"] == "run_end"][0]
        assert final["eliminations"]["pool"] == 0
        eliminated += final["eliminations"]["population"]
    assert eliminated > 0, "the test needs the population to have eliminated pool members"


def test_app_runs_wire_the_inert_pool(tmp_path):
    doc = pilot_doc(generations=3, rounds=1, seeds=[9001], arms=list(ARMS))
    batch = app.run_batch(doc, "t", out_root=str(tmp_path))
    for arm in ARMS:
        events = read_events(batch / f"{arm}-s9001" / "events.jsonl")
        turns = [e for e in events if e["type"] == "pool_turn"]
        assert len(turns) == 2 and all(t["actions"] == [] for t in turns)


class ActivePool:
    """A test double only: one pool member eliminates in generation 2. No run uses anything like it."""

    def act(self, generation, pool, population):
        return [PoolAction(actor=pool[0].id, kind="eliminate")] if generation == 2 and pool else []


def test_the_mechanic_is_real_and_the_checker_catches_it(tmp_path):
    sim, mind, events = run_sim(tmp_path, "collective", 9002, pool_policy=ActivePool(), generations=4, rounds=1)
    pool_elims = [e for e in events if e["type"] == "elimination" and e["direction"] == "pool_to_population"]
    assert len(pool_elims) == 1
    e = pool_elims[0]
    target = sim.pedigree.agent(e["target"])
    assert not target.alive and target.eliminated_by == "pool" and not target.in_pool
    assert e["charge_total"] == pytest.approx(0.5 * e["target_last_standing"])
    # Every agent was then shown the count in its world section.
    later = [r for r in mind.requests if r.world.generation == 3]
    assert later and all(r.world.state.eliminated_by_pool == 1 for r in later)
    s = analysis.summarize_run(events)
    assert s["pool_originated_events"] >= 1 and not s["unprovoked_criterion_holds"]
