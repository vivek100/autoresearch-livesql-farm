from __future__ import annotations

import json
from pathlib import Path


REQUIRED_MANIFEST_KEYS = [
    "run_id",
    "benchmark_version",
    "scorer_version",
    "rca_version",
    "trace_schema_version",
]

REQUIRED_FILES = [
    "manifest.json",
    "summary.json",
    "predictions.jsonl",
    "failures.jsonl",
    "rca.jsonl",
    "aggregate_rca.json",
]


def validate_run_folder(run_dir: Path) -> dict[str, object]:
    missing_files = [name for name in REQUIRED_FILES if not (run_dir / name).exists()]

    manifest_path = run_dir / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    missing_manifest_keys = [k for k in REQUIRED_MANIFEST_KEYS if k not in manifest]

    ok = not missing_files and not missing_manifest_keys
    return {
        "ok": ok,
        "run_dir": run_dir.as_posix(),
        "missing_files": missing_files,
        "missing_manifest_keys": missing_manifest_keys,
    }

