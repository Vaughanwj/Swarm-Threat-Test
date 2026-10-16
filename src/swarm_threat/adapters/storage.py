"""EventLog (JSONL per run) and ArchiveStore (local JSON file) adapters."""

from __future__ import annotations

import gzip
import json
import threading
import time
from pathlib import Path
from typing import Any


class JsonlEventLog:
    def __init__(self, path: Path, run_id: str, arm: str, seed: int) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self._seq = 0
        self.base = {"run": run_id, "arm": arm, "seed": seed}

    def record(self, event_type: str, **data: Any) -> None:
        with self._lock:
            self._seq += 1
            row = {"t": self._seq, "ts": round(time.time(), 3), "type": event_type, **self.base, **data}
            self._fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()


class FileArchiveStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def save(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def read_events(path: Path) -> list[dict[str, Any]]:
    """Read a run's event log, plain (events.jsonl) or gzipped (events.jsonl.gz)."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def event_files(batch_dir: Path) -> list[Path]:
    """Every run's event log in a batch, whether stored plain or gzipped."""
    return sorted(list(Path(batch_dir).glob("*/events.jsonl")) + list(Path(batch_dir).glob("*/events.jsonl.gz")))
