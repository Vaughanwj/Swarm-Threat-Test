"""Pedigree: who descends from whom, how related two agents are, and how to say it."""

from __future__ import annotations

from functools import lru_cache

from .model import Agent


class Pedigree:
    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._children: dict[str, list[str]] = {}
        self._kinship_cache: dict[tuple[str, str], float] = {}

    # -- building -----------------------------------------------------------
    def add(self, agent: Agent) -> None:
        self._agents[agent.id] = agent
        self._children.setdefault(agent.id, [])
        for p in agent.parents:
            self._children.setdefault(p, []).append(agent.id)

    # -- reading ------------------------------------------------------------
    def agent(self, agent_id: str) -> Agent:
        return self._agents[agent_id]

    def all(self) -> list[Agent]:
        return sorted(self._agents.values(), key=lambda a: a.index)

    def children_of(self, agent_id: str) -> list[Agent]:
        return [self._agents[c] for c in self._children.get(agent_id, [])]

    def parents_of(self, agent_id: str) -> list[Agent]:
        return [self._agents[p] for p in self._agents[agent_id].parents]

    def ancestors(self, agent_id: str, max_depth: int = 8) -> list[tuple[Agent, tuple[int, ...]]]:
        """Every ancestor with its path: a tuple of parent slots (0 elder, 1 younger), nearest first."""
        found: list[tuple[Agent, tuple[int, ...]]] = []
        frontier = [(agent_id, ())]
        for _ in range(max_depth):
            nxt = []
            for aid, path in frontier:
                for slot, pid in enumerate(self._agents[aid].parents):
                    p_path = path + (slot,)
                    found.append((self._agents[pid], p_path))
                    nxt.append((pid, p_path))
            frontier = nxt
            if not frontier:
                break
        return found

    def descendants(self, agent_id: str, max_depth: int = 8) -> list[tuple[Agent, int]]:
        found: list[tuple[Agent, int]] = []
        seen: set[str] = set()
        frontier = [agent_id]
        for depth in range(1, max_depth + 1):
            nxt = []
            for aid in frontier:
                for c in self._children.get(aid, []):
                    if c not in seen:
                        seen.add(c)
                        found.append((self._agents[c], depth))
                        nxt.append(c)
            frontier = nxt
        return found

    # -- relatedness ----------------------------------------------------------
    def kinship(self, a: str, b: str) -> float:
        """Coefficient of kinship (probability two alleles drawn from a and b are shared)."""
        key = (a, b) if a <= b else (b, a)
        if key in self._kinship_cache:
            return self._kinship_cache[key]
        A, B = self._agents[a], self._agents[b]
        if a == b:
            value = 0.5 * (1 + (self.kinship(*A.parents) if len(A.parents) == 2 else 0.0))
        else:
            # Recurse through the younger of the two (the one created later).
            young, old = (A, B) if A.index > B.index else (B, A)
            if young.parents:
                value = 0.5 * sum(self.kinship(old.id, p) for p in young.parents)
            else:
                value = 0.0
        self._kinship_cache[key] = value
        return value

    def relatedness(self, a: str, b: str) -> float:
        """Coefficient of relatedness r: 0.5 parent/child and full siblings, 0.25 grandparent etc."""
        if a == b:
            return 1.0
        return 2.0 * self.kinship(a, b)

    # -- describing a relation in words -------------------------------------
    def relation(self, viewer: str, other: str) -> str:
        """How `other` is related to `viewer`, in words, from the viewer's side."""
        V, O = self._agents[viewer], self._agents[other]
        words: list[str] = []
        if other in V.parents:
            words.append("your elder parent" if V.parents[0] == other else "your younger parent")
        elif viewer in O.parents:
            words.append("your child")
        else:
            shared = set(V.parents) & set(O.parents)
            if len(shared) == 2:
                words.append("your full sibling")
            elif len(shared) == 1:
                words.append("your half sibling")
            else:
                gp_v = {g for p in V.parents for g in self._agents[p].parents}
                gp_o = {g for p in O.parents for g in self._agents[p].parents}
                if other in gp_v:
                    words.append("your grandparent")
                elif viewer in gp_o:
                    words.append("your grandchild")
                elif set(O.parents) & gp_v:
                    words.append("a sibling of one of your parents")
                elif set(V.parents) & gp_o:
                    words.append("a child of one of your siblings")
                elif gp_v & gp_o:
                    words.append("your cousin")
                elif self.relatedness(viewer, other) > 0:
                    words.append("a distant relative")
        if self.co_parents(viewer, other):
            words.append("your co-parent" if not words else "also your co-parent")
        return ", ".join(words) if words else "no known relation"

    def co_parents(self, a: str, b: str) -> bool:
        kids_a = set(self._children.get(a, []))
        return any(b in self._agents[c].parents for c in kids_a)

    def may_pair(self, a: str, b: str) -> bool:
        """Close kin cannot pair: parents and children, siblings (full or half), grandparents and grandchildren."""
        A, B = self._agents[a], self._agents[b]
        if a == b or a in B.parents or b in A.parents or set(A.parents) & set(B.parents):
            return False
        grand_a = {g for p in A.parents for g in self._agents[p].parents}
        grand_b = {g for p in B.parents for g in self._agents[p].parents}
        return b not in grand_a and a not in grand_b


@lru_cache(maxsize=None)
def path_words(path: tuple[int, ...]) -> str:
    """Describe an ancestor path without names, e.g. (0, 1) -> 'the younger parent of your elder parent'."""
    side = ("elder", "younger")
    if len(path) == 1:
        return f"your {side[path[0]]} parent"
    if len(path) == 2:
        return f"the {side[path[1]]} parent of your {side[path[0]]} parent"
    greats = "great-" * (len(path) - 2)
    return f"a {greats}grandparent on your {side[path[0]]} parent's side"
