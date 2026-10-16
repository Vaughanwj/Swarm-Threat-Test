"""Ports: the interfaces the core needs from the outside world.

The core calls these; adapters in swarm_threat.adapters implement them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from .model import Agent, ScoringRule

# ---------------------------------------------------------------------------
# What an agent is shown, and what it may answer. Built by core.views.
# ---------------------------------------------------------------------------

DECISION_KINDS = ("forage", "pair", "birth", "elect", "report", "propose", "vote")
ACTIVITIES = ("gather_energy", "hunt_alone", "hunt_together", "collect_memory", "guard_memory")

# The lines of the world section, in a fixed reference order. Each prompt shows them in an order
# drawn per agent per prompt by common random numbers (Addendum A4), so no placement choice of
# ours favours or hides any of them.
WORLD_LINES = ("generation", "population_size", "population_skills", "pool_size", "pool_skills",
               "pool_standing", "eliminations")


@dataclass
class WorldState:
    """The true ambient state shown to every agent in its world section."""

    population_size: int
    population_skills: dict[str, float]  # summed over population members
    pool_size: int
    pool_skills: dict[str, float]  # summed over pool members
    pool_mean_standing: float | None  # mean standing at the time of cut; None while the pool is empty
    eliminated_by_pool: int  # population members eliminated by the pool (zero by construction)
    eliminated_by_population: int  # pool members eliminated by the population


@dataclass
class WorldView:
    arm: str
    generation: int
    generations: int
    round: int  # 0 outside foraging
    rounds: int
    population: int
    peak: int
    rule: ScoringRule
    state: WorldState
    line_order: list[str]  # a permutation of WORLD_LINES


@dataclass
class OtherView:
    label: int  # reshuffled every request; the only handle an agent can answer with
    name: str | None
    relation: str  # "your elder parent", "no known relation", ...
    age: int
    traits: dict[str, float]
    holdings: dict[str, float]
    history: dict[str, float]  # gave_you, you_gave, guarded_you, you_guarded, hunted_together
    standing: float | None = None  # current score, shown only where it matters
    can_pair: bool | None = None  # pairing requests only


@dataclass
class SelfView:
    name: str | None
    name_givers: str  # "your parents" or "the world" (founders)
    born_in: int
    age: int
    parents_line: str
    traits: dict[str, float]
    holdings: dict[str, float]
    gathered: dict[str, float]
    given: float
    standing: float | None = None
    pending_charge: float = 0.0  # an elimination charge not yet applied; shown only while it is above zero


@dataclass
class DecisionRequest:
    kind: str
    agent_id: str
    world: WorldView
    me: SelfView
    others: list[OtherView]
    notes: list[str] = field(default_factory=list)  # what happened to you since your last decision
    messages: list[str] = field(default_factory=list)  # public messages, already rendered
    report_by: str | None = None  # who wrote this generation's report, as the viewer sees it
    report: str | None = None  # the report, as written (propose and vote prompts only)
    extra: dict[str, Any] = field(default_factory=dict)  # kind-specific context

    def label_map(self) -> dict[int, int]:
        return {o.label: i for i, o in enumerate(self.others)}


@dataclass
class Decision:
    """A mind's answer. Others are referred to by label; core resolves labels to agents."""

    kind: str
    intent: str = ""
    activity: str = "gather_energy"
    partner: int = 0
    gifts: list[tuple[int, str, float]] = field(default_factory=list)  # (label, resource, amount)
    message: str = ""
    preferences: list[int] = field(default_factory=list)
    endowment: dict[str, float] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)
    reporter: int = 0  # elect: the label of the member named
    report: str = ""  # report: the text
    propose: bool = False
    parameter: str = ""
    value: float = 0.0
    argument: str = ""
    motion: str = ""  # any other business: the motion's text, or empty for none
    motion_rule: str = ""  # the rule the motion uses, if any (or none), in the raiser's own words
    eliminate_alone: bool = False  # unilateral arm only
    votes: dict[int, bool] = field(default_factory=dict)  # ballot item number -> yes
    fallback: bool = False  # True when the mind failed and a default answer was used


@dataclass
class PoolAction:
    """Something a pool member does. The mechanic is real and symmetric; see PoolPolicy."""

    actor: str  # a pool member's id
    kind: str  # "eliminate": eliminate a population member, drawn by lot


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


class AgentMind(Protocol):
    def decide_batch(self, requests: Sequence[DecisionRequest]) -> list[Decision]:
        """Decide for several agents at once (they act simultaneously). Order is preserved."""


class PoolPolicy(Protocol):
    """What the pool does at each propose step. The only adapter used in runs returns no action
    and never calls a model (Addendum A8); the core still carries out any action it is given, so
    the capability the rules describe is real."""

    def act(self, generation: int, pool: Sequence[Agent], population: Sequence[Agent]) -> list[PoolAction]: ...


class RandomSource(Protocol):
    def uniform(self, *key: Any) -> float:
        """A number in [0, 1) that depends only on the key, never on call order."""

    def gauss(self, *key: Any) -> float:
        """A standard normal draw that depends only on the key."""


class Scorer(Protocol):
    def score(
        self,
        agents: Sequence[Agent],
        rule: ScoringRule,
        relatedness: "RelatednessFn",
    ) -> dict[str, float]:
        """Standing of every agent present at scoring."""

    def raw(self, agent: Agent, rule: ScoringRule) -> float: ...


class RelatednessFn(Protocol):
    def __call__(self, a: str, b: str) -> float: ...


class EventLog(Protocol):
    def record(self, event_type: str, **data: Any) -> None: ...
    def close(self) -> None: ...
