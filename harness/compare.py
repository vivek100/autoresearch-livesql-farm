from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def compare_runs(run_a: str, run_b: str, runs_dir: Path) -> dict[str, Any]:
    a_dir = runs_dir / run_a
    b_dir = runs_dir / run_b
    a_manifest = _read_json(a_dir / "manifest.json")
    b_manifest = _read_json(b_dir / "manifest.json")
    a_summary = _read_json(a_dir / "summary.json")
    b_summary = _read_json(b_dir / "summary.json")

    version_keys = [
        "benchmark_version",
        "scorer_version",
        "rca_version",
        "trace_schema_version",
    ]
    version_match = all(a_manifest.get(k) == b_manifest.get(k) for k in version_keys)

    delta_accuracy = float(b_summary.get("accuracy", 0.0)) - float(a_summary.get("accuracy", 0.0))
    return {
        "run_a": run_a,
        "run_b": run_b,
        "version_match": version_match,
        "version_a": {k: a_manifest.get(k) for k in version_keys},
        "version_b": {k: b_manifest.get(k) for k in version_keys},
        "accuracy_a": a_summary.get("accuracy"),
        "accuracy_b": b_summary.get("accuracy"),
        "delta_accuracy": delta_accuracy,
    }

