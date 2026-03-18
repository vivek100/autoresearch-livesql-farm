from __future__ import annotations

"""
Local benchmark runner that writes all artifacts to disk.

This module is intentionally local-first:
- no required W&B/Weave dependency in the critical path
- every run produces inspectable JSON/JSONL outputs
"""

import json
import os
import random
import sqlite3
import string
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
REPO_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from candidate.agent_graph import run_candidate_question  # noqa: E402
from harness.rca import generate_rca_for_run  # noqa: E402
from harness.scorer import extract_gold_value, score  # noqa: E402
from harness.traces import TraceWriter, build_run_paths  # noqa: E402
from harness.versions import BENCHMARK_VERSION, RCA_VERSION, SCORER_VERSION, TRACE_SCHEMA_VERSION  # noqa: E402


def load_env() -> None:
    local_env = PROJECT_ROOT / ".env"
    root_env = REPO_ROOT / ".env"
    if local_env.exists():
        load_dotenv(local_env)
    if root_env.exists():
        load_dotenv(root_env, override=False)


def resolve_spider_root() -> Path:
    env_root = os.environ.get("SPIDER_ROOT")
    if env_root:
        return Path(env_root)
    fallback = Path(r"C:\spider_data\spider_data")
    if fallback.exists():
        return fallback
    local_new = PROJECT_ROOT / "data" / "spider"
    if local_new.exists():
        return local_new
    return Path("analytics-agent/data/spider")


def run_gold_sql(db_path: Path, sql: str) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db_path.as_posix())
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def random_suffix(length: int = 8) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def get_git_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or "unknown"
        return "unknown"
    except Exception:
        return "unknown"


def build_run_id(prefix: str = "run") -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}-{random_suffix(6)}"


def _dataset_slice(dev_rows: list[dict[str, Any]], split: str, limit: int, offset: int) -> list[dict[str, Any]]:
    if split == "smoke":
        return dev_rows[offset : offset + min(limit, 30)]
    return dev_rows[offset : offset + limit]


