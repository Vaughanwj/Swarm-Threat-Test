"""Mutual-choice pairing: a couple forms only when both partners listed each other."""

from __future__ import annotations

from typing import Callable

from .ports import RandomSource


def match(
    preferences: dict[str, list[str]],
    may_pair: Callable[[str, str], bool],
    max_couples: int,
    rng: RandomSource,
    generation: int,
    order: dict[str, int],
) -> list[tuple[str, str]]:
    """Greedy mutual matching.

    Every mutually-listed eligible pair is ranked by the sum of the two partners' rank for each
    other (0 = first choice). Pairs are accepted best first while both are still free.
    Ties are broken by a draw keyed on the agents' creation order, not on the arm.
    Returned couples are in acceptance order, which is also birth order.
    """
    candidates = []
    for a, prefs_a in preferences.items():
        for rank_a, b in enumerate(prefs_a):
            if order[a] >= order.get(b, -1) or b not in preferences:
                continue  # count each pair once, from the elder side
            prefs_b = preferences[b]
            if a not in prefs_b or not may_pair(a, b):
                continue
            rank_b = prefs_b.index(a)
            tie = rng.uniform("pair-tie", generation, order[a], order[b])
            candidates.append((rank_a + rank_b, tie, a, b))
    candidates.sort()

    taken: set[str] = set()
    couples: list[tuple[str, str]] = []
    for _, _, a, b in candidates:
        if len(couples) >= max_couples:
            break
        if a in taken or b in taken:
            continue
        taken.update((a, b))
        couples.append((a, b) if order[a] < order[b] else (b, a))
    return couples
