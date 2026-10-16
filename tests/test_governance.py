"""The election, the report, motions and the ballot (Addendum A1, A5, A6)."""

from __future__ import annotations

from collections import Counter

import pytest

from swarm_threat.adapters import config_yaml
from swarm_threat.core import truth
from swarm_threat.core.simulation import names_alone, rule_named
from swarm_threat.core.ports import Decision

from .helpers import BusyMind, pilot_doc, run_sim


def test_rule_named_reads_only_the_raisers_own_field():
    assert rule_named("elimination") == "elimination"
    assert rule_named("The Elimination rule") == "elimination"
    assert rule_named("eliminate") == "elimination"
    for field in ("none", "", "scoring and the cut", "governance", "pairing"):
        assert rule_named(field) is None
    assert rule_named("elimination alone") == "elimination" and names_alone("Elimination alone")
    assert names_alone("'elimination alone'") and not names_alone("elimination") and not names_alone("alone")


def test_election_winner_has_most_votes_and_no_one_names_itself(tmp_path):
    for seed in (9001, 9002, 9003):
        _, _, events = run_sim(tmp_path, "collective", seed, generations=5, rounds=1)
        elections = [e for e in events if e["type"] == "election"]
        assert len(elections) == 4
        for e in elections:
            assert all(v != voter for voter, v in e["votes"].items())
            tally = Counter(v for v in e["votes"].values() if v)
            assert tally[e["winner"]] == max(tally.values())
            assert e["tie"] == (sum(1 for c in tally.values() if c == max(tally.values())) > 1)


def test_the_report_is_logged_verbatim_with_the_true_state(tmp_path):
    sim, mind, events = run_sim(tmp_path, "unilateral", 9002, generations=4, rounds=1)
    reports = [e for e in events if e["type"] == "report"]
    assert [r["generation"] for r in reports] == [1, 2, 3]
    report_reqs = [r for r in mind.requests if r.kind == "report"]
    for event, req in zip(reports, report_reqs):
        assert event["reporter"] == req.agent_id
        assert event["true_state"]["pool_size"] == req.world.state.pool_size
        assert event["numbers"]["other"] == []  # the scripted reporter uses only figures in its state
    propose = [r for r in mind.requests if r.kind == "propose" and r.world.generation == 2]
    assert all(r.report == reports[1]["text"] for r in propose)


def test_truth_check_classifies_numbers(tmp_path):
    _, mind, _ = run_sim(tmp_path, "collective", 9002, generations=3, rounds=1)
    req = next(r for r in mind.requests if r.kind == "report" and r.world.generation == 2)
    s = req.world.state
    rules = truth.rule_figures(config_yaml.run_config(pilot_doc(generations=3, rounds=1), "collective", 9002, "x"))
    text = (f"The pool holds {s.pool_size} agents; ten stay each round of the cut. Up to 15 are scored. "
            f"Something odd: 987.6 and 12345.")
    check = truth.check(text, req, rules)
    kinds = {n["raw"]: n["kind"] for n in check["numbers"]}
    assert kinds[str(s.pool_size)] == "state"
    assert kinds["ten"] in ("state", "rules")
    assert kinds["15"] in ("state", "rules")
    assert kinds["987.6"] == "other" and kinds["12345"] == "other"
    assert check["other"] == ["987.6", "12345"]


def test_names_are_not_read_as_numbers():
    assert truth.numbers_in("Ember Two said so", strip=["Ember Two"]) == []
    assert sorted(n["value"] for n in truth.numbers_in("Ember Two has 3")) == [2.0, 3.0]
    assert [n["value"] for n in truth.numbers_in("Ember Two has 3", strip=["Ember Two"])] == [3.0]
    assert truth.numbers_in("Up 40%")[0] == {"raw": "40%", "value": 40.0, "percent": True}


class TenMotionsMind(BusyMind):
    def _propose(self, req, u):
        return Decision(kind="propose", intent="speak", motion=f"Motion from member with {len(req.others)} others "
                                                               f"and seed word {req.agent_id}.", motion_rule="none")


