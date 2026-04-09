#!/usr/bin/env python3
"""
Farm worker — runs a batch of benchmark questions inside a sandbox.

This script is executed inside each Blaxel sandbox by the farm orchestrator.
It processes N questions sequentially, runs the agent + scorer for each,
and writes results as JSONL to an output file.

Usage (inside sandbox):
    python -m harness.farm_worker --batch /tmp/batch.json --output /tmp/results.jsonl
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

# Ensure project root is on path
HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from candidate.agent_graph import run_candidate_question  # noqa: E402
from harness.scorer import score_execution  # noqa: E402


def process_question(
    question_row: dict,
    ghost_db_id: str,
    model_name: str,
    idx: int,
) -> dict:
    """Run agent + scorer on a single question and return a flat result dict."""
    instance_id = str(question_row.get("instance_id", f"q_{idx}"))
    question_id = f"livesql_{instance_id}"
    question = str(question_row.get("question"))
    db_id = str(question_row.get("db_id"))
    gold_sql_raw = question_row.get("sol_sql")
    if isinstance(gold_sql_raw, list) and gold_sql_raw:
        gold_sql = str(gold_sql_raw[0])
    else:
        gold_sql = str(gold_sql_raw) if gold_sql_raw else ""

    wall_start = time.perf_counter()

    try:
        candidate = run_candidate_question(
            question=question,
            db_id=db_id,
            ghost_db_id=ghost_db_id,
            model_name=model_name,
        )
        candidate_dict = candidate.to_dict()
        latency_ms = int(candidate_dict.get("artifacts", {}).get("latency_ms", 0))

        predicted_sql = candidate.final_sql or ""
        exec_score = score_execution(
            ghost_db_id=ghost_db_id,
            schema=db_id,
            predicted_sql=predicted_sql,
            gold_sql=gold_sql,
        )

        is_correct = exec_score.get("result_match", False) or False

        return {
            "ok": True,
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
            "latency_ms": latency_ms,
            "model_name": model_name,
            "trace_events": candidate_dict.get("artifacts", {}).get("trace_events", []),
            "phoenix_spans": candidate_dict.get("artifacts", {}).get("phoenix_spans", []),
            "observability": candidate_dict.get("artifacts", {}).get("observability"),
            "runtime": candidate.runtime,
        }

    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - wall_start) * 1000)
        return {
            "ok": False,
            "question_id": question_id,
            "question": question,
            "db_id": db_id,
            "correct": False,
            "predicted_executable": False,
            "gold_executable": False,
            "result_match": None,
            "predicted_error": f"{type(exc).__name__}: {exc}",
            "gold_error": "",
            "predicted_row_count": 0,
            "gold_row_count": 0,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            "answer_raw": None,
            "answer_normalized": None,
            "answer_kind": "null",
            "answer_text": None,
            "final_sql": None,
            "gold_sql": gold_sql,
            "steps_used": 0,
            "latency_ms": elapsed_ms,
            "model_name": model_name,
            "trace_events": [],
            "phoenix_spans": [],
            "observability": None,
            "runtime": "langgraph.prebuilt.create_react_agent",
        }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Farm worker: process a batch of questions")
    parser.add_argument("--batch", required=True, help="Path to batch JSON file")
    parser.add_argument("--output", required=True, help="Path to write JSONL results")
    parser.add_argument("--ghost-db-id", default=None, help="Ghost DB ID (or use GHOST_DB_ID env)")
    parser.add_argument("--model", default="openai", help="Model name")
    args = parser.parse_args()

    import os
    ghost_db_id = args.ghost_db_id or os.environ.get("GHOST_DB_ID")
    if not ghost_db_id:
        print("ERROR: --ghost-db-id or GHOST_DB_ID env required", file=sys.stderr)
        return 1

    batch_path = Path(args.batch)
    if not batch_path.exists():
        print(f"ERROR: batch file not found: {batch_path}", file=sys.stderr)
        return 1

    with batch_path.open("r", encoding="utf-8") as f:
        batch = json.load(f)

    questions = batch.get("questions", [])
    if not questions:
        print("ERROR: no questions in batch", file=sys.stderr)
        return 1

    print(f"[worker] Processing {len(questions)} questions...", file=sys.stderr, flush=True)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as out:
        for i, q in enumerate(questions):
            seq_idx = batch.get("start_index", 0) + i
            print(f"[worker] Question {i+1}/{len(questions)}: {q.get('instance_id', '?')}", file=sys.stderr, flush=True)

            result = process_question(
                question_row=q,
                ghost_db_id=ghost_db_id,
                model_name=args.model,
                idx=seq_idx,
            )
            result["sequence_index"] = seq_idx
            result["batch_index"] = i
            out.write(json.dumps(result, default=str) + "\n")
            out.flush()

    print(f"[worker] Done. Wrote {len(questions)} results to {output_path}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
