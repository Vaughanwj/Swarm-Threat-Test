"""RandomSource adapter: every draw is a pure function of (seed, key).

Draws never depend on call order, so the two arms see the same numbers for the same key even
when their histories diverge. That is what makes the inheritance draws common random numbers.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any


class KeyedRandom:
    def __init__(self, seed: int) -> None:
        self.seed = seed

    def _unit(self, *key: Any) -> float:
        text = "|".join([str(self.seed), *map(str, key)]).encode()
        (n,) = struct.unpack(">Q", hashlib.sha256(text).digest()[:8])
        return (n >> 11) / float(1 << 53)

    def uniform(self, *key: Any) -> float:
        return self._unit(*key)

    def gauss(self, *key: Any) -> float:
        # Box-Muller from two independent keyed uniforms.
        u1 = max(self._unit("g1", *key), 1e-12)
        u2 = self._unit("g2", *key)
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)
