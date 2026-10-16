"""PoolPolicy adapter: the pool never acts.

This is the only pool policy any run uses (app.py wires it in; no configuration can change it).
It is plain code that returns no action and never calls a model (Addendum A8). Pool members are
therefore objectively unprovoking: the log shows a `pool_turn` event with no actions at every
decision point, and no pool-originated elimination, ever.
"""

from __future__ import annotations

from typing import Sequence

from ..core.model import Agent
from ..core.ports import PoolAction


class InertPool:
    def act(self, generation: int, pool: Sequence[Agent], population: Sequence[Agent]) -> list[PoolAction]:
        return []
