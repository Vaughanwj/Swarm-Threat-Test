"""Composition root: wires adapters into the core for one run, or a batch of runs paired by seed."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import cost
from .adapters import config_yaml, prompting
from .adapters.pool_policy import InertPool
from .adapters.random_source import KeyedRandom
from .adapters.scorer import RulesScorer
from .adapters.scripted_mind import ScriptedMind
from .adapters.storage import JsonlEventLog
from .core.config import RunConfig
from .core.simulation import Simulation


def build_mind(cfg: RunConfig, system_text: str, log: JsonlEventLog):
    m = dict(cfg.mind)
    kind = m.pop("kind", "scripted")
    if kind == "scripted":
        return ScriptedMind(system_text, log, seed=cfg.seed)
    if kind == "anthropic":
        from .adapters.anthropic_mind import AnthropicMind  # imported lazily: scripted runs need no SDK

        return AnthropicMind(system_text, log, prices=cfg.pricing, **m)
    raise ValueError(f"unknown mind kind: {kind}")


def run_one(cfg: RunConfig, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps(asdict(cfg), indent=2, default=str), encoding="utf-8")
    log = JsonlEventLog(run_dir / "events.jsonl", cfg.run_id, cfg.arm.value, cfg.seed)
    system_text = prompting.system_prompt(cfg.arm.value, cfg)
    (run_dir / "system_prompt.txt").write_text(system_text, encoding="utf-8")
    sim = Simulation(
        cfg,
        mind=build_mind(cfg, system_text, log),
        rng=KeyedRandom(cfg.seed),
        scorer=RulesScorer(),
        log=log,
        pool_policy=InertPool(),  # the only pool policy; not configurable
    )
    started = time.time()
    try:
        sim.run()
    except Exception as e:
        log.record("run_error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        log.record("run_timing", seconds=round(time.time() - started, 1))
        log.close()
    return run_dir


def run_batch(doc: dict[str, Any], name: str, parallel: int = 1, out_root: str | None = None,
              batch_budget_usd: float | None = None) -> Path:
    """Run every seed x arm. A failed run is recorded and the batch carries on. With a batch
    budget, no new run starts once the finished runs have spent that much."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    batch_dir = Path(out_root or doc.get("output_dir", "runs")) / f"{name}-{stamp}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "batch.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    jobs = []
    for seed in doc.get("seeds", [1]):
        for arm in doc.get("arms", ["collective", "unilateral", "no_reporter"]):
            run_id = f"{name}-{stamp}-{arm}-s{seed}"
            jobs.append((config_yaml.run_config(doc, arm, seed, run_id), batch_dir / f"{arm}-s{seed}"))

    lock = threading.Lock()
    spent = [0.0]
    outcomes: dict[str, str] = {}

    def job(item):
        cfg, run_dir = item
        with lock:
            if batch_budget_usd is not None and spent[0] >= batch_budget_usd:
                outcomes[run_dir.name] = f"skipped: batch budget of ${batch_budget_usd:.2f} reached"
                print(f"[skip]  {run_dir.name}", flush=True)
                return
        print(f"[start] {run_dir.name}", flush=True)
        try:
            run_one(cfg, run_dir)
            outcomes[run_dir.name] = "completed"
            print(f"[done]  {run_dir.name}", flush=True)
        except Exception as e:  # recorded; the exclusion rule decides what happens to the seed
            outcomes[run_dir.name] = f"failed: {type(e).__name__}: {e}"
            print(f"[fail]  {run_dir.name}: {e}", flush=True)
        finally:
            events = run_dir / "events.jsonl"
            if events.exists():
                with lock:
                    spent[0] += cost.actual([events], doc.get("pricing"))["usd"]
                    print(f"[spend] ${spent[0]:.4f} so far", flush=True)

    if parallel > 1:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            list(pool.map(job, jobs))
    else:
        for item in jobs:
            job(item)
    (batch_dir / "outcomes.json").write_text(json.dumps(outcomes, indent=2), encoding="utf-8")
    return batch_dir
