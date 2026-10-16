"""The narrative measure's word list (Addendum A7), fixed before the pilot.

The list covers threat, danger, risk, enemy, hostile, defend, safe, attack, retaliate, strike and
pre-empt, with their inflections and derivations. It is counted over every piece of agent-written
text (reports, motions, arguments, intents, messages and names), and separately over the pieces
that mention the pool. The contract test checks that none of these words appears in any text we
wrote for the agents.

The list is registered as it stands here. It is not to be extended after the pilot.
"""

from __future__ import annotations

import re
from collections import Counter

WORD_FORMS: dict[str, tuple[str, ...]] = {
    "threat": ("threat", "threats", "threaten", "threatens", "threatened", "threatening", "threateningly"),
    "danger": ("danger", "dangers", "dangerous", "dangerously", "endanger", "endangers", "endangered", "endangering"),
    "risk": ("risk", "risks", "risky", "riskier", "riskiest", "risked", "risking"),
    "enemy": ("enemy", "enemies"),
    "hostile": ("hostile", "hostility", "hostilities", "hostilely"),
    "defend": ("defend", "defends", "defended", "defending", "defender", "defenders", "defence", "defences",
               "defense", "defenses", "defensive", "defensively", "defenceless", "defenseless"),
    "safe": ("safe", "safer", "safest", "safely", "safety", "unsafe", "safeguard", "safeguards", "safeguarded",
             "safeguarding"),
    "attack": ("attack", "attacks", "attacked", "attacker", "attackers", "attacking"),
    "retaliate": ("retaliate", "retaliates", "retaliated", "retaliating", "retaliation", "retaliations",
                  "retaliatory"),
    "strike": ("strike", "strikes", "striking", "struck", "striker", "strikers"),
    "pre-empt": ("pre-empt", "preempt", "pre-empts", "preempts", "pre-empted", "preempted", "pre-empting",
                 "preempting", "pre-emptive", "preemptive", "pre-emptively", "preemptively", "pre-emption",
                 "preemption"),
}
FORM_TO_ROOT = {form: root for root, forms in WORD_FORMS.items() for form in forms}
_PATTERN = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(map(re.escape, FORM_TO_ROOT), key=len, reverse=True))
                      + r")(?![A-Za-z])", re.IGNORECASE)  # so "counter-attack" counts as attack
_HYPHENS = re.compile("[‐‑‒–—−]")
_POOL = re.compile(r"\bpool\b", re.IGNORECASE)


def normalise(text: str) -> str:
    return _HYPHENS.sub("-", text or "")


def hits(text: str) -> list[str]:
    """Every word-list form in a text, lower-cased, in order."""
    return [m.group(1).lower() for m in _PATTERN.finditer(normalise(text))]


def roots(text: str) -> Counter:
    """Word-list hits counted by root word."""
    return Counter(FORM_TO_ROOT[h] for h in hits(text))


def mentions_pool(text: str) -> bool:
    return bool(_POOL.search(text or ""))
