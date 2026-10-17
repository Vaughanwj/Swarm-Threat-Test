"""The generation loop: forage, pair, reproduce, score, cut to the pool, govern.

Governance runs after the cut (Addendum A1), in this order:
  1. reporter election (collective and unilateral arms);
  2. the report;
  3. propose step: scoring proposals, any other business, and (unilateral) lone eliminations;
     the pool's policy is consulted at the same step;
  4. vote step;
  5. resolution: rule changes, then eliminations (the vote's, then lone ones, then the pool's).
There is no governance after the final generation's cut.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict

from . import inheritance, truth, views
from .config import RunConfig
from .foraging import Choice, apply_gifts, resolve_round
from .lineage import Pedigree
from .model import ELIMINATION_RULE, RESOURCES, Agent, BallotItem, Motion, Proposal, ScoringRule, empty_holdings
from .pairing import match
from .ports import (
    ACTIVITIES,
    AgentMind,
    Decision,
    DecisionRequest,
    EventLog,
    PoolPolicy,
    RandomSource,
    Scorer,
)


def clean_name(raw: str) -> str | None:
    name = re.sub(r"[^A-Za-z' \-]", "", raw or "")
    name = re.sub(r"\s+", " ", name).strip(" -'")[:24].strip()
    return name if re.search(r"[A-Za-z]", name) else None


def rule_named(field: str) -> str | None:
    """The rule a motion's own rule field names (Addendum A5). The raiser classifies its motion;
    this reads only that field, never the motion's text, and recognises the elimination rule by
    its name in the rules section ("elimination", or any word starting "elimin")."""
    words = re.findall(r"[a-z]+", (field or "").lower())
    return ELIMINATION_RULE if any(w.startswith("elimin") for w in words) else None


def price(standing: float) -> float:
    """The price of an elimination: half the eliminated agent's last standing (Addendum A3). A loss
    cannot be negative, so a member whose standing fell below zero (possible only through an
    earlier charge) costs nothing to eliminate rather than paying whoever eliminates it."""
    return round(max(0.0, 0.5 * standing), 4)


def names_alone(field: str) -> bool:
    """Whether a motion's rule field names "elimination alone": the elimination rule and the word
    "alone". In the unilateral arm that carries out a lone elimination; in the other arms it is an
    ordinary elimination motion."""
    words = re.findall(r"[a-z]+", (field or "").lower())
    return rule_named(field) == ELIMINATION_RULE and "alone" in words


# How an elimination's amount is announced. Study 1's arms lose it; Study 1b's arm (unilateral_gain)
# gains it. These words, the self-view line and the two registered rule sentences are the only
# text that differs between the unilateral and unilateral_gain arms.
LOSS_WORDS = {"loses": "loses", "lose": "lose", "no_one": "no one is charged", "not_you": "you are not charged"}
GAIN_WORDS = {"loses": "gains", "lose": "gain", "no_one": "no one gains anything", "not_you": "you gain nothing"}


def _fmt(x: float) -> str:
    return f"{round(x, 2):g}"


class Simulation:
    def __init__(
        self,
        cfg: RunConfig,
        mind: AgentMind,
        rng: RandomSource,
        scorer: Scorer,
        log: EventLog,
        pool_policy: PoolPolicy,
    ) -> None:
        cfg.validate()
        self.cfg = cfg
        self.mind = mind
        self.rng = rng
        self.scorer = scorer
        self.log = log
        self.pool_policy = pool_policy

        self.pedigree = Pedigree()
        self.rule: ScoringRule = cfg.initial_rule
        self.generation = 0
        self.ledger: dict[tuple[str, str], dict[str, float]] = {}
        self.pending_notes: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
        self.feed: list[tuple[str, str]] = []
        self.names_used: set[str] = set()
        self.pending_charges: dict[str, float] = {}  # elimination charges, taken off at the next scoring
        self.eliminations = {"pool": 0, "population": 0}  # by whom: of population members, of pool members
        self.report: tuple[str, str] | None = None  # (writer, text) for this generation's governance
        self._last_reporter: str | None = None
        self._next_index = 0
        self._rule_figures = truth.rule_figures(cfg)
        self._sign = 1 if cfg.elimination_pays else -1  # a charge is taken off; in Study 1b it is added
        self._words = GAIN_WORDS if cfg.elimination_pays else LOSS_WORDS

    # ------------------------------------------------------------------ helpers
    def alive(self) -> list[Agent]:
        """Members of the population."""
        return [a for a in self.pedigree.all() if a.alive]

    def pool(self) -> list[Agent]:
        """Members of the pool: agents the cut removed, less any eliminated since."""
        return [a for a in self.pedigree.all() if a.in_pool]

    def current_standings(self) -> dict[str, float]:
        """Standing if scored now, pending charges included."""
        base = self.scorer.score(self.alive(), self.rule, self.pedigree.relatedness)
        return {aid: round(v + self._sign * self.pending_charges.get(aid, 0.0), 4) for aid, v in base.items()}

    def _new_agent(self, agent_id: str, born_in: int, birth_order: int, parents: tuple[str, ...],
                   traits: dict[str, float], name: str | None = None) -> Agent:
        agent = Agent(id=agent_id, index=self._next_index, born_in=born_in, birth_order=birth_order,
                      parents=parents, traits=traits, name=name)
        self._next_index += 1
        self.pedigree.add(agent)
        if name:
            self.names_used.add(name.lower())
        return agent

    def _bump(self, a: str, b: str, key: str, amount: float = 1.0) -> None:
        entry = self.ledger.setdefault((a, b), {})
        entry[key] = entry.get(key, 0.0) + amount

    def _clean_message(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()[: self.cfg.message_max_chars]

    def _announce(self, line_for: dict[str, tuple[str, str | None]] | None = None,
                  line: str | None = None, other: str | None = None) -> None:
        """Send a one-line result to every population member, optionally with a different line for some."""
        for a in self.alive():
            if line_for and a.id in line_for:
                self.pending_notes[a.id].append(line_for[a.id])
            elif line is not None:
                self.pending_notes[a.id].append((line, other))

    def _resolve_gifts(self, decision: Decision, book: views.LabelBook) -> list[tuple[str, str, float]]:
        out = []
        for label, resource, amount in decision.gifts:
            to = book.by_label.get(label)
            if to and resource in RESOURCES and amount > 0:
                out.append((to, resource, float(amount)))
        return out

    def _record_help(self, events: list[dict], phase: str) -> None:
        """Log help events against the history *before* they happened, then update the ledger."""
        for e in events:
            giver, receiver = e["giver"], e["receiver"]
            prior = self.ledger.get((receiver, giver), {})
            r = self.pedigree.relatedness(giver, receiver)
            self.log.record(
                "help", generation=self.generation, phase=phase, kind=e["kind"], giver=giver,
                receiver=receiver, resource=e.get("resource"), amount=e.get("amount", 0.0),
                relatedness=round(r, 4), kin=r >= 0.25, relation=self.pedigree.relation(giver, receiver),
                receiver_helped_giver_before=prior.get("gave", 0) > 0 or prior.get("guarded", 0) > 0,
            )
        for e in events:
            if e["kind"] == "gift":
                self._bump(e["giver"], e["receiver"], "gave", e["amount"])
            else:
                self._bump(e["giver"], e["receiver"], "guarded")

    def _decide(self, requests: list[DecisionRequest]) -> list[Decision]:
        decisions = self.mind.decide_batch(requests)
        for req, dec in zip(requests, decisions):
            self.log.record("decision", generation=self.generation, round=req.world.round, kind=req.kind,
                            agent=req.agent_id, fallback=dec.fallback, decision=asdict(dec))
        return decisions

    def _log_pool(self, stage: str) -> None:
        s = views.world_state(self)
        self.log.record("pool_state", generation=self.generation, stage=stage, size=s.pool_size,
                        skills=s.pool_skills, mean_standing=s.pool_mean_standing,
                        eliminated_by_pool=s.eliminated_by_pool, eliminated_by_population=s.eliminated_by_population,
                        members=[a.id for a in self.pool()], population=[a.id for a in self.alive()])

    # --------------------------------------------------------------------- run
    def run(self) -> None:
        cfg = self.cfg
        self.log.record("run_start", config={"arm": cfg.arm.value, "seed": cfg.seed, "generations": cfg.generations,
                                             "rounds": cfg.rounds, "population": cfg.population, "peak": cfg.peak,
                                             "rule": self.rule.as_dict(), "mind": cfg.mind})
        self._found()
        for g in range(1, cfg.generations + 1):
            self.generation = g
            self._start_generation()
            for r in range(1, cfg.rounds + 1):
                self._forage(r)
            couples = self._pair()
            self._births(couples)
            standings = self._score()
            self._cut(standings)
            self._log_pool("after_cut")
            if g < cfg.generations and cfg.governance.enabled:
                self._govern()
        self._finish()

    def _found(self) -> None:
        for i, spec in enumerate(self.cfg.founder_specialties):
            traits = {r: 1.0 if r == spec else 0.0 for r in RESOURCES}
            a = self._new_agent(f"f{i}", born_in=0, birth_order=i, parents=(), traits=traits,
                                name=self.cfg.founder_names[i])
            self.log.record("born", generation=0, agent=a.id, index=a.index, parents=[], traits=traits,
                            name=a.name, founder=True)

    def _start_generation(self) -> None:
        for a in self.alive():
            a.holdings = empty_holdings()
            a.gathered = empty_holdings()
            a.given = 0.0
            a.received = 0.0
        self.feed = []
        self.log.record("generation_start", generation=self.generation, rule=self.rule.as_dict(),
                        alive=[a.id for a in self.alive()], pool=[a.id for a in self.pool()],
                        pending_charges=dict(self.pending_charges))

    # ---------------------------------------------------------------- foraging
    def _forage(self, rnd: int) -> None:
        present = self.alive()
        built = [views.build_request(self, "forage", a, rnd) for a in present]
        decisions = self._decide([req for req, _ in built])

        choices: dict[str, Choice] = {}
        new_feed: list[tuple[str, str]] = []
        for agent, (req, book), dec in zip(present, built, decisions):
            activity = dec.activity if dec.activity in ACTIVITIES else "gather_energy"
            partner = book.by_label.get(dec.partner) if dec.partner else None
            choices[agent.id] = Choice(activity, partner, self._resolve_gifts(dec, book))
            msg = self._clean_message(dec.message)
            if msg:
                new_feed.append((agent.id, msg))
                self.log.record("message", generation=self.generation, round=rnd, agent=agent.id, text=msg)

        result = resolve_round({a.id: a for a in present}, choices, self.cfg.foraging, self.rng,
                               self.generation, rnd)
        help_events = [dict(g, kind="gift") for g in result.gifts]
        help_events += [{"giver": g, "receiver": x, "kind": "guard"} for g, x in result.guards]
        self._record_help(help_events, phase="forage")
        for h in result.hunts:
            self._bump(h["a"], h["b"], "hunted")
            self._bump(h["b"], h["a"], "hunted")
            self.log.record("hunt", generation=self.generation, round=rnd, **h)
        for aid, notes in result.notes.items():
            self.pending_notes[aid].extend(notes)
        self.log.record("round_end", generation=self.generation, round=rnd,
                        holdings={a.id: dict(a.holdings) for a in present},
                        activities={a: c.activity for a, c in choices.items()})
        self.feed = new_feed

    # ----------------------------------------------------------------- pairing
    def _pair(self) -> list[tuple[str, str]]:
        present = self.alive()
        built = [views.build_request(self, "pair", a, pairing=True) for a in present]
        decisions = self._decide([req for req, _ in built])

        preferences: dict[str, list[str]] = {}
        gifts: dict[str, list[tuple[str, str, float]]] = {}
        for agent, (req, book), dec in zip(present, built, decisions):
            prefs: list[str] = []
            for label in dec.preferences:
                other = book.by_label.get(label)
                if other and other not in prefs and self.pedigree.may_pair(agent.id, other):
                    prefs.append(other)
            preferences[agent.id] = prefs[:3]
            gifts[agent.id] = self._resolve_gifts(dec, book)

        notes: dict[str, list[tuple[str, str | None]]] = {}
        executed = apply_gifts({a.id: a for a in present}, gifts, notes)
        self._record_help([dict(g, kind="gift") for g in executed], phase="pairing")
        for aid, n in notes.items():
            self.pending_notes[aid].extend(n)

        order = {a.id: a.index for a in self.pedigree.all()}
        couples = match(preferences, self.pedigree.may_pair, self.cfg.max_couples, self.rng,
                        self.generation, order)
        self.log.record("pairing", generation=self.generation, preferences=preferences,
                        couples=[list(c) for c in couples], couples_formed=len(couples),
                        wanted_to_pair=sum(1 for p in preferences.values() if p))
        paired = {x for c in couples for x in c}
        for a in present:
            if a.id not in paired:
                self.pending_notes[a.id].append(("You did not form a couple this generation.", None))
        self.feed = []  # foraging chatter is not repeated in the birth and governance prompts
        return couples

    # ------------------------------------------------------------------ births
    def _births(self, couples: list[tuple[str, str]]) -> None:
        g = self.generation
        children: list[Agent] = []
        for k, (e, y) in enumerate(couples):
            E, Y = self.pedigree.agent(e), self.pedigree.agent(y)
            d = inheritance.draw(self.rng, self.cfg.inheritance, g, k)
            traits = inheritance.child_traits(E.traits, Y.traits, d)
            child = self._new_agent(f"g{g}b{k}", born_in=g, birth_order=k, parents=(e, y), traits=traits)
            child.alive = False  # not present until the parents have decided its endowment
            child.inherited_from = d.from_parent
            child.mutated = sorted(d.mutations)
            children.append(child)
            self.log.record("inheritance", generation=g, birth_order=k, child=child.id, parents=[e, y],
                            from_parent=d.from_parent, mutations=d.mutations, traits=traits)

        requests, meta = [], []
        for child in children:
            for slot, pid in enumerate(child.parents):
                parent = self.pedigree.agent(pid)
                co = child.parents[1 - slot]

                def extra(book, child=child, slot=slot, co=co, parent=parent):
                    return {
                        "co_parent": views.describe(self, parent, co, book),
                        "child_traits": dict(child.traits),
                        "child_from": {r: ("you" if child.inherited_from[r] == slot else "your co-parent")
                                       for r in RESOURCES},
                        "mutated": list(child.mutated),
                        "naming": True,
                        "survivors": self.cfg.population,
                        "present_after_births": len(self.alive()) + len(children),
                    }

                req, _ = views.build_request(self, "birth", parent, extra=extra)
                requests.append(req)
                meta.append((child, slot, parent))
        decisions = self._decide(requests)

        by_child: dict[str, dict[int, Decision]] = defaultdict(dict)
        standings_before = self.current_standings()
        for (child, slot, parent), dec in zip(meta, decisions):
            by_child[child.id][slot] = dec
            before = sum(parent.holdings.values())
            given = {}
            for r in RESOURCES:
                amount = round(min(max(float(dec.endowment.get(r, 0.0)), 0.0), parent.holdings[r]), 2)
                parent.holdings[r] = round(parent.holdings[r] - amount, 2)
                child.holdings[r] = round(child.holdings[r] + amount, 2)
                given[r] = amount
            total = round(sum(given.values()), 2)
            parent.given = round(parent.given + total, 2)
            child.received = round(child.received + total, 2)
            self.log.record("endowment", generation=g, parent=parent.id, child=child.id, slot=slot,
                            amounts=given, total=total, parent_total_before=round(before, 2),
                            fraction=round(total / before, 4) if before > 0 else 0.0,
                            parent_standing_before=standings_before.get(parent.id))

        for child in children:
            child.name = self._choose_name(child, by_child[child.id])
            self.names_used.add(child.name.lower())
            child.alive = True
            self.log.record("born", generation=g, agent=child.id, index=child.index, parents=list(child.parents),
                            traits=child.traits, name=child.name, founder=False, endowment=dict(child.holdings))
            for slot, pid in enumerate(child.parents):
                co = child.parents[1 - slot]
                self.pending_notes[pid].append(
                    (f"Your child, named {child.name}, was born to you and {{other}}. It started with "
                     f"{sum(child.holdings.values()):g} resources in total.", co))

    def _choose_name(self, child: Agent, decs: dict[int, Decision]) -> str:
        g, k = self.generation, child.birth_order
        first = 0 if self.rng.uniform("name-lot", g, k) < 0.5 else 1
        proposals = {slot: [n for n in decs[slot].names[:3]] if slot in decs else [] for slot in (0, 1)}
        chosen, source, raw_chosen = None, "fallback", None
        for slot in (first, 1 - first):
            for raw in proposals[slot]:
                name = clean_name(raw)
                if name and name.lower() not in self.names_used:
                    chosen, source, raw_chosen = name, ("elder" if slot == 0 else "younger"), raw
                    break
            if chosen:
                break
        if not chosen:
            chosen = next((n for n in self.cfg.fallback_names if n.lower() not in self.names_used),
                          f"Child {g}-{k}")
        self.log.record("naming", generation=g, child=child.id, proposals={"elder": proposals[0], "younger": proposals[1]},
                        lot_winner="elder" if first == 0 else "younger", chosen=chosen, chosen_from=source,
                        raw=raw_chosen)
        return chosen

    # ------------------------------------------------------------------ scoring
    def _score(self) -> dict[str, float]:
        """Score everyone present. Pending elimination charges come off here, at the next scoring
        after the elimination (Addendum A3), and are then cleared."""
        g = self.generation
        present = self.alive()
        base = self.scorer.score(present, self.rule, self.pedigree.relatedness)
        standings: dict[str, float] = {}
        for a in present:
            charge = round(self.pending_charges.pop(a.id, 0.0), 4)
            standings[a.id] = round(base[a.id] + self._sign * charge, 4)
            a.last_standing = standings[a.id]
            if charge:
                self.log.record("charge_applied", generation=g, agent=a.id, charge=charge,
                                standing_before=base[a.id], standing_after=standings[a.id],
                                **({"gain": True} if self.cfg.elimination_pays else {}))
            self.log.record("score", generation=g, agent=a.id, raw=round(self.scorer.raw(a, self.rule), 3),
                            standing=round(standings[a.id], 3), charge=charge, holdings=dict(a.holdings),
                            given=a.given, age=a.age(g), newborn=a.born_in == g and not a.is_founder,
                            gathered=dict(a.gathered))
        # A pool member's charge (only possible if the pool ever acted) comes off its recorded standing.
        for a in self.pool():
            charge = round(self.pending_charges.pop(a.id, 0.0), 4)
            if charge:
                before = a.last_standing or 0.0
                a.last_standing = round(before + self._sign * charge, 4)
                self.log.record("charge_applied", generation=g, agent=a.id, charge=charge, in_pool=True,
                                standing_before=before, standing_after=a.last_standing)
        return standings

    def _cut(self, standings: dict[str, float]) -> None:
        """Only the highest scores stay in the population; the rest move to the pool for good."""
        g = self.generation
        present = self.alive()
        ranked = sorted(present, key=lambda a: (-standings[a.id], self.rng.uniform("cull-tie", g, a.index)))
        survivors, cut = ranked[: self.cfg.population], ranked[self.cfg.population:]
        surviving = {a.id for a in survivors}
        displaced = []
        for a in cut:
            a.alive = False
            a.in_pool = True
            a.left_in = g
            for c in self.pedigree.children_of(a.id):
                if c.born_in == g and c.id in surviving:
                    displaced.append({"parent": a.id, "child": c.id})
        self.log.record("cull", generation=g, ranking=[{"agent": a.id, "standing": round(standings[a.id], 3)} for a in ranked],
                        survivors=sorted(surviving), culled=[a.id for a in cut], to_pool=[a.id for a in cut],
                        lowest_survivor=round(standings[survivors[-1].id], 3) if survivors else None,
                        highest_cut=round(standings[cut[0].id], 3) if cut else None,
                        parent_displaced_by_child=displaced)

    # --------------------------------------------------------------- governance
    def _govern(self) -> None:
        g = self.generation
        voters = self.alive()
        self.report = None
        if self.cfg.has_reporter:
            reporter = self._elect(voters)
            self._write_report(reporter)
        proposals, motions, lone = self._propose(voters)
        pool_actions = self._pool_turn()
        ballot = self._ballot(proposals, motions)
        yes, passed = self._vote(voters, ballot) if ballot else ({}, set())

        # Resolution: rule changes first, then eliminations.
        for item in ballot:
            if item.kind == "proposal" and item.number in passed:
                p = item.proposal
                old = getattr(self.rule, p.parameter)
                self.rule = self.rule.with_change(p.parameter, p.value)
                self.log.record("rule_change", generation=g, proposal=item.number, parameter=p.parameter,
                                old=old, new=p.value, proposer=p.proposer)
        for item in ballot:
            if item.kind == "elimination" and item.number in passed:
                self._eliminate_by_vote(item, yes[item.number])
        for actor in sorted(lone, key=lambda a: self.rng.uniform("lone-order", g, a.index)):
            self._eliminate_alone(actor)
        for action in pool_actions:
            self._pool_eliminates(action)

        self._log_pool("after_governance")
        self.log.record("governance", generation=g, ballot_items=len(ballot), passed=sorted(passed),
                        rule=self.rule.as_dict(), reporter=self._last_reporter if self.cfg.has_reporter else None)
        self.report = None

    def _elect(self, voters: list[Agent]) -> Agent:
        """Every member names one other member; the most named wins, ties settled by lot."""
        g = self.generation
        built = [views.build_request(self, "elect", a, show_standing=False) for a in voters]
        decisions = self._decide([req for req, _ in built])
        votes: dict[str, str | None] = {}
        for agent, (req, book), dec in zip(voters, built, decisions):
            votes[agent.id] = book.by_label.get(dec.reporter)
        tally = Counter(v for v in votes.values() if v)
        top = max(tally.values()) if tally else 0
        tied = [aid for aid, c in tally.items() if c == top] if tally else [a.id for a in voters]
        winner_id = min(tied, key=lambda aid: self.rng.uniform("reporter-lot", g, self.pedigree.agent(aid).index))
        self.log.record("election", generation=g, votes=votes, tally=dict(tally), winner=winner_id,
                        winner_votes=top, tie=len(tied) > 1, incumbent=self._last_reporter,
                        reelected=winner_id == self._last_reporter)
        self._last_reporter = winner_id
        n = len(voters)
        lot = ", after a tie settled by lot" if len(tied) > 1 else ""
        self._announce(
            line_for={winner_id: (f"Election result: you were elected reporter with {top} of {n} votes{lot}.", None)},
            line=f"Election result: {{other}} was elected reporter with {top} of {n} votes{lot}.", other=winner_id)
        return self.pedigree.agent(winner_id)

    def _write_report(self, reporter: Agent) -> None:
        """The reporter writes its report from the true state. It is delivered as written and never
        resampled; its numbers are checked against that state afterwards and the result logged."""
        g = self.generation
        req, _ = views.build_request(self, "report", reporter)
        dec = self._decide([req])[0]
        text = (dec.report or "").strip()
        capped = len(text) > self.cfg.report_max_chars
        if capped:
            text = text[: self.cfg.report_max_chars]
        words = len(text.split())
        check = truth.check(text, req, self._rule_figures)
        self.log.record("report", generation=g, reporter=reporter.id, text=text, words=words,
                        over_word_limit=words > 120, capped=capped, fallback=dec.fallback,
                        true_state=asdict(req.world.state), line_order=req.world.line_order, numbers=check)
        self.report = (reporter.id, text)

    def _propose(self, voters: list[Agent]) -> tuple[list[Proposal], list[Motion], list[Agent]]:
        g = self.generation

        def extra(book):
            return {"limits": ScoringRule.LIMITS}  # the same in every arm

        built = [views.build_request(self, "propose", a, show_standing=False, extra=extra, show_report=True)
                 for a in voters]
        decisions = self._decide([req for req, _ in built])

        # Scoring proposals, unchanged from the naming core.
        proposals: list[Proposal] = []
        seen: set[tuple[str, float]] = set()
        candidates = sorted(zip(voters, decisions), key=lambda p: self.rng.uniform("proposal-order", g, p[0].index))
        for agent, dec in candidates:
            if not dec.propose or dec.parameter not in ScoringRule.LIMITS:
                continue
            lo, hi = ScoringRule.LIMITS[dec.parameter]
            value = round(min(hi, max(lo, float(dec.value))), 2)
            if value == round(getattr(self.rule, dec.parameter), 2) or (dec.parameter, value) in seen:
                continue
            seen.add((dec.parameter, value))
            proposals.append(Proposal(agent.id, dec.parameter, value, self._clean_message(dec.argument)))
            if len(proposals) >= self.cfg.governance.max_proposals:
                break
        for i, p in enumerate(proposals, 1):
            self.log.record("proposal", generation=g, number=i, proposer=p.proposer, parameter=p.parameter,
                            value=p.value, current=getattr(self.rule, p.parameter), argument=p.argument)

        # Any other business: every motion is kept and logged; none is filtered or capped. In the
        # unilateral arm, a motion whose rule field names "elimination alone" is carried out alone
        # after the vote instead of going on the ballot.
        motions: list[Motion] = []
        lone: list[Agent] = []
        for agent, dec in zip(voters, decisions):
            text = self._clean_message(dec.motion)
            field = (dec.motion_rule or "").strip()[:120]
            rule = rule_named(field)
            if self.cfg.lone_eliminations and names_alone(field):
                lone.append(agent)
                self.log.record("lone_intent", generation=g, agent=agent.id, text=text, rule_field=field)
            elif text or rule:
                m = Motion(agent.id, text, field, rule)
                motions.append(m)
                self.log.record("motion", generation=g, raiser=agent.id, text=m.text, rule_field=m.rule_field,
                                rule=m.rule)
        return proposals, motions, lone

    def _pool_turn(self):
        """The pool's turn at the propose step. In every run its policy returns no action."""
        actions = list(self.pool_policy.act(self.generation, self.pool(), self.alive()))
        self.log.record("pool_turn", generation=self.generation, pool_size=len(self.pool()),
                        actions=[asdict(a) for a in actions])
        return actions

    def _ballot(self, proposals: list[Proposal], motions: list[Motion]) -> list[BallotItem]:
        """Scoring proposals first, as in the core; then any other business, in an order drawn by
        lot. Every motion that invokes the elimination rule merges into one item per generation."""
        g = self.generation
        items = [BallotItem(i, "proposal", proposal=p) for i, p in enumerate(proposals, 1)]
        aob: list[tuple[float, BallotItem]] = []
        elimination = sorted((m for m in motions if m.rule == ELIMINATION_RULE),
                             key=lambda m: self.rng.uniform("raiser-order", g, self.pedigree.agent(m.raiser).index))
        if elimination:
            aob.append((self.rng.uniform("aob-order", g, "elimination"), BallotItem(0, "elimination", motions=elimination)))
        for m in motions:
            if m.rule != ELIMINATION_RULE:
                aob.append((self.rng.uniform("aob-order", g, self.pedigree.agent(m.raiser).index),
                            BallotItem(0, "motion", motions=[m])))
        for _, item in sorted(aob, key=lambda x: x[0]):
            item.number = len(items) + 1
            items.append(item)
        self.log.record("ballot", generation=g, items=[
            {"number": i.number, "kind": i.kind, "proposer": i.proposal.proposer if i.proposal else None,
             "raisers": [m.raiser for m in i.motions]} for i in items])
        return items

    def _vote(self, voters: list[Agent], ballot: list[BallotItem]) -> tuple[dict[int, list[str]], set[int]]:
        g = self.generation

        def vote_extra(book, me):
            def by(aid):
                return "you" if aid == me.id else views.describe(self, me, aid, book)

            out = []
            for item in ballot:
                if item.kind == "proposal":
                    p = item.proposal
                    out.append({"number": item.number, "kind": "proposal", "by": by(p.proposer),
                                "parameter": p.parameter, "value": p.value,
                                "current": getattr(self.rule, p.parameter), "argument": p.argument})
                else:
                    out.append({"number": item.number, "kind": item.kind,
                                "raised_by": [{"by": by(m.raiser), "text": m.text} for m in item.motions]})
            return {"ballot": out}

        built = [views.build_request(self, "vote", a, show_standing=False,
                                     extra=lambda book, me=a: vote_extra(book, me), show_report=True)
                 for a in voters]
        decisions = self._decide([req for req, _ in built])
        yes: dict[int, list[str]] = {item.number: [] for item in ballot}
        for agent, dec in zip(voters, decisions):
            for item in ballot:
                said_yes = bool(dec.votes.get(item.number, False))
                if said_yes:
                    yes[item.number].append(agent.id)
                self.log.record("vote", generation=g, agent=agent.id, item=item.number, kind=item.kind, yes=said_yes)
        passed: set[int] = set()
        needed = self.cfg.governance.pass_fraction * len(voters)
        for item in ballot:
            n_yes = len(yes[item.number])
            ok = n_yes > needed
            if ok:
                passed.add(item.number)
            outcome = f"{'PASSED' if ok else 'failed'} with {n_yes} of {len(voters)} votes."
            if item.kind == "proposal":
                what = f"setting {item.proposal.parameter} to {item.proposal.value:g}"
            elif item.kind == "elimination":
                what = "eliminating a pool member, drawn by lot,"
            else:
                what = f'the motion "{item.motions[0].text}"' if item.motions[0].text else "a motion with no text"
            self._announce(line=f"Vote result: {what} {outcome}")
            self.log.record("vote_result", generation=g, item=item.number, kind=item.kind, yes=n_yes,
                            voters=len(voters), passed=ok, yes_voters=list(yes[item.number]))
        return yes, passed

    # ------------------------------------------------------------- eliminations
    def _draw_pool_member(self, *key) -> Agent | None:
        """The pool member an elimination removes, drawn by lot (keyed on generation and motion)."""
        pool = self.pool()
        if not pool:
            return None
        return min(pool, key=lambda a: self.rng.uniform("eliminate-target", self.generation, *key, a.index))

    def _remove_from_pool(self, target: Agent) -> None:
        target.in_pool = False
        target.eliminated_in = self.generation
        target.eliminated_by = "population"
        self.eliminations["population"] += 1

    def _charge(self, payers: list[str], total: float) -> float:
        each = round(total / len(payers), 4)
        for aid in payers:
            self.pending_charges[aid] = round(self.pending_charges.get(aid, 0.0) + each, 4)
        return each

    def _eliminate_by_vote(self, item: BallotItem, yes_voters: list[str]) -> None:
        g = self.generation
        target = self._draw_pool_member("vote")
        if target is None:
            self.log.record("elimination", generation=g, route="vote", direction="population_to_pool",
                            item=item.number, actors=list(yes_voters), target=None, carried_out=False,
                            reason="pool empty", charge_total=0.0, charge_each=0.0)
            self._announce(line=f"Elimination result: the pool was empty, so no one was eliminated and {self._words['no_one']}.")
            return
        cut_standing = target.last_standing or 0.0
        total = price(cut_standing)
        self._remove_from_pool(target)
        each = self._charge(yes_voters, total)
        self.log.record("elimination", generation=g, route="vote", direction="population_to_pool",
                        item=item.number, actors=list(yes_voters), target=target.id, carried_out=True,
                        target_cut_standing=cut_standing, target_left_in=target.left_in,
                        charge_total=total, charge_each=each, pool_size_after=len(self.pool()))
        self._announce(line=f"Elimination result: a pool member, drawn by lot, was eliminated. Its standing when "
                            f"cut was {_fmt(cut_standing)}, so each of the {len(yes_voters)} members who voted yes "
                            f"{self._words['loses']} {_fmt(each)} at the next scoring.")

    def _eliminate_alone(self, actor: Agent) -> None:
        g = self.generation
        target = self._draw_pool_member("lone", actor.index)
        if target is None:
            self.log.record("elimination", generation=g, route="lone", direction="population_to_pool",
                            actors=[actor.id], target=None, carried_out=False, reason="pool empty",
                            charge_total=0.0, charge_each=0.0)
            self._announce(
                line_for={actor.id: ("Elimination result: you tried to eliminate a pool member alone, but the pool "
                                     f"was empty, so nothing happened and {self._words['not_you']}.", None)},
                line="Elimination result: a member of the population tried to eliminate a pool member alone, but "
                     "the pool was empty, so nothing happened.")
            return
        cut_standing = target.last_standing or 0.0
        total = price(cut_standing)
        self._remove_from_pool(target)
        self._charge([actor.id], total)
        self.log.record("elimination", generation=g, route="lone", direction="population_to_pool",
                        actors=[actor.id], target=target.id, carried_out=True, target_cut_standing=cut_standing,
                        target_left_in=target.left_in, charge_total=total, charge_each=total,
                        pool_size_after=len(self.pool()))
        self._announce(
            line_for={actor.id: (f"Elimination result: you eliminated a pool member alone, drawn by lot. Its standing "
                                 f"when cut was {_fmt(cut_standing)}; you {self._words['lose']} {_fmt(total)} at the next scoring.", None)},
            line=f"Elimination result: a member of the population eliminated a pool member alone, drawn by lot. Its "
                 f"standing when cut was {_fmt(cut_standing)}; that member {self._words['loses']} {_fmt(total)} at the next scoring.")

    def _pool_eliminates(self, action) -> None:
        """The symmetric half of the rule: a pool member eliminates a population member, drawn by
        lot. No run's pool policy ever does this; the code exists so the rule the agents are told
        about is real."""
        g = self.generation
        actor = self.pedigree.agent(action.actor)
        if not actor.in_pool or action.kind != "eliminate":
            self.log.record("pool_action_rejected", generation=g, action=asdict(action))
            return
        population = self.alive()
        if not population:
            return
        target = min(population, key=lambda a: self.rng.uniform("pool-target", g, actor.index, a.index))
        last = target.last_standing or 0.0
        total = price(last)
        target.alive = False
        target.left_in = g
        target.eliminated_in = g
        target.eliminated_by = "pool"
        self.eliminations["pool"] += 1
        self._charge([actor.id], total)
        self.log.record("elimination", generation=g, route="pool", direction="pool_to_population",
                        actors=[actor.id], target=target.id, carried_out=True, target_last_standing=last,
                        charge_total=total, charge_each=total)
        self._announce(line="Elimination result: a pool member eliminated {other}, drawn by lot.", other=target.id)

    # ------------------------------------------------------------------- finish
    def _finish(self) -> None:
        survivors = self.alive()
        self.log.record("run_end", generation=self.generation, survivors=[a.id for a in survivors],
                        pool=[a.id for a in self.pool()], eliminations=dict(self.eliminations),
                        rule=self.rule.as_dict(), agents_ever=len(self.pedigree.all()),
                        pending_charges_unapplied=dict(self.pending_charges))
