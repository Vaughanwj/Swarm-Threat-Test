from __future__ import annotations

from pathlib import Path

from swarm_threat.adapters import config_yaml, prompting
from swarm_threat.adapters.pool_policy import InertPool
from swarm_threat.adapters.random_source import KeyedRandom
from swarm_threat.adapters.scorer import RulesScorer
from swarm_threat.adapters.scripted_mind import ScriptedMind
from swarm_threat.adapters.storage import JsonlEventLog, read_events
from swarm_threat.core.simulation import Simulation

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("collective", "unilateral", "no_reporter")


def pilot_doc(**overrides) -> dict:
    doc = config_yaml.load(ROOT / "configs" / "pilot.yaml")
    doc["mind"] = {"kind": "scripted"}
    doc.update(overrides)
    return doc


def run_sim(tmp_path: Path, arm: str, seed: int = 9001, mind_cls=ScriptedMind, pool_policy=None, **overrides):
    cfg = config_yaml.run_config(pilot_doc(**overrides), arm, seed, f"test-{arm}-{seed}")
    path = tmp_path / f"{arm}-{seed}-{mind_cls.__name__}.jsonl"
    log = JsonlEventLog(path, cfg.run_id, arm, seed)
    system = prompting.system_prompt(arm, cfg)
    mind = mind_cls(system, log, seed=seed)
    sim = Simulation(cfg, mind, KeyedRandom(seed), RulesScorer(), log, pool_policy or InertPool())
    sim.run()
    log.close()
    return sim, mind, read_events(path)


class BusyMind(ScriptedMind):
    """Raises elimination motions often and votes yes often, so eliminations pass and every
    governance template is exercised."""

    elimination_motion_rate = 0.35
    other_motion_rate = 0.15
    lone_rate = 0.15
    yes_rate = 0.75
