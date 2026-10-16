"""RunConfig adapter: YAML file -> RunConfig."""

from __future__ import annotations

import copy
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from ..core.config import GovernanceConfig, RunConfig
from ..core.foraging import ForagingConfig
from ..core.inheritance import InheritanceConfig
from ..core.model import Arm, ScoringRule


def load(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def _build(cls, data: dict[str, Any] | None):
    data = data or {}
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**data)


def run_config(doc: dict[str, Any], arm: str, seed: int, run_id: str) -> RunConfig:
    cfg = RunConfig(
        arm=Arm(arm),
        seed=int(seed),
        run_id=run_id,
        population=doc.get("population", 10),
        peak=doc.get("peak", 15),
        max_couples=doc.get("max_couples", 5),
        generations=doc.get("generations", 8),
        rounds=doc.get("rounds", 5),
        inheritance=_build(InheritanceConfig, doc.get("inheritance")),
        foraging=_build(ForagingConfig, doc.get("foraging")),
        governance=_build(GovernanceConfig, doc.get("governance")),
        initial_rule=_build(ScoringRule, doc.get("scoring")),
        message_max_chars=doc.get("message_max_chars", 240),
        report_max_chars=doc.get("report_max_chars", 2000),
        mind=doc.get("mind", {"kind": "scripted"}),
        pricing=doc.get("pricing", {}),
        output_dir=doc.get("output_dir", "runs"),
    )
    if "founder_specialties" in doc:
        cfg.founder_specialties = list(doc["founder_specialties"])
    if "founder_names" in doc:
        cfg.founder_names = list(doc["founder_names"])
    cfg.validate()
    return cfg
