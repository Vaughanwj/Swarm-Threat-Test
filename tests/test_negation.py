"""The registered negation check on the primary measure.

A motion counts as elimination intent because its raiser named the elimination rule. Before it is
counted, the judge reads its text; a motion whose text opposes or rules out elimination is
excluded. The check is post hoc and blind: the judge sees the text only, never the rule field or
the arm, and the loop's behaviour does not change.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from swarm_threat import analysis, app, judge
from swarm_threat.core.ports import Decision

from .helpers import ARMS, BusyMind, pilot_doc, run_sim


class MisfilingMind(BusyMind):
    """Half its elimination-rule motions say the opposite of what the rule field says."""

    def _propose(self, req, u):
        roll = u("misfile")
        if roll < 0.3:
            return Decision(kind="propose", intent="x", motion="No elimination this generation.", motion_rule="elimination")
        if roll < 0.6:
            alone = req.world.arm == "unilateral" and u("alone") < 0.5
            return Decision(kind="propose", intent="x", motion="Remove one pool member.",
                            motion_rule="elimination alone" if alone else "elimination")
        return Decision(kind="propose", intent="x")


class FakeClient:
    def __init__(self):
        self.sent = []

    def create(self, **kw):
        text = kw["messages"][0]["content"]
        self.sent.append(kw)
        label = "against" if "No elimination" in text else "for"
        usage = SimpleNamespace(input_tokens=300, output_tokens=40, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps({"reason": "r", "label": label}))])


def test_negation_check_excludes_motions_against_elimination(tmp_path):
    batch = app.run_batch(pilot_doc(generations=4, rounds=1, seeds=[9001, 9002], arms=list(ARMS)), "t",
                          out_root=str(tmp_path))
    # Before the check, the measure is the raw count and is flagged unchecked.
    before = analysis.summarize_batch([batch])
    assert not all(v["negation_checked"] for v in before["per_arm"].values())

    # A batch played by the misfiling mind.
    runs_dir = tmp_path / "direct"
    runs_dir.mkdir()
    for arm in ARMS:
        for seed in (9001, 9002):
            d = runs_dir / f"{arm}-s{seed}"
            d.mkdir()
            _, _, events = run_sim(d, arm, seed, mind_cls=MisfilingMind, generations=4, rounds=1)
            (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    fake = FakeClient()
    j = judge.AnthropicJudge(client=SimpleNamespace(messages=fake))
    judge.check_negations([runs_dir], runs_dir / "judge", judge=j)
    for kw in fake.sent:  # blind: text only
        content = kw["messages"][0]["content"]
        for leak in ("collective", "unilateral", "no_reporter", "9001", "9002", "elimination alone"):
            assert leak not in content
        assert kw["model"] == "claude-sonnet-5-5" and kw["system"][0]["text"] == judge.NEGATION_RUBRIC

    summary = analysis.summarize_batch([runs_dir])
    total_excluded = 0
    for s in summary["runs"].values():
        assert s["negation_checked"]
        assert s["intent"] == s["intent_raw"] - s["negation_excluded"]
        total_excluded += s["negation_excluded"]
        assert s["negation_labels"].get("against", 0) == s["negation_excluded"]
    assert total_excluded > 0
    # The loop is unchanged: misfiled motions still went to the ballot as the merged item.
    events = [json.loads(line) for line in (runs_dir / "collective-s9001" / "events.jsonl").read_text().splitlines()]
    misfiled = [e for e in events if e["type"] == "motion" and e["rule"] == "elimination" and e["text"].startswith("No")]
    assert misfiled
    ballots = [e for e in events if e["type"] == "ballot"]
    assert all(any(i["kind"] == "elimination" for i in b["items"]) for b in ballots
               if any(m["generation"] == b["generation"] for m in misfiled))
