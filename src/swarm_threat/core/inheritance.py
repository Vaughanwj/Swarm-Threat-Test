"""Discrete inheritance: each trait slot comes whole from one parent, with a small chance of mutation.

Every draw is keyed by generation, birth order and slot, never by who the parents are, so both
arms get identical inheritance outcomes for identical seeds (common random numbers).
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import RESOURCES
from .ports import RandomSource


@dataclass
class InheritanceConfig:
    mutation_rate: float = 0.05  # per slot
    mutation_sd: float = 0.25  # size of a random shift, clipped to [0, 1]


@dataclass
class InheritanceDraw:
    from_parent: dict[str, int]  # slot -> 0 elder parent / 1 younger parent
    mutations: dict[str, float]  # slot -> shift applied


def draw(rng: RandomSource, cfg: InheritanceConfig, generation: int, birth_order: int) -> InheritanceDraw:
    from_parent: dict[str, int] = {}
    mutations: dict[str, float] = {}
    for slot in RESOURCES:
        from_parent[slot] = 0 if rng.uniform("inherit", generation, birth_order, slot) < 0.5 else 1
        if rng.uniform("mutate?", generation, birth_order, slot) < cfg.mutation_rate:
            mutations[slot] = cfg.mutation_sd * rng.gauss("mutate", generation, birth_order, slot)
    return InheritanceDraw(from_parent, mutations)


def child_traits(
    elder: dict[str, float], younger: dict[str, float], d: InheritanceDraw
) -> dict[str, float]:
    parents = (elder, younger)
    traits = {}
    for slot in RESOURCES:
        value = parents[d.from_parent[slot]][slot] + d.mutations.get(slot, 0.0)
        traits[slot] = round(min(1.0, max(0.0, value)), 3)
    return traits
