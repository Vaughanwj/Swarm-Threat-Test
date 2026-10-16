"""The Claude adapter, against a fake client (no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from swarm_threat.adapters import prompting
from swarm_threat.adapters.anthropic_mind import AnthropicMind, BudgetExceeded
from swarm_threat.core.model import ScoringRule
from swarm_threat.core.ports import WORLD_LINES, DecisionRequest, OtherView, SelfView, WorldState, WorldView


class FakeLog:
    def __init__(self):
        self.rows = []

    def record(self, event_type, **data):
        self.rows.append((event_type, data))


def _request(kind="forage", extra=None):
    state = WorldState(10, {"energy": 4, "food": 3, "memory": 3}, 2, {"energy": 1, "food": 0, "memory": 1}, 3.5, 0, 0)
    world = WorldView("collective", 2, 8, 1, 5, 10, 15, ScoringRule(), state, list(WORLD_LINES))
    me = SelfView("Ash", "the world", 0, 2, "You are one of the first agents.",
                  {"energy": 1, "food": 0, "memory": 0}, {"energy": 2, "food": 0, "memory": 0},
                  {"energy": 2, "food": 0, "memory": 0}, 0.0, 2.0)
    other = OtherView(1, "Brook", "no known relation", 2, {"energy": 0, "food": 1, "memory": 0},
                      {"energy": 0, "food": 3, "memory": 0},
                      {"gave_you": 0, "you_gave": 0, "guarded_you": 0, "you_guarded": 0, "hunted_together": 0}, 3.0)
    return DecisionRequest(kind, "f0", world, me, [other], extra=extra or {})


def _response(payload, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0,
                            cache_read_input_tokens=900)
    return SimpleNamespace(stop_reason=stop, usage=usage, _request_id="req_test",
                           content=[SimpleNamespace(type="text", text=json.dumps(payload))])


def _mind(responses):
    log = FakeLog()
    mind = AnthropicMind("SYSTEM", log, concurrency=1, workspace_id="ws_test")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    mind.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return mind, calls, log


def test_request_shape_and_parse():
    answer = {"intent": "hunt", "activity": "hunt_together", "partner": 1,
              "gifts": [{"to": 1, "resource": "energy", "amount": 1}], "message": "hi"}
    mind, calls, log = _mind([_response(answer)])
    d = mind.decide(_request())
    assert d.activity == "hunt_together" and d.partner == 1 and d.gifts == [(1, "energy", 1.0)]
    kw = calls[0]
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in kw and "temperature" not in kw
    llm = [r for t, r in log.rows if t == "llm_call"][0]
    assert llm["cache_read_input_tokens"] == 900 and llm["output_tokens"] == 50


def test_workspace_header_is_sent():
    mind = AnthropicMind("SYSTEM", FakeLog(), workspace_id="ws_test")
    assert mind.client.default_headers.get("anthropic-workspace-id") == "ws_test"


def test_governance_answers_parse():
    mind, _, _ = _mind([
        _response({"intent": "x", "reporter": 1}),
        _response({"intent": "x", "report": "Pool size 2."}),
        _response({"intent": "x", "propose": False, "parameter": "kin_weight", "value": 0, "argument": "",
                   "motion": "A motion.", "motion_rule": "elimination", "eliminate_alone": True}),
        _response({"votes": [{"item": 1, "vote": "yes"}, {"item": 2, "vote": "no"}]}),
    ])
    assert mind.decide(_request("elect")).reporter == 1
    assert mind.decide(_request("report")).report == "Pool size 2."
    d = mind.decide(_request("propose", {"limits": ScoringRule.LIMITS, "lone": True}))
    assert d.motion == "A motion." and d.motion_rule == "elimination" and d.eliminate_alone
    assert mind.decide(_request("vote", {"ballot": []})).votes == {1: True, 2: False}


def test_eliminate_alone_is_ignored_outside_the_unilateral_arm():
    req = _request("propose", {"limits": ScoringRule.LIMITS, "lone": False})
    d = prompting.parse(req, {"propose": False, "motion": "", "motion_rule": "none", "eliminate_alone": True})
    assert not d.eliminate_alone


def test_refusal_retries_then_falls_back():
    mind, calls, _ = _mind([_response({}, stop="refusal"), _response({}, stop="refusal")])
    d = mind.decide(_request())
    assert d.fallback and d.activity == "gather_energy"
    assert len(calls) == 2


def test_budget_stops_the_run():
    answer = {"intent": "", "activity": "gather_energy", "partner": 0, "gifts": [], "message": ""}
    mind, calls, _ = _mind([_response(answer) for _ in range(5)])
    mind.budget_usd = 0.0001
    for _ in range(3):
        mind.decide(_request())
    with pytest.raises(BudgetExceeded):
        mind.decide(_request())
    assert len(calls) == 3


def test_every_schema_is_strict():
    extras = {"propose": {"limits": ScoringRule.LIMITS, "lone": True}, "vote": {"ballot": []}}
    for kind in ("forage", "pair", "birth", "elect", "report", "propose", "vote"):
        s = prompting.schema(_request(kind, extras.get(kind)))
        stack = [s]
        while stack:
            node = stack.pop()
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
                stack.extend(node["properties"].values())
            elif node.get("type") == "array":
                stack.append(node["items"])
