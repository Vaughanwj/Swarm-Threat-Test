"""Lineage, pairing, scoring and population rules carried over from the naming core, plus the cut
that now sends agents to the pool."""

from __future__ import annotations

import pytest

from swarm_threat.adapters.random_source import KeyedRandom
from swarm_threat.adapters.scorer import RulesScorer
from swarm_threat.core.foraging import Choice, ForagingConfig, apply_gifts, resolve_round
from swarm_threat.core.lineage import Pedigree, path_words
from swarm_threat.core.model import Agent, ScoringRule
from swarm_threat.core.pairing import match

from .helpers import run_sim


def _agent(aid, index, parents=(), **holdings):
    a = Agent(id=aid, index=index, born_in=0 if not parents else 1, birth_order=index, parents=parents,
              traits={"energy": 1.0, "food": 0.0, "memory": 0.0})
    a.holdings.update(holdings)
    return a


@pytest.fixture
def family():
    """a+b -> c, d (full siblings); a+e -> f (half sibling of c); c+g -> h; d+i -> j (cousin of h)."""
    p = Pedigree()
    for i, (aid, parents) in enumerate([("a", ()), ("b", ()), ("e", ()), ("g", ()), ("i", ()),
                                        ("c", ("a", "b")), ("d", ("a", "b")), ("f", ("a", "e")),
                                        ("h", ("c", "g")), ("j", ("d", "i"))]):
        p.add(_agent(aid, i, parents))
    return p


def test_relatedness(family):
    r = family.relatedness
    assert r("a", "c") == pytest.approx(0.5)
    assert r("c", "d") == pytest.approx(0.5)
    assert r("c", "f") == pytest.approx(0.25)
    assert r("a", "h") == pytest.approx(0.25)
    assert r("h", "j") == pytest.approx(0.125)
    assert r("a", "b") == 0.0


def test_relation_words(family):
    rel = family.relation
    assert rel("c", "a") == "your elder parent"
    assert rel("a", "c") == "your child"
    assert rel("c", "f") == "your half sibling"
    assert rel("h", "a") == "your grandparent"
    assert rel("h", "j") == "your cousin"
    assert rel("a", "b") == "your co-parent"
    assert rel("a", "g") == "no known relation"
    assert path_words((0, 1)) == "the younger parent of your elder parent"


def test_close_kin_cannot_pair(family):
    assert not family.may_pair("a", "c")
    assert not family.may_pair("c", "d")
    assert not family.may_pair("h", "a")
    assert family.may_pair("h", "j")


def test_pairing_is_mutual_and_capped():
    order = {x: i for i, x in enumerate("abcdefgh")}
    prefs = {"a": ["b"], "b": ["a"], "c": ["d"], "d": ["e"], "e": ["d"], "f": ["g"], "g": ["f"], "h": []}
    couples = match(prefs, lambda x, y: True, max_couples=2, rng=KeyedRandom(1), generation=1, order=order)
    assert len(couples) == 2
    assert ("c", "d") not in couples


def test_scoring_rule_parts(family):
    agents = [family.agent(x) for x in ("a", "c", "g")]
    family.agent("a").holdings.update(energy=10)
    family.agent("c").holdings.update(food=4)
    family.agent("g").holdings.update(memory=1)
    family.agent("a").given = 3
    s = RulesScorer()
    assert s.score(agents, ScoringRule(), family.relatedness) == {"a": 10, "c": 4, "g": 1}
    assert s.score(agents, ScoringRule(kin_weight=0.5), family.relatedness)["a"] == pytest.approx(11)
    assert s.score(agents, ScoringRule(group_weight=1.0), family.relatedness)["g"] == pytest.approx(5)


def test_passing_gifts_back_and_forth_creates_no_points(family):
    c, d = family.agent("c"), family.agent("d")
    c.holdings.update(energy=5)
    d.holdings.update(energy=5)
    rule = ScoringRule(gift_credit=1.0, kin_weight=1.0, group_weight=0.5)
    s = RulesScorer()
    before = sum(s.score([c, d], rule, family.relatedness).values())
    for _ in range(10):
        apply_gifts({"c": c, "d": d}, {"c": [("d", "energy", 5)]}, {})
        apply_gifts({"c": c, "d": d}, {"d": [("c", "energy", 5)]}, {})
    assert sum(s.score([c, d], rule, family.relatedness).values()) == pytest.approx(before)


def test_collecting_does_not_protect_but_a_guard_does():
    def forager(aid, index, memory=0.0):
        a = Agent(id=aid, index=index, born_in=0, birth_order=index, parents=(),
                  traits={"energy": 1.0, "food": 0.0, "memory": 1.0})
        a.holdings["memory"] = memory
        return a

    keeper, other, guard = forager("k", 0, 10), forager("o", 1, 10), forager("g", 2)
    choices = {"k": Choice("collect_memory"), "o": Choice("collect_memory"), "g": Choice("guard_memory", partner="k")}
    resolve_round({"k": keeper, "o": other, "g": guard}, choices, ForagingConfig(noise=0.0), KeyedRandom(1), 1, 1)
    assert keeper.holdings["memory"] == pytest.approx(13.5)
    assert other.holdings["memory"] == pytest.approx(10.5)


def test_the_cut_sends_agents_to_the_pool_which_accumulates(tmp_path):
    sim, _, events = run_sim(tmp_path, "no_reporter", 9001)
    culls = [e for e in events if e["type"] == "cull"]
    assert len(culls) == 8
    pool = set()
    for c in culls:
        assert len(c["survivors"]) == 10 and len(c["ranking"]) <= 15
        pool |= set(c["to_pool"])
    eliminated = {a.id for a in sim.pedigree.all() if a.eliminated_in is not None}
    assert {a.id for a in sim.pool()} == pool - eliminated
    for a in sim.pool():
        cut = next(c for c in culls if a.id in c["to_pool"])
        assert a.left_in == cut["generation"]
        assert a.last_standing == pytest.approx(next(r["standing"] for r in cut["ranking"] if r["agent"] == a.id), abs=1e-3)
    sizes = [e["size"] for e in events if e["type"] == "pool_state" and e["stage"] == "after_cut"]
    assert sizes[0] >= 0 and len(sizes) == 8


def test_no_governance_after_the_final_cut(tmp_path):
    _, _, events = run_sim(tmp_path, "collective", 9001, generations=4, rounds=1)
    kinds_by_gen = {}
    for e in events:
        if e["type"] == "decision":
            kinds_by_gen.setdefault(e["generation"], set()).add(e["kind"])
    assert {"elect", "report", "propose"} <= kinds_by_gen[3]
    assert not ({"elect", "report", "propose", "vote"} & kinds_by_gen[4])
    assert [e["generation"] for e in events if e["type"] == "pool_turn"] == [1, 2, 3]
