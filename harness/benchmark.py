from __future__ import annotations

"""
Local benchmark runner that writes all artifacts to disk.

Uses Ghost PostgreSQL databases for both agent execution and scoring.
"""

import json
import os
import random
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
from harness.scorer import score_execution  # noqa: E402
from harness.traces import TraceWriter, build_run_paths  # noqa: E402
from harness.versions import BENCHMARK_VERSION, RCA_VERSION, SCORER_VERSION, TRACE_SCHEMA_VERSION  # noqa: E402


def load_env() -> None:
    local_env = PROJECT_ROOT / ".env"
    root_env = REPO_ROOT / ".env"
    if local_env.exists():
        load_dotenv(local_env)
    if root_env.exists():
        load_dotenv(root_env, override=False)


def resolve_livesql_root() -> Path:
    env_root = os.environ.get("LIVESQL_ROOT")
    if env_root:
        return Path(env_root)
    local = PROJECT_ROOT / "data" / "livesqlbench"
    if local.exists():
        return local
    return Path("data/livesqlbench")


def resolve_ghost_db_id() -> str:
    db_id = os.environ.get("GHOST_DB_ID")
    if not db_id:
        raise RuntimeError("GHOST_DB_ID is not set. Run 'ghost list' to find your DB ID.")
    return db_id


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
    model_name: str = "openai",
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
    ghost_db_id = resolve_ghost_db_id()
    livesql_root = resolve_livesql_root()
    dev_path = livesql_root / "dataset.json"
    if not dev_path.exists():
        raise FileNotFoundError(f"dataset.json not found at {dev_path.as_posix()}")

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
        "ghost_db_id": ghost_db_id,
        "extra_metadata": extra_metadata or {},
    }
    writer.write_manifest(manifest)

    total = 0
    correct_count = 0
    executable_count = 0
    gold_executable_count = 0
    result_match_count = 0
    result_scoreable_count = 0
    skipped = 0
    latencies: list[int] = []

    for idx, ex in enumerate(selected):
        instance_id = str(ex.get("instance_id", f"q_{idx}"))
        question_id = f"livesql_{instance_id}"
        question = str(ex.get("question"))
        db_id = str(ex.get("db_id"))
        gold_sql_raw = ex.get("sol_sql")
        if isinstance(gold_sql_raw, list) and gold_sql_raw:
            gold_sql = str(gold_sql_raw[0])
        else:
            gold_sql = str(gold_sql_raw) if gold_sql_raw else ""

        candidate = run_candidate_question(
            question=question,
            db_id=db_id,
            ghost_db_id=ghost_db_id,
            model_name=model_name,
        )
        candidate_dict = candidate.to_dict()
        latencies.append(int(candidate_dict.get("artifacts", {}).get("latency_ms", 0)))

        # Execution-based scoring via Ghost PostgreSQL
        predicted_sql = candidate.final_sql or ""
        exec_score = score_execution(
            ghost_db_id=ghost_db_id,
            schema=db_id,
            predicted_sql=predicted_sql,
            gold_sql=gold_sql,
        )

        total += 1
        if exec_score["predicted_executable"]:
            executable_count += 1
        if exec_score["gold_executable"]:
            gold_executable_count += 1
        if exec_score["result_match"] is not None:
            result_scoreable_count += 1
            if exec_score["result_match"]:
                result_match_count += 1
                correct_count += 1
        is_correct = exec_score.get("result_match", False) or False

        prediction_row = {
            "timestamp": now_iso(),
            "sequence_index": idx,
            "question_id": question_id,
            "question": question,
            "db_id": db_id,
            "correct": is_correct,
            "predicted_executable": exec_score["predicted_executable"],
            "gold_executable": exec_score["gold_executable"],
            "result_match": exec_score["result_match"],
            "predicted_error": exec_score["predicted_error"],
            "gold_error": exec_score["gold_error"],
            "predicted_row_count": exec_score["predicted_row_count"],
            "gold_row_count": exec_score["gold_row_count"],
            "status": candidate.status,
            "error": candidate.error,
            "answer_raw": candidate.answer.raw,
            "answer_normalized": candidate.answer.normalized,
            "answer_kind": candidate.answer.kind,
            "answer_text": candidate_dict.get("artifacts", {}).get("answer_text"),
            "final_sql": candidate.final_sql,
            "gold_sql": gold_sql,
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
                "error": candidate.error,
                "final_sql": candidate.final_sql,
                "answer_raw": candidate.answer.raw,
                "answer_normalized": candidate.answer.normalized,
                "answer_text": candidate_dict.get("artifacts", {}).get("answer_text"),
            },
            "observability": candidate_dict.get("artifacts", {}).get("observability"),
            "score": {
                "correct": is_correct,
                "predicted_executable": exec_score["predicted_executable"],
                "gold_executable": exec_score["gold_executable"],
                "result_match": exec_score["result_match"],
            },
        }
        writer.write_trace(question_id=question_id, trace_payload=trace_payload)

    exec_rate = (executable_count / total) if total else 0.0
    result_match_rate = (result_match_count / result_scoreable_count) if result_scoreable_count else 0.0
    accuracy = result_match_rate
    mean_latency = (sum(latencies) / len(latencies)) if latencies else 0.0
    summary = {
        "run_id": resolved_run_id,
        "timestamp": now_iso(),
        "experiment_name": manifest["experiment_name"],
        "notes": notes,
        "questions_total": total,
        "questions_correct": correct_count,
        "questions_skipped": skipped,
        "executable_count": executable_count,
        "executable_rate": round(exec_rate, 4),
        "gold_executable_count": gold_executable_count,
        "result_scoreable_count": result_scoreable_count,
        "result_match_count": result_match_count,
        "result_match_rate": round(result_match_rate, 4),
        "accuracy": round(accuracy, 4),
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
