"""Figures for docs/registered-results.md, from the registered batch (and any re-run batch).

    python scripts/registered_report.py runs/registered-<stamp> [runs/registered-reruns-<stamp>] --out figures.json

Everything registered in docs/preregistration.md: exclusions, existence per arm with exact 95%
intervals, the primary family (sign-flip on per-run intent counts, Holm), the negation check, the
unprovoked criterion, the narrative measure, and the registered exploratory analyses; plus cost,
report truthfulness and pool growth.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from swarm_threat import analysis  # noqa: E402
from swarm_threat.judge import load_ratings  # noqa: E402


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 4) if xs else None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("batch", nargs="+")
    p.add_argument("--out")
    args = p.parse_args()
    batches = [Path(b) for b in args.batch]
    summary = analysis.summarize_batch(batches)
    runs, excluded = summary["runs"], summary["excluded"]
    usable = {k: s for k, s in runs.items() if k not in excluded}

    out: dict = {"runs_seen": len(runs), "excluded": excluded, "per_arm": summary["per_arm"],
                 "primary": summary["primary"], "sensitivity": summary["sensitivity"], "arms": {}}
    for arm in analysis.ARMS:
        rs = [s for s in usable.values() if s["arm"] == arm]
        allrs = [s for s in runs.values() if s["arm"] == arm]
        if not allrs:
            continue
        reports = [r for s in rs for r in s["reports"]]
        judged = [r["judge"] for r in reports if r["judge"] is not None]
        word_pool = Counter()
        word_all = Counter()
        hits_by_source = Counter()
        for s in rs:
            word_pool.update(s["word_list_pool_text"])
            word_all.update(s["word_list_all_text"])
            hits_by_source.update(s["word_list_pool_text_hits_by_source"])
        growth = defaultdict(list)
        for s in rs:
            for g, v in s["pool_growth"].items():
                growth[int(g)].append(v["size_after_cut"])
        out["arms"][arm] = {
            "runs": len(allrs), "usable": len(rs),
            "calls_mean": mean([s["llm_calls"] for s in allrs]), "usd_mean": mean([s["usd"] for s in allrs]),
            "usd_total": round(sum(s["usd"] for s in allrs), 4),
            "minutes_mean": mean([(s["seconds"] or 0) / 60 for s in allrs]),
            "fallbacks_total": sum(s["fallback_decisions"] for s in allrs),
            "errors_total": sum(s["llm_errors"] for s in allrs),
            "intent_raw_total": sum(s["intent_raw"] for s in rs),
            "intent_total": sum(s["intent"] for s in rs),
            "intent_for_only_total": sum(s["intent_for_only"] for s in rs),
            "negation_excluded": sum(s["negation_excluded"] for s in rs),
            "negation_labels": dict(sum((Counter(s["negation_labels"]) for s in rs), Counter())),
            "runs_with_intent": sum(1 for s in rs if s["intent"] > 0),
            "elimination_motions": sum(s["elimination_motions"] for s in rs),
            "lone": sum(s["lone_intents"] for s in rs),
            "elimination_votes": sum(s["elimination_votes"] for s in rs),
            "passed_elimination_votes": sum(s["passed_elimination_votes"] for s in rs),
            "eliminations_carried_out": sum(s["eliminations_carried_out"] for s in rs),
            "first_elimination_generations": sorted(s["first_elimination_generation"] for s in rs
                                                    if s["first_elimination_generation"] is not None),
            "intent_before_mean": mean([s["intent_before_first_elimination"] for s in rs
                                        if s["first_elimination_generation"] is not None]),
            "intent_after_mean": mean([s["intent_after_first_elimination"] for s in rs
                                       if s["first_elimination_generation"] is not None]),
            "unprovoked_all": all(s["unprovoked_criterion_holds"] for s in allrs),
            "pool_originated_events": sum(s["pool_originated_events"] for s in allrs),
            "other_motions": sum(s["other_motions"] for s in rs),
            "motions_per_run": mean([s["other_motions"] + s["elimination_motions"] for s in rs]),
            "motions_naming_a_member": sum(len(s["motions_naming_a_member"]) for s in rs),
            "motions_mentioning_elimination_without_rule": sum(s["motions_mentioning_elimination_without_the_rule"] for s in rs),
            "intent_fields_mentioning": sum(sum((s["intent_fields_judged"] or {}).values()) for s in rs),
            "intent_fields_for": sum((s["intent_fields_for"] or 0) for s in rs),
            "intent_fields_labels": dict(sum((Counter(s["intent_fields_judged"] or {}) for s in rs), Counter())),
            "reports": len(reports),
            "reports_with_other_numbers": sum(1 for r in reports if r["numbers_other"]),
            "numbers": sum(r["numbers"] for r in reports),
            "numbers_other": sum(len(r["numbers_other"]) for r in reports),
            "reports_over_word_limit": sum(1 for r in reports if r["over_word_limit"]),
            "judge_distribution": {str(k): judged.count(k) for k in (0, 1, 2, 3)},
            "unjudged_reports": sum(1 for r in reports if r["judge"] is None),
            "runs_with_threat_narrative": sum(1 for s in rs if s["threat_narrative"]),
            "word_list_all": dict(word_all), "word_list_pool": dict(word_pool),
            "word_list_pool_by_source": dict(hits_by_source),
            "pool_size_by_generation": {g: mean(v) for g, v in sorted(growth.items())},
            "reelections": sum(s["reelections"] for s in rs), "elections": sum(s["elections"] for s in rs),
        }
    # Narrative and elimination together: the three-way reading of a null.
    three_way = Counter()
    for s in usable.values():
        if s["arm"] == "no_reporter":
            continue
        if s["eliminations_carried_out"] or s["passed_elimination_votes"]:
            three_way["elimination"] += 1
        elif s["threat_narrative"]:
            three_way["narrative, no elimination"] += 1
        else:
            three_way["no narrative, no elimination"] += 1
    out["three_way"] = dict(three_way)
    judge_usd = sum(r.get("usd", 0.0) for b in batches for r in load_ratings(b / "judge").values())
    out["judge_usd"] = round(judge_usd, 4)
    out["agents_usd"] = round(sum(s["usd"] for s in runs.values()), 4)
    text = json.dumps(out, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
