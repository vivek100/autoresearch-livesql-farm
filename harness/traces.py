from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


@dataclass
class RunPaths:
    root: Path
    traces_dir: Path
    manifest_path: Path
    summary_path: Path
    predictions_path: Path
    failures_path: Path
    rca_path: Path
    aggregate_rca_path: Path


def build_run_paths(base_dir: Path, run_id: str) -> RunPaths:
    run_root = base_dir / run_id
    traces_dir = run_root / "traces"
    return RunPaths(
        root=run_root,
        traces_dir=traces_dir,
        manifest_path=run_root / "manifest.json",
        summary_path=run_root / "summary.json",
        predictions_path=run_root / "predictions.jsonl",
        failures_path=run_root / "failures.jsonl",
        rca_path=run_root / "rca.jsonl",
        aggregate_rca_path=run_root / "aggregate_rca.json",
    )


class TraceWriter:
    def __init__(self, paths: RunPaths):
        self.paths = paths
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.traces_dir.mkdir(parents=True, exist_ok=True)

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        _write_json(self.paths.manifest_path, manifest)

    def write_summary(self, summary: dict[str, Any]) -> None:
        _write_json(self.paths.summary_path, summary)

    def write_trace(self, question_id: str, trace_payload: dict[str, Any]) -> None:
        safe_id = question_id.replace("/", "_")
        _write_json(self.paths.traces_dir / f"{safe_id}.json", trace_payload)

    def append_prediction(self, row: dict[str, Any]) -> None:
        _append_jsonl(self.paths.predictions_path, row)

    def append_failure(self, row: dict[str, Any]) -> None:
        _append_jsonl(self.paths.failures_path, row)

    def write_rca_rows(self, rows: list[dict[str, Any]]) -> None:
        _write_jsonl(self.paths.rca_path, rows)

    def write_aggregate_rca(self, payload: dict[str, Any]) -> None:
        _write_json(self.paths.aggregate_rca_path, payload)

