from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .traces import read_jsonl


def _classify_failure(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("status") == "error":
        error = str(row.get("error") or "").lower()
        if "no such table" in error:
            return "hallucinated_table", "candidate_fix"
        if "no such column" in error:
            return "hallucinated_column", "candidate_fix"
        return "tool_recovery_failed", "candidate_fix"

    if row.get("final_sql") in (None, ""):
        return "stopped_too_early", "candidate_fix"

    answer = row.get("answer_normalized")
    if answer is None:
        return "empty_result_mishandled", "candidate_fix"

    return "wrong_aggregation_or_filter", "candidate_fix"


def generate_rca_for_run(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    failures_path = run_dir / "failures.jsonl"
    rows = read_jsonl(failures_path)
    out: list[dict[str, Any]] = []
    tags = Counter()
    rca_types = Counter()

    for row in rows:
        question_id = str(row.get("question_id") or "")
        primary_cause, rca_type = _classify_failure(row)
        tags[primary_cause] += 1
        rca_types[rca_type] += 1
        out.append(
            {
                "question_id": question_id,
                "rca_type": rca_type,
                "primary_cause": primary_cause,
                "explanation": f"Auto-classified from status/error/answer signals for {question_id}.",
                "suggested_fix_surface": [
                    "candidate/prompt.py",
                    "candidate/agent_graph.py",
                    "candidate/tools.py",
                ],
                "confidence": "medium",
                "manual_override": False,
            }
        )

    summary = {
        "failed_questions": len(rows),
        "rca_type_counts": dict(sorted(rca_types.items())),
        "primary_cause_counts": dict(sorted(tags.items())),
    }
    return out, summary

