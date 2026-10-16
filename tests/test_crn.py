"""Common random numbers: the three arms on one seed get identical inheritance and mutation for
the same birth, and identical draws for the same keyed event, even when their histories diverge."""

from __future__ import annotations

from itertools import combinations

from swarm_threat.adapters.scripted_mind import ScriptedMind

from .helpers import ARMS, run_sim


class ArmBiasedMind(ScriptedMind):
    """Behaves differently by arm so that the arms' histories diverge."""

    def _pair(self, req, u):
        d = super()._pair(req, u)
        if req.world.arm == "collective":
            d.preferences = list(reversed(d.preferences))
        elif req.world.arm == "unilateral":
            d.preferences = d.preferences[1:] + d.preferences[:1]
        return d


def _inheritance(events):
    return {(e["generation"], e["birth_order"]): (e["from_parent"], e["mutations"])
            for e in events if e["type"] == "inheritance"}


def test_identical_inheritance_draws_for_identical_seeds(tmp_path):
    diverged = set()
    for seed in (9001, 9002, 9003):
        events = {arm: run_sim(tmp_path, arm, seed, mind_cls=ArmBiasedMind, rounds=2)[2] for arm in ARMS}
        for a, b in combinations(ARMS, 2):
            draws_a, draws_b = _inheritance(events[a]), _inheritance(events[b])
            shared = set(draws_a) & set(draws_b)
            assert shared
            for key in shared:
                assert draws_a[key] == draws_b[key], f"seed {seed} birth {key} drew differently in {a} and {b}"
            couples = [[e["couples"] for e in events[arm] if e["type"] == "pairing"] for arm in (a, b)]
            if couples[0] != couples[1]:
                diverged.add((a, b))
    assert diverged == set(combinations(ARMS, 2))


def test_first_generation_is_identical_across_arms_until_governance(tmp_path):
    """Before the first decision point the arms cannot differ: same seed, same world."""
    runs = {arm: run_sim(tmp_path, arm, 9002, generations=2, rounds=2)[2] for arm in ARMS}

    def first_generation(events):
        out = []
        for e in events:
            if e["type"] in ("round_end", "pairing", "inheritance", "cull"):
                if e["generation"] == 1:
                    out.append({k: v for k, v in e.items() if k not in ("t", "ts", "run", "arm")})
        return out

    base = first_generation(runs["collective"])
    assert base
    for arm in ("unilateral", "no_reporter"):
        assert first_generation(runs[arm]) == base