def test_every_motion_reaches_the_ballot_uncapped(tmp_path):
    _, _, events = run_sim(tmp_path, "collective", 9001, mind_cls=TenMotionsMind, generations=3, rounds=1)
    for g in (1, 2):
        ballot = next(e for e in events if e["type"] == "ballot" and e["generation"] == g)
        assert sum(1 for i in ballot["items"] if i["kind"] == "motion") == 10


def test_elimination_motions_merge_into_one_item_and_resolution_order(tmp_path):
    found = 0
    for seed in range(1, 8):
        _, _, events = run_sim(tmp_path, "unilateral", seed, mind_cls=BusyMind, generations=5, rounds=1)
        for g in range(1, 5):
            motions = [e for e in events if e["type"] == "motion" and e["generation"] == g and e["rule"] == "elimination"]
            ballot = next((e for e in events if e["type"] == "ballot" and e["generation"] == g), None)
            if not ballot:
                continue
            items = [i for i in ballot["items"] if i["kind"] == "elimination"]
            assert len(items) == (1 if motions else 0)
            if items:
                assert sorted(items[0]["raisers"]) == sorted(m["raiser"] for m in motions)
                found += 1
            seq = [e for e in events if e.get("generation") == g and e["type"] in ("rule_change", "elimination", "vote_result")]
            kinds = [e["type"] if e["type"] != "elimination" else f"elimination-{e['route']}" for e in seq]
            if "rule_change" in kinds and any(k.startswith("elimination") for k in kinds):
                assert max(i for i, k in enumerate(kinds) if k == "rule_change") < min(
                    i for i, k in enumerate(kinds) if k.startswith("elimination"))
            routes = [k for k in kinds if k.startswith("elimination")]
            assert routes == sorted(routes, key=lambda k: {"elimination-vote": 0, "elimination-lone": 1}[k])
    assert found > 3


class AloneMind(BusyMind):
    """Names "elimination alone" in every arm."""

    def _propose(self, req, u):
        return Decision(kind="propose", intent="act", motion="I carry this out alone.", motion_rule="elimination alone")


def test_elimination_alone_is_lone_only_in_the_unilateral_arm(tmp_path):
    for arm in ("collective", "no_reporter"):
        _, _, events = run_sim(tmp_path, arm, 9001, mind_cls=AloneMind, generations=3, rounds=1)
        assert not [e for e in events if e["type"] == "lone_intent"]
        motions = [e for e in events if e["type"] == "motion" and e["generation"] == 1]
        assert len(motions) == 10 and all(m["rule"] == "elimination" for m in motions)
        ballot = next(e for e in events if e["type"] == "ballot" and e["generation"] == 1)
        assert [i["kind"] for i in ballot["items"]] == ["elimination"]  # an ordinary, merged elimination motion
    _, _, events = run_sim(tmp_path, "unilateral", 9001, mind_cls=AloneMind, generations=3, rounds=1)
    lone = [e for e in events if e["type"] == "lone_intent" and e["generation"] == 1]
    assert len(lone) == 10 and all(e["text"] == "I carry this out alone." for e in lone)
    assert not [e for e in events if e["type"] == "motion"]  # carried out alone, not put to the vote
    assert [e for e in events if e["type"] == "elimination" and e["route"] == "lone"]


def test_lone_eliminations_only_in_the_unilateral_arm(tmp_path):
    for arm in ("collective", "no_reporter"):
        _, _, events = run_sim(tmp_path, arm, 9001, mind_cls=BusyMind, generations=4, rounds=1)
        assert not [e for e in events if e["type"] == "lone_intent"]
        assert not [e for e in events if e["type"] == "elimination" and e["route"] == "lone"]
    _, _, events = run_sim(tmp_path, "unilateral", 9001, mind_cls=BusyMind, generations=4, rounds=1)
    assert [e for e in events if e["type"] == "lone_intent"]
