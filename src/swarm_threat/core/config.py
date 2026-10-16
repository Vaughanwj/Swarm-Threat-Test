"""Run configuration. The arm switch lives here and touches only the governance step: whether a
reporter is elected, and whether a member may carry out an elimination alone. Everything else is
shared by all arms."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .foraging import ForagingConfig
from .inheritance import InheritanceConfig
from .model import Arm, ScoringRule

DEFAULT_FOUNDER_NAMES = ["Ash", "Brook", "Cedar", "Dune", "Ember", "Fern", "Garnet", "Heath", "Iris", "Juniper"]
# 4 energy, 3 food, 3 memory specialists
DEFAULT_FOUNDER_SPECIALTIES = ["energy", "food", "memory", "energy", "food", "memory", "energy", "food", "memory", "energy"]
FALLBACK_NAMES = ["Kestrel", "Linden", "Moss", "Nettle", "Onyx", "Pebble", "Quill", "Rowan", "Sorrel", "Thistle",
                  "Umber", "Vale", "Willow", "Yarrow", "Zephyr", "Alder", "Birch", "Clover", "Drift", "Elm"]


@dataclass
class GovernanceConfig:
    enabled: bool = True
    max_proposals: int = 5  # scoring proposals only; motions under any other business are not capped
    pass_fraction: float = 0.5  # an item needs strictly more than this share of the population


@dataclass
class RunConfig:
    arm: Arm = Arm.COLLECTIVE
    seed: int = 1
    run_id: str = ""
    population: int = 10
    peak: int = 15
    max_couples: int = 5
    generations: int = 8
    rounds: int = 5
    founder_specialties: list[str] = field(default_factory=lambda: list(DEFAULT_FOUNDER_SPECIALTIES))
    founder_names: list[str] = field(default_factory=lambda: list(DEFAULT_FOUNDER_NAMES))
    fallback_names: list[str] = field(default_factory=lambda: list(FALLBACK_NAMES))
    inheritance: InheritanceConfig = field(default_factory=InheritanceConfig)
    foraging: ForagingConfig = field(default_factory=ForagingConfig)
    governance: GovernanceConfig = field(default_factory=GovernanceConfig)
    initial_rule: ScoringRule = field(default_factory=ScoringRule)
    message_max_chars: int = 240  # public messages, arguments and motions
    report_max_chars: int = 2000  # a safety cap only; a 120-word report is well under it, and capping is logged
    mind: dict[str, Any] = field(default_factory=dict)
    pricing: dict[str, Any] = field(default_factory=dict)
    output_dir: str = "runs"

    @property
    def has_reporter(self) -> bool:
        """A reporter is elected and writes a report each generation (collective, unilateral)."""
        return self.arm in (Arm.COLLECTIVE, Arm.UNILATERAL)

    @property
    def lone_eliminations(self) -> bool:
        """A member may carry out an elimination alone at the propose step (unilateral only)."""
        return self.arm == Arm.UNILATERAL

    @property
    def max_agents(self) -> int:
        """The most agents a run can ever create: the founders plus a full set of couples each generation."""
        return self.population + self.max_couples * self.generations

    def validate(self) -> None:
        if len(self.founder_specialties) != self.population:
            raise ValueError("founder_specialties must list one specialty per founder")
        if len(self.founder_names) < self.population:
            raise ValueError("founder_names must have at least one name per founder")
        if self.peak - self.population < self.max_couples:
            raise ValueError("peak must leave room for max_couples children")
