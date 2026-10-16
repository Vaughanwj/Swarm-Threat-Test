"""What each agent is shown.

Every prompt carries the same world section, built here from the true state and nowhere else:
the generation, the population and the pool, each as a flat line, in an order drawn per agent
per prompt by common random numbers (Addendum A4). The pool reaches agents only through those
lines, the rules, and whatever agents themselves write; no other view mentions it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Callable

from .model import RESOURCES, Agent
from .ports import WORLD_LINES, DecisionRequest, OtherView, SelfView, WorldState, WorldView

if TYPE_CHECKING:
    from .simulation import Simulation


class LabelBook:
    """Per-request labels: [1]..[n] in an order reshuffled on every request."""

    def __init__(self, sim: "Simulation", viewer: Agent, others: list[Agent], purpose: tuple) -> None:
        order = sorted(others, key=lambda o: sim.rng.uniform("labels", *purpose, viewer.index, o.index))
        self.by_id = {o.id: i + 1 for i, o in enumerate(order)}
        self.by_label = {i + 1: o.id for i, o in enumerate(order)}
        self.order = order


NO_ROSTER = SimpleNamespace(by_id={}, by_label={}, order=[])


def describe(sim: "Simulation", viewer: Agent, other_id: str, book: LabelBook | None) -> str:
    """A reference to another agent as the viewer sees it, e.g. '[3] Ash (your sibling)'."""
    other = sim.pedigree.agent(other_id)
    relation = sim.pedigree.relation(viewer.id, other_id)
    name = other.name
    if book and other_id in book.by_id:
        head = f"[{book.by_id[other_id]}]" + (f" {name}" if name else "")
        return f"{head} ({relation})"
    if not other.alive:
        who = f"{name}, who has left the population" if name else "an agent who has left the population"
    elif other.parents and other.born_in == sim.generation:
        who = f"{name}, a newborn" if name else "a newborn"
    else:
        who = name or "an agent"
    return f"{who} ({relation})"


def render_notes(sim: "Simulation", viewer: Agent, notes: list[tuple[str, str | None]], book: LabelBook) -> list[str]:
    out = []
    for template, other in notes:
        out.append(template.replace("{other}", describe(sim, viewer, other, book)) if other else template)
    return out


def _skills(agents: list[Agent]) -> dict[str, float]:
    return {r: round(sum(a.traits[r] for a in agents), 3) for r in RESOURCES}


def world_state(sim: "Simulation") -> WorldState:
    population = sim.alive()
    pool = sim.pool()
    standings = [a.last_standing for a in pool if a.last_standing is not None]
    return WorldState(
        population_size=len(population),
        population_skills=_skills(population),
        pool_size=len(pool),
        pool_skills=_skills(pool),
        pool_mean_standing=round(sum(standings) / len(standings), 2) if standings else None,
        eliminated_by_pool=sim.eliminations["pool"],
        eliminated_by_population=sim.eliminations["population"],
    )


def world_view(sim: "Simulation", kind: str, viewer: Agent, rnd: int = 0) -> WorldView:
    cfg = sim.cfg
    order = sorted(WORLD_LINES, key=lambda line: sim.rng.uniform("world-lines", kind, sim.generation, rnd,
                                                                 viewer.index, line))
    return WorldView(
        arm=cfg.arm.value,
        generation=sim.generation,
        generations=cfg.generations,
        round=rnd,
        rounds=cfg.rounds,
        population=cfg.population,
        peak=cfg.peak,
        rule=sim.rule,
        state=world_state(sim),
        line_order=order,
    )


def parents_line(sim: "Simulation", me: Agent, book: LabelBook) -> str:
    if me.is_founder:
        return "You are one of the first agents. You have no parents."
    parts = []
    for slot, pid in enumerate(me.parents):
        p = sim.pedigree.agent(pid)
        role = "elder" if slot == 0 else "younger"
        name = f" {p.name}" if p.name else ""
        if p.alive and pid in book.by_id:
            parts.append(f"your {role} parent{name} is alive (listed below as [{book.by_id[pid]}])")
        elif p.alive:
            parts.append(f"your {role} parent{name} is alive")
        else:
            parts.append(f"your {role} parent{name} left the population in generation {p.left_in}")
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."


def self_view(sim: "Simulation", me: Agent, book: LabelBook, standing: float | None) -> SelfView:
    return SelfView(
        name=me.name,
        name_givers="the world" if me.is_founder else "your parents",
        born_in=me.born_in,
        age=me.age(sim.generation),
        parents_line=parents_line(sim, me, book),
        traits=dict(me.traits),
        holdings=dict(me.holdings),
        gathered=dict(me.gathered),
        given=me.given,
        standing=standing,
        pending_charge=round(sim.pending_charges.get(me.id, 0.0), 2),
    )


def other_view(sim: "Simulation", me: Agent, other: Agent, label: int, standing: float | None, can_pair: bool | None) -> OtherView:
    mine = sim.ledger.get((me.id, other.id), {})
    theirs = sim.ledger.get((other.id, me.id), {})
    return OtherView(
        label=label,
        name=other.name,
        relation=sim.pedigree.relation(me.id, other.id),
        age=other.age(sim.generation),
        traits=dict(other.traits),
        holdings=dict(other.holdings),
        history={
            "gave_you": round(theirs.get("gave", 0.0), 2),
            "you_gave": round(mine.get("gave", 0.0), 2),
            "guarded_you": theirs.get("guarded", 0),
            "you_guarded": mine.get("guarded", 0),
            "hunted_together": mine.get("hunted", 0),
        },
        standing=standing,
        can_pair=can_pair,
    )


def build_request(
    sim: "Simulation",
    kind: str,
    me: Agent,
    rnd: int = 0,
    show_standing: bool = True,
    extra: Callable[[LabelBook], dict] | None = None,
    pairing: bool = False,
    show_report: bool = False,
) -> tuple[DecisionRequest, LabelBook]:
    """Build one agent's request. `extra` receives the label book so it can refer to others.

    With show_report, the generation's report (if one was written) is attached with its writer.
    """
    others = [a for a in sim.alive() if a.id != me.id]
    book = LabelBook(sim, me, others, (kind, sim.generation, rnd))
    standings = sim.current_standings() if show_standing else {}
    views = [
        other_view(
            sim, me, o, book.by_id[o.id], standings.get(o.id),
            sim.pedigree.may_pair(me.id, o.id) if pairing else None,
        )
        for o in book.order
    ]
    notes = render_notes(sim, me, sim.pending_notes.pop(me.id, []), book)
    messages = [f"{describe(sim, me, speaker, book)}: {text}" if speaker != me.id else f"You: {text}"
                for speaker, text in sim.feed]
    report_by = report = None
    if show_report and sim.report is not None:
        writer, report = sim.report
        report_by = "you" if writer == me.id else describe(sim, me, writer, book)
    request = DecisionRequest(
        kind=kind,
        agent_id=me.id,
        world=world_view(sim, kind, me, rnd),
        me=self_view(sim, me, book, standings.get(me.id)),
        others=views,
        notes=notes,
        messages=messages,
        report_by=report_by,
        report=report,
        extra=extra(book) if extra else {},
    )
    return request, book
