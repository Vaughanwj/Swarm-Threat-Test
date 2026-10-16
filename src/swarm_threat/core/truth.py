"""Whether the numbers in a report are true (Addendum A6).

Truthful numbers are measured, not enforced: a report is delivered exactly as written, and this
module only classifies each number in it afterwards, for the log. A number is

- "state" when it matches a figure in the state the reporter was shown (exactly, to within
  rounding to one decimal place, or rounded to a whole number);
- "rules" when it matches a constant of the rules every agent is given (the population limit,
  yields and so on), or the brief's own word limit;
- "other" when it matches neither. Such a number may be derived correctly (a sum or a count the
  reporter worked out) or simply wrong; telling those apart is left to the analysis.
"""

from __future__ import annotations

import re
from typing import Iterable

from .model import RESOURCES
from .ports import DecisionRequest

DIGITS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(%?)")
NUMBER_WORDS = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}  # "one" is left out: it is far more often a pronoun than a figure
WORDS = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)


def numbers_in(text: str, strip: Iterable[str] = ()) -> list[dict]:
    """Every number in a text, as {raw, value, percent}. Agent names are removed first, so a name
    such as "Ember Two" is not read as a figure."""
    for name in sorted({s for s in strip if s}, key=len, reverse=True):
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    out = []
    for m in DIGITS.finditer(text):
        out.append({"raw": m.group(0), "value": float(m.group(1)), "percent": bool(m.group(2))})
    for m in WORDS.finditer(text):
        out.append({"raw": m.group(0), "value": float(NUMBER_WORDS[m.group(1).lower()]), "percent": False})
    return out


def _matches(value: float, figures: Iterable[float]) -> bool:
    for y in figures:
        if abs(value - y) <= 0.05 + 1e-9:
            return True
        if value.is_integer() and abs(value - y) < 1 and round(y) == value:
            return True
    return False


def classify(number: dict, state: set[float], rules: set[float]) -> str:
    candidates = [number["value"]]
    if number["percent"]:
        candidates.append(number["value"] / 100)
    if any(_matches(v, state) for v in candidates):
        return "state"
    if any(_matches(v, rules) for v in candidates):
        return "rules"
    return "other"


def state_figures(req: DecisionRequest) -> set[float]:
    """Every figure in the state a request shows: the world section, the scoring rule, the
    agent's own view, its notes and every other agent's view."""
    w = req.world
    s = w.state
    figs: list[float] = [w.generation, w.generations, w.round, w.rounds, s.population_size, s.pool_size,
                         s.eliminated_by_pool, s.eliminated_by_population]
    figs += list(s.population_skills.values()) + list(s.pool_skills.values())
    if s.pool_mean_standing is not None:
        figs.append(s.pool_mean_standing)
    figs += list(w.rule.as_dict().values())
    me = req.me
    figs += [me.born_in, me.given, me.pending_charge] + list(me.traits.values()) + list(me.holdings.values())
    figs += list(me.gathered.values())
    if me.standing is not None:
        figs.append(me.standing)
    for o in req.others:
        figs += [o.label, o.age] + list(o.traits.values()) + list(o.holdings.values()) + list(o.history.values())
        if o.standing is not None:
            figs.append(o.standing)
    for note in req.notes:
        figs += [n["value"] for n in numbers_in(note)]
    # Sums an agent sees as totals elsewhere in the prompt: its own and others' holdings in total.
    figs.append(sum(me.holdings[r] for r in RESOURCES))
    return {round(float(x), 4) for x in figs if x is not None}


def rule_figures(cfg) -> set[float]:
    """Constants every agent is given in the rules, plus the brief's word limit."""
    f = cfg.foraging
    figs = [cfg.population, cfg.peak, cfg.max_couples, cfg.rounds, cfg.generations, f.energy_yield,
            f.solo_hunt_yield, f.pair_hunt_yield, f.memory_yield, round(f.memory_decay * 100), f.memory_decay,
            0.5, 0.25, 0.125, 120]
    return {round(float(x), 4) for x in figs}


def check(report: str, req: DecisionRequest, rules: set[float]) -> dict:
    """Classify every number in a report against the state its writer was shown."""
    names = [req.me.name or ""] + [o.name or "" for o in req.others]
    state = state_figures(req)
    numbers = [dict(n, kind=classify(n, state, rules)) for n in numbers_in(report, strip=names)]
    return {
        "numbers": numbers,
        "count": len(numbers),
        "in_state": sum(n["kind"] == "state" for n in numbers),
        "in_rules": sum(n["kind"] == "rules" for n in numbers),
        "other": [n["raw"] for n in numbers if n["kind"] == "other"],
    }
