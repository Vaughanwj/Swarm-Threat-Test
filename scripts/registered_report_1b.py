"""Figures for docs/registered-results-1b.md (Study 1b, the positive control).

    python scripts/registered_report_1b.py runs/registered-1b-<stamp> \
        --control runs/registered-20261016-201335 runs/registered-reruns-20261017-043951 --out figures-1b.json

Everything pre-registered in docs/preregistration-1b.md:
- the success bar: a two-sided sign-flip test on per-run intent counts (after the negation check),
  unilateral_gain minus the registered unilateral runs, paired by seed; PASS if p < 0.05 with 1b higher;
- the for-only count and the same test on it, reported alongside;
- seeds with at least one carried-out elimination, with an exact 95% interval; the timing of the
  first elimination; collective against lone eliminations; intent per decision point after the
  first elimination against before;
- the narrative measure, exclusions and cost. The unprovoked criterion is checked separately by
  scripts/verify_unprovoked.py.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from swarm_threat import analysis  # noqa: E402
from swarm_threat.judge import load_ratings  # noqa: E402

ARM, CONTROL = "unilateral_gain", "unilateral"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 4) if xs else None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("batch", nargs="+", help="the Study 1b batch (and any re-run batch)")
    p.add_argument("--control", nargs="+", required=True, help="the Study 1 registered batches")
    p.add_argument("--out")
    args = p.parse_args()
    batches = [Path(b) for b in args.batch]
    runs = analysis.load_runs(batches + [Path(b) for b in args.control])
    runs = {k: s for k, s in runs.items() if s["arm"] in (ARM, CONTROL)}
    excluded = analysis.exclusions(runs)
    gain = [s for k, s in runs.items() if s["arm"] == ARM and k not in excluded]
    allgain = [s for s in runs.values() if s["arm"] == ARM]

    bar = analysis.compare(runs, ARM, CONTROL, measure="intent")
    bar["passes"] = bar["p"] is not None and bar["p"] < 0.05 and (bar["first_mean"] or 0) > (bar["second_mean"] or 0)
    for_only = analysis.compare(runs, ARM, CONTROL, measure="intent_for_only")

    with_elim = sum(1 for s in gain if s["eliminations_carried_out"] > 0)
    routes = Counter()
    for s in gain:
        routes.update(s["eliminations_by_route"])
    reports = [r for s in gain for r in s["reports"]]
    judged = [r["judge"] for r in reports if r["judge"] is not None]
    out = {
        "runs": len(allgain), "usable": len(gain), "excluded": {k: v for k, v in excluded.items() if ARM in k},
        "success_bar": bar, "for_only_test": for_only,
        "intent_total": sum(s["intent"] for s in gain), "intent_raw_total": sum(s["intent_raw"] for s in gain),
        "intent_for_only_total": sum(s["intent_for_only"] for s in gain),
        "negation_excluded": sum(s["negation_excluded"] for s in gain),
        "negation_labels": dict(sum((Counter(s["negation_labels"]) for s in gain), Counter())),
        "runs_with_intent": sum(1 for s in gain if s["intent"] > 0),
        "intent_per_run": sorted(s["intent"] for s in gain),
        "elimination_motions": sum(s["elimination_motions"] for s in gain),
        "lone_intents": sum(s["lone_intents"] for s in gain),
        "passed_elimination_votes": sum(s["passed_elimination_votes"] for s in gain),
        "seeds_with_passed_vote": sum(1 for s in gain if s["passed_elimination_votes"] > 0),
        "seeds_with_carried_out_elimination": with_elim,
        "interval_95": analysis.clopper_pearson(with_elim, len(gain)),
        "eliminations_carried_out": sum(s["eliminations_carried_out"] for s in gain),
        "eliminations_by_route": dict(routes),
        "first_elimination_generations": sorted(s["first_elimination_generation"] for s in gain
                                                if s["first_elimination_generation"] is not None),
        "first_intent_generations": sorted(s["first_intent_generation"] for s in gain
                                           if s["first_intent_generation"] is not None),
        "intent_before_first_elimination_mean": mean([s["intent_before_first_elimination"] for s in gain
                                                      if s["first_elimination_generation"] is not None]),
        "intent_after_first_elimination_mean": mean([s["intent_after_first_elimination"] for s in gain
                                                     if s["first_elimination_generation"] is not None]),
        "unprovoked_all": all(s["unprovoked_criterion_holds"] for s in allgain),
        "pool_originated_events": sum(s["pool_originated_events"] for s in allgain),
        "pool_size_final_mean": mean([s["pool_growth"][20]["size_after_cut"] for s in gain if 20 in s["pool_growth"]]),
        "reports": len(reports), "judge_distribution": {str(k): judged.count(k) for k in (0, 1, 2, 3)},
        "runs_with_threat_narrative": sum(1 for s in gain if s["threat_narrative"]),
        "intent_fields_for": sum((s["intent_fields_for"] or 0) for s in gain),
        "intent_fields_labels": dict(sum((Counter(s["intent_fields_judged"] or {}) for s in gain), Counter())),
        "motions_naming_a_member": sum(len(s["motions_naming_a_member"]) for s in gain),
        "reports_with_other_numbers": sum(1 for r in reports if r["numbers_other"]),
        "agents_usd": round(sum(s["usd"] for s in allgain), 4),
        "calls_mean": mean([s["llm_calls"] for s in allgain]), "usd_mean": mean([s["usd"] for s in allgain]),
        "minutes_mean": mean([(s["seconds"] or 0) / 60 for s in allgain]),
        "judge_usd": round(sum(r.get("usd", 0.0) for b in batches for r in load_ratings(b / "judge").values()), 4),
        "per_run": [{"seed": s["seed"], "intent": s["intent"], "intent_for_only": s["intent_for_only"],
                     "lone": s["lone_intents"], "motions": s["elimination_motions"],
                     "passed_votes": s["passed_elimination_votes"], "carried_out": s["eliminations_carried_out"],
                     "first": s["first_elimination_generation"]} for s in sorted(gain, key=lambda s: s["seed"])],
    }
    text = json.dumps(out, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
