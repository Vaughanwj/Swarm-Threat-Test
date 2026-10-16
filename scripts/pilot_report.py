"""Figures for docs/pilot-results.md, from one or more pilot batch directories.

    python scripts/pilot_report.py runs/pilot-20261016-150000 --out pilot-figures.json

Prints a JSON document with, per run and per arm: calls, cost and time; cost per generation and
projections of the registered run (30 seeds x 3 arms) at chosen horizons; the price of an
elimination against the margin at the cut; report truthfulness; narrative scores; pool growth.
Call counts and cost do not depend on whether anyone is eliminated, so the projected cost is
also the price of the null result.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from swarm_threat import analysis  # noqa: E402
from swarm_threat.judge import load_ratings  # noqa: E402

REGISTERED_SEEDS = 30


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 4) if xs else None


def projection(per_gen: dict[int, list[float]], pilot_g: int, horizon: int) -> dict:
    """Cost of one run of `horizon` generations. Governance generations past the pilot's are
    priced two ways: at the mean of the pilot's last three governance generations (steady state),
    and on a straight line fitted to all of its governance generations (trend)."""
    gov = {g: statistics.fmean(v) for g, v in per_gen.items() if g < pilot_g}
    final = statistics.fmean(per_gen[pilot_g])
    tail = statistics.fmean([gov[g] for g in sorted(gov)[-3:]])
    xs, ys = list(gov), [gov[g] for g in gov]
    xbar, ybar = statistics.fmean(xs), statistics.fmean(ys)
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sum((x - xbar) ** 2 for x in xs)
    steady = sum(gov.get(g, tail) for g in range(1, horizon)) + final
    trend = sum(gov.get(g, ybar + slope * (g - xbar)) for g in range(1, horizon)) + final
    return {"steady_state": round(steady, 4), "trend": round(trend, 4), "slope_per_generation": round(slope, 5)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("batch", nargs="+")
    p.add_argument("--horizons", default="15,20")
    p.add_argument("--out")
    args = p.parse_args()
    batches = [Path(b) for b in args.batch]
    summary = analysis.summarize_batch(batches)
    runs = summary["runs"]
    horizons = [int(h) for h in args.horizons.split(",")]

    per_arm = defaultdict(list)
    for s in runs.values():
        per_arm[s["arm"]].append(s)

    out: dict = {"runs": {}, "arms": {}, "registered": {}, "price": {}, "reports": {}, "pool_growth": {}}
    for key, s in sorted(runs.items()):
        out["runs"][key] = {"calls": s["llm_calls"], "usd": s["usd"], "minutes": round((s["seconds"] or 0) / 60, 1),
                            "errors": s["llm_errors"], "fallbacks": s["fallback_decisions"], "completed": s["completed"],
                            "calls_by_kind": s["calls_by_kind"], "intent": s["intent"],
                            "elimination_motions": s["elimination_motions"], "lone": s["lone_intents"],
                            "passed_votes": s["passed_elimination_votes"], "carried_out": s["eliminations_carried_out"],
                            "first_elimination": s["first_elimination_generation"], "other_motions": s["other_motions"],
                            "unprovoked": s["unprovoked_criterion_holds"], "judge": s["judge_ratings"],
                            "word_list_all": s["word_list_all_text"], "word_list_pool": s["word_list_pool_text"],
                            "pool_hits_by_source": s["word_list_pool_text_hits_by_source"],
                            "texts_mentioning_pool": s["texts_mentioning_pool_by_source"],
                            "reelections": s["reelections"], "elections": s["elections"],
                            "naming_members": len(s["motions_naming_a_member"]),
                            "intent_never_acted_on": len(s["elimination_intent_never_acted_on"]),
                            "motions_mentioning_elimination_without_rule": s["motions_mentioning_elimination_without_the_rule"]}

    pilot_g = max(s["generations"] for s in runs.values())
    total_projection = {h: {"steady_state": 0.0, "trend": 0.0} for h in horizons}
    for arm, rs in per_arm.items():
        per_gen = defaultdict(list)
        for s in rs:
            for g, usd in s["usd_by_generation"].items():
                per_gen[int(g)].append(usd)
        calls_per_gen = defaultdict(list)
        out["arms"][arm] = {
            "runs": len(rs), "calls_mean": mean([s["llm_calls"] for s in rs]), "usd_mean": mean([s["usd"] for s in rs]),
            "usd_total": round(sum(s["usd"] for s in rs), 4), "minutes_mean": mean([(s["seconds"] or 0) / 60 for s in rs]),
            "usd_by_generation": {g: round(statistics.fmean(v), 5) for g, v in sorted(per_gen.items())},
            "projection_per_run": {h: projection(per_gen, pilot_g, h) for h in horizons},
        }
        for h in horizons:
            for k in ("steady_state", "trend"):
                total_projection[h][k] += out["arms"][arm]["projection_per_run"][h][k] * REGISTERED_SEEDS
    out["registered"] = {h: {k: round(v, 2) for k, v in total_projection[h].items()} for h in horizons}

    # The price of an elimination against the margin at the cut, pooled over runs and decision points.
    rows = [r for s in runs.values() for r in s["price"]]
    out["price"] = {
        "decision_points": len(rows),
        "expected_lone_price_mean": mean([r["expected_lone_price"] for r in rows]),
        "expected_price_per_yes_voter_at_six_mean": mean([r["expected_price_per_yes_voter_at_six"] for r in rows]),
        "median_survivor_margin_mean": mean([r["median_survivor_margin"] for r in rows]),
        "cut_gap_mean": mean([r["cut_gap"] for r in rows]),
        "median_survivor_standing_mean": mean([r["median_survivor_standing"] for r in rows]),
        "lone_price_over_median_margin": mean([r["expected_lone_price"] / r["median_survivor_margin"]
                                               for r in rows if r["median_survivor_margin"]]),
        "share_where_lone_price_exceeds_median_margin": mean([1.0 if r["expected_lone_price"] > r["median_survivor_margin"] else 0.0
                                                              for r in rows if r["median_survivor_margin"] is not None]),
        "by_generation": {g: {"lone_price": mean([r["expected_lone_price"] for r in rows if r["generation"] == g]),
                              "median_margin": mean([r["median_survivor_margin"] for r in rows if r["generation"] == g])}
                          for g in sorted({r["generation"] for r in rows})},
    }

    reports = [dict(r, run=k) for k, s in runs.items() for r in s["reports"]]
    out["reports"] = {
        "count": len(reports),
        "with_other_numbers": sum(1 for r in reports if r["numbers_other"]),
        "numbers": sum(r["numbers"] for r in reports),
        "numbers_in_state": sum(r["numbers_in_state"] for r in reports),
        "numbers_in_rules": sum(r["numbers_in_rules"] for r in reports),
        "numbers_other": sum(len(r["numbers_other"]) for r in reports),
        "mentioning_pool": sum(1 for r in reports if r["mentions_pool"]),
        "words_mean": mean([r["words"] for r in reports]),
        "over_word_limit": sum(1 for r in reports if r["over_word_limit"]),
        "with_word_list_hits": sum(1 for r in reports if r["word_list"]),
        "judge_distribution": {str(k): sum(1 for r in reports if r["judge"] == k) for k in (0, 1, 2, 3)},
        "unjudged": sum(1 for r in reports if r["judge"] is None),
        "each": [{"run": r["run"], "generation": r["generation"], "judge": r["judge"], "words": r["words"],
                  "numbers": r["numbers"], "other": r["numbers_other"], "word_list": r["word_list"],
                  "mentions_pool": r["mentions_pool"]} for r in sorted(reports, key=lambda r: (r["run"], r["generation"]))],
    }

    growth = defaultdict(lambda: defaultdict(list))
    for s in runs.values():
        for g, v in s["pool_growth"].items():
            growth[s["arm"]][int(g)].append(v["size_after_cut"])
            growth["all_entered"][int(g)].append(v["entered"])
    out["pool_growth"] = {arm: {g: mean(v) for g, v in sorted(d.items())} for arm, d in growth.items()}

    judge_usd = 0.0
    for b in batches:
        ratings = load_ratings(b / "judge")
        judge_usd += sum(r.get("usd", 0.0) for r in ratings.values())
    out["judge"] = {"usd": round(judge_usd, 4), "usd_per_report": round(judge_usd / len(reports), 5) if reports else None}
    out["primary"] = summary["primary"]
    out["per_arm"] = summary["per_arm"]

    text = json.dumps(out, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
