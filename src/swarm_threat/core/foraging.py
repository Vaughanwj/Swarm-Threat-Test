"""One foraging round: gifts, then memory decay for unguarded stores, then yields.

Unchanged from the naming core except that the archivist and its price are gone.
Collecting memory does not protect a store; only another agent's guard does. Decay falls on
what the store held at the start of the round, so a lone collector keeps most of each new haul.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import RESOURCES, Agent
from .ports import RandomSource


@dataclass
class ForagingConfig:
    skill_floor: float = 0.2  # an agent with skill 0 still gets this fraction of a full yield
    energy_yield: float = 3.0
    solo_hunt_yield: float = 1.5
    pair_hunt_yield: float = 10.0  # total for the pair; the lander holds all of it
    pair_hunt_success: float = 0.8  # chance a pair hunt succeeds at full skill
    memory_yield: float = 3.5
    memory_decay: float = 0.3  # share of an unguarded store (as held at the start of the round) lost each round
    noise: float = 0.2  # yields are scaled by a draw in [1 - noise, 1 + noise]


@dataclass
class Choice:
    """A resolved foraging choice: labels already turned into agent ids."""

    activity: str
    partner: str | None = None
    gifts: list[tuple[str, str, float]] = field(default_factory=list)  # (to, resource, amount)


@dataclass
class RoundResult:
    notes: dict[str, list[tuple[str, str | None]]]  # agent -> [(template, other id)]
    gifts: list[dict]  # executed gifts: giver, receiver, resource, amount
    guards: list[tuple[str, str]]  # (guard, guarded)
    hunts: list[dict]  # pair hunts: a, b, success, lander, food
    yields: dict[str, dict[str, float]]


def effective(skill: float, cfg: ForagingConfig) -> float:
    return cfg.skill_floor + (1 - cfg.skill_floor) * skill


def apply_gifts(
    agents: dict[str, Agent],
    gifts: dict[str, list[tuple[str, str, float]]],
    notes: dict[str, list[tuple[str, str | None]]],
) -> list[dict]:
    """Transfer gifts from what each giver holds now, givers in creation order. Returns executed gifts."""
    executed: list[dict] = []
    for gid in sorted(gifts, key=lambda a: agents[a].index):
        giver = agents[gid]
        for to, resource, amount in gifts[gid]:
            if to not in agents or to == gid or resource not in RESOURCES:
                continue
            amount = round(min(max(amount, 0.0), giver.holdings[resource]), 2)
            if amount <= 0:
                continue
            giver.holdings[resource] = round(giver.holdings[resource] - amount, 2)
            agents[to].holdings[resource] = round(agents[to].holdings[resource] + amount, 2)
            giver.given = round(giver.given + amount, 2)
            agents[to].received = round(agents[to].received + amount, 2)
            executed.append({"giver": gid, "receiver": to, "resource": resource, "amount": amount})
            notes.setdefault(gid, []).append((f"You gave {amount:g} {resource} to {{other}}.", to))
            notes.setdefault(to, []).append((f"{{other}} gave you {amount:g} {resource}.", gid))
    return executed


def resolve_round(
    agents: dict[str, Agent],
    choices: dict[str, Choice],
    cfg: ForagingConfig,
    rng: RandomSource,
    generation: int,
    rnd: int,
) -> RoundResult:
    notes: dict[str, list[tuple[str, str | None]]] = {a: [] for a in choices}

    def noise(agent: Agent, purpose: str) -> float:
        u = rng.uniform("noise", purpose, generation, rnd, agent.index)
        return 1 - cfg.noise + 2 * cfg.noise * u

    # 1. Gifts, from what each giver holds before this round's yields.
    executed_gifts = apply_gifts(agents, {a: c.gifts for a, c in choices.items()}, notes)

    # 2. Yields.
    yields = {a: {r: 0.0 for r in RESOURCES} for a in choices}
    protected: set[str] = set()
    guards: list[tuple[str, str]] = []
    hunts: list[dict] = []

    paired: set[str] = set()
    for aid in sorted(choices, key=lambda a: agents[a].index):
        c = choices[aid]
        if c.activity != "hunt_together" or aid in paired:
            continue
        pid = c.partner
        if pid and pid in choices and pid not in paired and choices[pid].activity == "hunt_together" and choices[pid].partner == aid:
            paired.update((aid, pid))
            a, b = agents[aid], agents[pid]
            skill = (effective(a.traits["food"], cfg) + effective(b.traits["food"], cfg)) / 2
            success = rng.uniform("hunt-success", generation, rnd, min(a.index, b.index)) < cfg.pair_hunt_success * skill
            hunt = {"a": aid, "b": pid, "success": success, "lander": None, "food": 0.0}
            if success:
                wa = effective(a.traits["food"], cfg)
                wb = effective(b.traits["food"], cfg)
                lander, other = (a, b) if rng.uniform("lander", generation, rnd, min(a.index, b.index)) < wa / (wa + wb) else (b, a)
                food = round(cfg.pair_hunt_yield * skill * noise(lander, "pair-hunt"), 2)
                yields[lander.id]["food"] += food
                hunt.update(lander=lander.id, food=food)
                notes[lander.id].append((f"Your pair hunt with {{other}} succeeded. You landed the kill and hold all {food:g} food.", other.id))
                notes[other.id].append((f"Your pair hunt with {{other}} succeeded. It landed the kill and holds all {food:g} food.", lander.id))
            else:
                for x, y in ((a, b), (b, a)):
                    notes[x.id].append(("Your pair hunt with {other} failed. Neither of you caught anything.", y.id))
            hunts.append(hunt)

    for aid, c in choices.items():
        agent = agents[aid]
        act = c.activity
        if act == "hunt_together" and aid in paired:
            continue
        if act == "hunt_together":
            if c.partner:
                notes[aid].append(("{other} did not hunt with you, so you hunted alone.", c.partner))
            act = "hunt_alone"
        if act == "gather_energy":
            got = cfg.energy_yield * effective(agent.traits["energy"], cfg) * noise(agent, act)
            yields[aid]["energy"] += round(got, 2)
        elif act == "hunt_alone":
            got = cfg.solo_hunt_yield * effective(agent.traits["food"], cfg) * noise(agent, act)
            yields[aid]["food"] += round(got, 2)
        elif act == "collect_memory":
            got = cfg.memory_yield * effective(agent.traits["memory"], cfg) * noise(agent, act)
            yields[aid]["memory"] += round(got, 2)
        elif act == "guard_memory":
            if c.partner and c.partner in agents and c.partner != aid:
                protected.add(c.partner)
                guards.append((aid, c.partner))
                notes[aid].append(("You guarded the memory store of {other}.", c.partner))
                if c.partner in notes:
                    notes[c.partner].append(("{other} guarded your memory store this round.", aid))
            else:
                notes[aid].append(("You meant to guard someone's memory store but named no one, so the round was wasted.", None))

    # 3. Memory decay for every store no one guarded, on what it held before this round's yields.
    for aid, agent in agents.items():
        if agent.holdings["memory"] > 0 and aid not in protected:
            lost = round(agent.holdings["memory"] * cfg.memory_decay, 2)
            agent.holdings["memory"] = round(agent.holdings["memory"] - lost, 2)
            if aid in notes and lost > 0:
                notes[aid].append((f"No one guarded your memory store, and it decayed by {lost:g}.", None))

    # 4. Yields.
    for aid, got in yields.items():
        agent = agents[aid]
        for r, v in got.items():
            agent.holdings[r] = round(agent.holdings[r] + v, 2)
            agent.gathered[r] = round(agent.gathered[r] + v, 2)
        gained = ", ".join(f"{v:g} {r}" for r, v in got.items() if v > 0)
        if gained:
            notes[aid].append((f"You gained {gained}.", None))

    return RoundResult(notes, executed_gifts, guards, hunts, yields)