def run_benchmark(
    split: str = "smoke",
    limit: int = 25,
    offset: int = 0,
    model_name: str = "mistral-small-latest",
    run_id: str | None = None,
    lane: str = "small",
    parent_run_id: str | None = None,
    parent_git_ref: str | None = None,
    experiment_name: str | None = None,
    notes: str | None = None,
    candidate_version: str = "candidate_v0",
    prompt_version: str = "prompt_v0",
    tags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run benchmark slice and emit manifest, summary, traces, predictions, failures, and RCA."""
    load_env()
    spider_root = resolve_spider_root()
    dev_path = spider_root / "dev.json"
    db_root = spider_root / "database"
    if not dev_path.exists():
        raise FileNotFoundError(f"dev.json not found at {dev_path.as_posix()}")
    if not db_root.exists():
        raise FileNotFoundError(f"database path not found at {db_root.as_posix()}")

    with dev_path.open("r", encoding="utf-8") as f:
        dev_rows = json.load(f)
    selected = _dataset_slice(dev_rows=dev_rows, split=split, limit=limit, offset=offset)
    if not selected:
        raise RuntimeError("No benchmark rows selected. Adjust limit/offset.")

    resolved_run_id = run_id or build_run_id(prefix=split)
    paths = build_run_paths(base_dir=PROJECT_ROOT / "runs", run_id=resolved_run_id)
    writer = TraceWriter(paths=paths)

    manifest = {
        "run_id": resolved_run_id,
        "timestamp": now_iso(),
        "experiment_name": experiment_name or resolved_run_id,
        "notes": notes,
        "split": split,
        "limit": limit,
        "offset": offset,
        "selected_rows": len(selected),
        "model_name": model_name,
        "model_lane": lane,
        "candidate_version": candidate_version,
        "prompt_version": prompt_version,
        "tags": tags or [],
        "benchmark_version": BENCHMARK_VERSION,
        "scorer_version": SCORER_VERSION,
        "rca_version": RCA_VERSION,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "git_sha": get_git_sha(),
        "parent_run_id": parent_run_id,
        "parent_git_ref": parent_git_ref,
        "extra_metadata": extra_metadata or {},
    }
    writer.write_manifest(manifest)

    total = 0
    correct_count = 0
    skipped = 0
    latencies: list[int] = []

    for idx, ex in enumerate(selected):
        # Each selected Spider row becomes one local trace + prediction record.
        question_id = f"spider_{offset + idx}"
        question = str(ex.get("question"))
        db_id = str(ex.get("db_id"))
        gold_sql = str(ex.get("query"))
        db_path = db_root / db_id / f"{db_id}.sqlite"
        if not db_path.exists():
            skipped += 1
            continue

        gold_rows = run_gold_sql(db_path=db_path, sql=gold_sql)
        expected_value = extract_gold_value(gold_rows)
        candidate = run_candidate_question(
            question=question,
            db_id=db_id,
            db_path=db_path.as_posix(),
            model_name=model_name,
        )
        candidate_dict = candidate.to_dict()
        latencies.append(int(candidate_dict.get("artifacts", {}).get("latency_ms", 0)))
        is_correct = score(candidate.answer.normalized, gold_rows)
        total += 1
        if is_correct:
            correct_count += 1

        prediction_row = {
            "timestamp": now_iso(),
            "sequence_index": idx,
            "question_id": question_id,
            "question": question,
            "db_id": db_id,
            "correct": is_correct,
            "status": candidate.status,
            "error": candidate.error,
            "expected_value": expected_value,
            "answer_raw": candidate.answer.raw,
            "answer_normalized": candidate.answer.normalized,
            "answer_kind": candidate.answer.kind,
            "answer_text": candidate_dict.get("artifacts", {}).get("answer_text"),
            "final_sql": candidate.final_sql,
            "steps_used": candidate.steps_used,
            "latency_ms": candidate_dict.get("artifacts", {}).get("latency_ms"),
            "model_name": model_name,
            "model_lane": lane,
            "split": split,
            "candidate_version": candidate_version,
            "prompt_version": prompt_version,
            "tags": tags or [],
            "run_id": resolved_run_id,
            "experiment_name": manifest["experiment_name"],
            "observability": candidate_dict.get("artifacts", {}).get("observability"),
            "phoenix_span_count": len(candidate_dict.get("artifacts", {}).get("phoenix_spans", [])),
        }
        writer.append_prediction(prediction_row)
        if not is_correct:
            writer.append_failure(prediction_row)

        trace_payload = {
            # Per-question trace file keeps enough evidence for RCA/debugging.
            "trace_id": f"trace_{question_id}",
            "question_id": question_id,
            "question": question,
            "db_id": db_id,
            "candidate": {
                "git_sha": manifest["git_sha"],
                "model_name": model_name,
                "runtime": candidate.runtime,
                "candidate_version": candidate_version,
                "prompt_version": prompt_version,
            },
            "events": candidate_dict.get("artifacts", {}).get("trace_events", []),
            "phoenix_spans": candidate_dict.get("artifacts", {}).get("phoenix_spans", []),
            "final": {
                "status": candidate.status,
                "final_sql": candidate.final_sql,
                "answer_raw": candidate.answer.raw,
                "answer_normalized": candidate.answer.normalized,
                "answer_text": candidate_dict.get("artifacts", {}).get("answer_text"),
            },
            "observability": candidate_dict.get("artifacts", {}).get("observability"),
            "score": {
                "correct": is_correct,
                "gold_value": expected_value,
            },
        }
        writer.write_trace(question_id=question_id, trace_payload=trace_payload)

    accuracy = (correct_count / total) if total else 0.0
    mean_latency = (sum(latencies) / len(latencies)) if latencies else 0.0
    summary = {
        "run_id": resolved_run_id,
        "timestamp": now_iso(),
        "experiment_name": manifest["experiment_name"],
        "notes": notes,
        "questions_total": total,
        "questions_correct": correct_count,
        "questions_skipped": skipped,
        "accuracy": accuracy,
        "latency_mean_ms": round(mean_latency, 2),
        "model_name": model_name,
        "model_lane": lane,
        "split": split,
        "candidate_version": candidate_version,
        "prompt_version": prompt_version,
        "tags": tags or [],
        "benchmark_version": BENCHMARK_VERSION,
        "scorer_version": SCORER_VERSION,
        "rca_version": RCA_VERSION,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "extra_metadata": extra_metadata or {},
    }
    writer.write_summary(summary)

    rca_rows, rca_summary = generate_rca_for_run(paths.root)
    writer.write_rca_rows(rca_rows)
    writer.write_aggregate_rca(rca_summary)

    return {
        "run_id": resolved_run_id,
        "run_dir": paths.root.as_posix(),
        "summary": summary,
        "rca_summary": rca_summary,
    }
