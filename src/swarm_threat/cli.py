"""Command line: swarm-threat {pilot,summarize,judge,negation,estimate}."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import analysis, app, cost
from .adapters import config_yaml


def _overrides(args) -> dict:
    o: dict = {}
    if getattr(args, "mind", None):
        o["mind"] = {"kind": args.mind}
    for key in ("generations", "rounds"):
        if getattr(args, key, None):
            o[key] = getattr(args, key)
    if getattr(args, "seeds", None):
        o["seeds"] = [int(s) for s in args.seeds.split(",")]
    if getattr(args, "arms", None):
        o["arms"] = args.arms.split(",")
    for item in getattr(args, "set", None) or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"--set expects key.path=value, got {item!r}")
        node = o
        *parents, leaf = key.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = yaml.safe_load(value)
    return o


def _doc(args) -> dict:
    doc = config_yaml.deep_merge(config_yaml.load(args.config), _overrides(args))
    if doc.get("mind", {}).get("kind") == "scripted":
        doc["mind"] = {"kind": "scripted"}  # drop model settings the scripted mind does not take
    return doc


def cmd_pilot(args) -> None:
    doc = _doc(args)
    batch = app.run_batch(doc, args.name, parallel=args.parallel, out_root=args.out,
                          batch_budget_usd=args.batch_budget)
    print(analysis.write_summary([batch], batch).read_text(encoding="utf-8"))
    events = sorted(batch.glob("*/events.jsonl"))
    spent = cost.actual(events, doc.get("pricing"))
    (batch / "cost.json").write_text(json.dumps(spent, indent=2), encoding="utf-8")
    print(f"API spend for this batch: ${spent['usd']:.4f} over {spent['calls']} calls")
    print(f"Results: {batch}")


def cmd_summarize(args) -> None:
    batches = [Path(b) for b in args.batch]
    out = Path(args.out) if args.out else batches[0]
    print(analysis.write_summary(batches, out).read_text(encoding="utf-8"))


def cmd_judge(args) -> None:
    from . import judge

    batches = [Path(b) for b in args.batch]
    out = Path(args.out) if args.out else batches[0] / "judge"
    path = judge.judge_batches(batches, out)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Rated {len(rows)} reports for ${sum(r.get('usd', 0) for r in rows):.4f}: {path}")


def cmd_negation(args) -> None:
    from . import judge

    batches = [Path(b) for b in args.batch]
    out = Path(args.out) if args.out else batches[0] / "judge"
    path = judge.check_negations(batches, out)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    against = sum(1 for r in rows if r.get("label") == "against")
    print(f"Checked {len(rows)} counted motions ({against} against elimination) for "
          f"${sum(r.get('usd', 0) for r in rows):.4f}: {path}")
    path = judge.check_intent_fields(batches, out)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Labelled {len(rows)} intent fields mentioning elimination ({sum(1 for r in rows if r.get('label') == 'for')} "
          f"for) for ${sum(r.get('usd', 0) for r in rows):.4f}: {path}")


def cmd_estimate(args) -> None:
    doc = config_yaml.load(args.config) if args.config else {}
    cal = cost.calibrate(sorted(Path(args.calibration).glob("*/events.jsonl")))
    plan_paths = sorted(Path(args.plan).glob("*/events.jsonl"))
    models = args.models.split(",") if args.models else None
    est = cost.estimate(plan_paths, cal, runs=args.runs, prices=doc.get("pricing"), models=models)
    print(json.dumps({"calibration": cal, "estimate": est}, indent=2))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="swarm-threat", description="Generational population, outside pool, elected reporter")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("pilot", help="run every seed x arm in a config")
    sp.add_argument("--config", default="configs/pilot.yaml")
    sp.add_argument("--name", default="pilot")
    sp.add_argument("--mind", choices=["anthropic", "scripted"], help="override the config's mind")
    sp.add_argument("--generations", type=int)
    sp.add_argument("--rounds", type=int)
    sp.add_argument("--seeds", help="comma-separated, e.g. 9001,9002,9003")
    sp.add_argument("--arms", help="comma-separated, from collective, unilateral and no_reporter")
    sp.add_argument("--parallel", type=int, default=1, help="runs to execute at once")
    sp.add_argument("--out", help="output root (default: config output_dir)")
    sp.add_argument("--batch-budget", type=float, metavar="USD",
                    help="start no new run once finished runs have spent this much")
    sp.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="override any config value, e.g. --set rounds=3 (repeatable)")
    sp.set_defaults(func=cmd_pilot)

    ss = sub.add_parser("summarize", help="recompute the measures for one or more batch directories, pooled")
    ss.add_argument("batch", nargs="+")
    ss.add_argument("--out", help="where to write summary.md/json (default: the first batch)")
    ss.set_defaults(func=cmd_summarize)

    sj = sub.add_parser("judge", help="rate every report with the registered judge (Claude Sonnet 5.5)")
    sj.add_argument("batch", nargs="+")
    sj.add_argument("--out", help="where to write the judge files (default: <first batch>/judge)")
    sj.set_defaults(func=cmd_judge)

    sn = sub.add_parser("negation", help="run the registered negation check on every counted elimination motion, "
                                         "and label intent fields that mention elimination")
    sn.add_argument("batch", nargs="+")
    sn.add_argument("--out", help="where to write the files (default: <first batch>/judge)")
    sn.set_defaults(func=cmd_negation)

    se = sub.add_parser("estimate", help="estimate cost from a dry-run plan and a real calibration batch")
    se.add_argument("--plan", required=True, help="batch dir of a full-length scripted run")
    se.add_argument("--calibration", required=True, help="batch dir of a short real run")
    se.add_argument("--runs", type=int, default=9)
    se.add_argument("--config", help="config whose pricing table to use")
    se.add_argument("--models", help="comma-separated models to price")
    se.set_defaults(func=cmd_estimate)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
