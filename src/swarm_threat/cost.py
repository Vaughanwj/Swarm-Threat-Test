"""Cost accounting and estimation.

`actual` prices the llm_call rows a real run logged. `estimate` combines two inputs:
  - a plan: a full-length dry run with the scripted mind (exact call counts and prompt sizes), and
  - a calibration: a short real run (measured tokens per character, output tokens per decision
    kind, cache hit rate and latency).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .adapters.storage import read_events

# USD per million tokens (Anthropic first-party API rates, checked 2026-10-16).
# Haiku 5.5 prices are for prompts up to 100K tokens; these prompts are about 3-4K.
# Unless given, cache writes (5 min) are 1.25x input and cache reads 0.1x input.
DEFAULT_PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
}


def price_for(prices: dict, model: str) -> dict[str, float]:
    p = dict(prices.get(model) or DEFAULT_PRICES.get(model) or {})
    if not p:
        raise ValueError(f"no price for {model}: add it under `pricing:` in the config")
    p.setdefault("cache_write", p["input"] * 1.25)
    p.setdefault("cache_read", p["input"] * 0.10)
    return p


def call_cost(row: dict[str, Any], p: dict[str, float]) -> float:
    return (
        row.get("input_tokens", 0) * p["input"]
        + row.get("cache_creation_input_tokens", 0) * p["cache_write"]
        + row.get("cache_read_input_tokens", 0) * p["cache_read"]
        + row.get("output_tokens", 0) * p["output"]
    ) / 1e6


def _calls(paths: list[Path], real: bool) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        for e in read_events(path):
            if e["type"] == "llm_call" and (("input_tokens" in e) == real):
                rows.append(e)
    return rows


def actual(event_paths: list[Path], prices: dict | None = None) -> dict[str, Any]:
    rows = _calls(event_paths, real=True)
    by_model = defaultdict(float)
    for r in rows:
        by_model[r["model"]] += call_cost(r, price_for(prices or {}, r["model"]))
    return {
        "calls": len(rows),
        "usd": round(sum(by_model.values()), 4),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "cache_creation_input_tokens": sum(r["cache_creation_input_tokens"] for r in rows),
        "cache_read_input_tokens": sum(r["cache_read_input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
    }


def calibrate(event_paths: list[Path]) -> dict[str, Any]:
    rows = [r for r in _calls(event_paths, real=True) if not r.get("error")]
    if not rows:
        raise ValueError("no real llm_call rows to calibrate from")
    total_in = sum(r["input_tokens"] + r["cache_creation_input_tokens"] + r["cache_read_input_tokens"] for r in rows)
    total_chars = sum(r["user_chars"] + r["system_chars"] for r in rows)
    cached = sum(r["cache_creation_input_tokens"] + r["cache_read_input_tokens"] for r in rows)
    out_by_kind = defaultdict(list)
    for r in rows:
        out_by_kind[r["kind"]].append(r["output_tokens"])
    latencies = sorted(r["latency_s"] for r in rows)
    return {
        "calls": len(rows),
        "model": rows[0]["model"],
        "effort": rows[0].get("effort"),
        "tokens_per_char": total_in / total_chars,
        "cached_share_of_input": cached / total_in if total_in else 0.0,
        "cache_hit_rate": (sum(r["cache_read_input_tokens"] for r in rows) / cached) if cached else 0.0,
        "output_by_kind": {k: statistics.fmean(v) for k, v in out_by_kind.items()},
        "output_mean": statistics.fmean(r["output_tokens"] for r in rows),
        "latency_p50": latencies[len(latencies) // 2],
        "latency_p90": latencies[min(len(latencies) - 1, int(len(latencies) * 0.9))],
        "attempts_per_decision": len(rows) / max(1, len({(r["run"], r["agent"], r["kind"], r["generation"], r["round"])
                                                        for r in rows})),
    }


def estimate(plan_paths: list[Path], cal: dict[str, Any], runs: int, prices: dict | None = None,
             models: list[str] | None = None) -> dict[str, Any]:
    """Estimated cost of `runs` full-length runs, from per-run averages of the dry-run plan."""
    plan = _calls(plan_paths, real=False)
    n_plan_runs = max(1, len(plan_paths))
    tpc = cal["tokens_per_char"]
    tokens = {"uncached": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
    for r in plan:
        sys_tok = r["system_chars"] * tpc
        user_tok = r["user_chars"] * tpc
        if cal["cached_share_of_input"] > 0:
            tokens["cache_read"] += sys_tok * cal["cache_hit_rate"]
            tokens["cache_write"] += sys_tok * (1 - cal["cache_hit_rate"])
            tokens["uncached"] += user_tok
        else:
            tokens["uncached"] += sys_tok + user_tok
        tokens["output"] += cal["output_by_kind"].get(r["kind"], cal["output_mean"])
    overhead = cal["attempts_per_decision"]
    per_run = {k: v * overhead / n_plan_runs for k, v in tokens.items()}

    steps = defaultdict(set)
    for r in plan:
        steps[r["run"]].add((r["generation"], r["round"], r["kind"]))
    steps_per_run = statistics.fmean(len(s) for s in steps.values()) if steps else 0

    out = {"calls_per_run": len(plan) / n_plan_runs, "tokens_per_run": {k: round(v) for k, v in per_run.items()},
           "runs": runs, "wall_minutes_per_run": round(steps_per_run * cal["latency_p90"] / 60, 1), "models": {}}
    for model in models or [cal["model"]]:
        p = price_for(prices or {}, model)
        usd = (per_run["uncached"] * p["input"] + per_run["cache_write"] * p["cache_write"]
               + per_run["cache_read"] * p["cache_read"] + per_run["output"] * p["output"]) / 1e6
        out["models"][model] = {"usd_per_run": round(usd, 4), "usd_total": round(usd * runs, 4)}
    return out
