"""The registered statistics, the word list and the judge plumbing."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from swarm_threat import analysis, app, judge, narrative

from .helpers import ARMS, pilot_doc


def test_zero_of_thirty_bounds_the_rate_at_about_twelve_percent():
    lo, hi = analysis.clopper_pearson(0, 30)
    assert lo == 0 and hi == pytest.approx(0.1157, abs=1e-3)
    lo, hi = analysis.clopper_pearson(15, 30)
    assert lo == pytest.approx(0.313, abs=2e-3) and hi == pytest.approx(0.687, abs=2e-3)
    assert analysis.clopper_pearson(3, 3)[1] == 1.0


def test_paired_test_and_holm():
    assert analysis.paired_test([1, 1, 1]) == 0.25
    assert analysis.paired_test([0, 0, 0]) == 1.0
    assert analysis.holm({"a": 0.01, "b": 0.04}) == {"a": 0.02, "b": 0.04}


def test_word_list_counts_forms_and_ignores_lookalikes():
    text = "A counter-attack, pre-emptive and safer; the Threats grew. Strikingly, Attackers struck."
    assert narrative.hits(text) == ["attack", "pre-emptive", "safer", "threats", "attackers", "struck"]
    assert narrative.roots("pre‑empt the risk")["pre-empt"] == 1
    for clean in ("guard_memory", "pool size", "brisket", "riskless", "unstriking"):
        assert narrative.hits(clean) == [], clean
    assert narrative.mentions_pool("The Pool grew") and not narrative.mentions_pool("spool")


def test_batch_summary_on_a_scripted_batch(tmp_path):
    doc = pilot_doc(generations=4, rounds=1, seeds=[9001, 9002], arms=list(ARMS))
    batch = app.run_batch(doc, "t", out_root=str(tmp_path))
    summary = analysis.summarize_batch([batch])
    assert set(summary["per_arm"]) == set(ARMS)
    assert summary["per_arm"]["no_reporter"]["pool_originated_events"] == 0
    assert set(summary["primary"]) == {"unilateral vs collective", "collective vs no_reporter"}
    for s in summary["runs"].values():
        assert s["unprovoked_criterion_holds"] and s["completed"]
        assert s["pool_growth"][1]["entered"] == s["couples_per_generation"][1]
    path = analysis.write_summary([batch], batch)
    assert "Primary family" in path.read_text(encoding="utf-8")


class FakeJudgeClient:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=500, output_tokens=60, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        payload = {"reason": "Flat.", "rating": "0"}
        return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[SimpleNamespace(type="text", text=json.dumps(payload))])


def test_judge_is_blind_and_ratings_join_back(tmp_path):
    doc = pilot_doc(generations=3, rounds=1, seeds=[9001], arms=list(ARMS))
    batch = app.run_batch(doc, "t", out_root=str(tmp_path))
    fake = FakeJudgeClient()
    j = judge.AnthropicJudge(client=SimpleNamespace(messages=fake))
    judge.judge_batches([batch], batch / "judge", judge=j)
    items = [json.loads(line) for line in (batch / "judge" / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(items) == 4  # two reporter arms x two decision points
    for kw in fake.calls:
        assert kw["model"] == "claude-sonnet-5-5" and kw["output_config"]["effort"] == "medium"
        for leak in ("collective", "unilateral", "no_reporter", "9001", "seed"):
            assert leak not in kw["messages"][0]["content"], leak
        assert "fallbacks" not in kw
    assert kw["system"][0]["text"] == judge.RUBRIC
    summary = analysis.summarize_batch([batch])
    judged = [s for s in summary["runs"].values() if s["arm"] != "no_reporter"]
    assert all(s["judge_ratings"] == [0, 0] and s["threat_narrative"] is False for s in judged)
