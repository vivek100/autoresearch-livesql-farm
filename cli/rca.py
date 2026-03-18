from __future__ import annotations

"""
CLI helpers for deterministic + human-in-the-loop RCA updates.

This is intentionally file-based so agents can write/update RCA without
depending on remote services.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from harness.rca import generate_rca_for_run  # noqa: E402
from harness.traces import read_jsonl  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _run_dir(run_id: str) -> Path:
    return PROJECT_ROOT / "runs" / run_id


def cmd_generate(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args.run_id)
    if not run_dir.exists():
        print(f"Run folder not found: {run_dir.as_posix()}")
        return 1
    rows, summary = generate_rca_for_run(run_dir=run_dir)
    _write_jsonl(run_dir / "rca.jsonl", rows)
    _write_json(run_dir / "aggregate_rca.json", summary)
    print(json.dumps({"run_id": args.run_id, "rows": len(rows), "summary": summary}, indent=2))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    # Update one RCA row and append an immutable change-log row.
    run_dir = _run_dir(args.run_id)
    rca_path = run_dir / "rca.jsonl"
    if not rca_path.exists():
        print(f"rca.jsonl not found for run {args.run_id}. Generate RCA first.")
        return 1

    rows = read_jsonl(rca_path)
    updated = False
    for row in rows:
        if str(row.get("question_id")) != args.question_id:
            continue
        row["primary_cause"] = args.tag or row.get("primary_cause")
        row["rca_type"] = args.rca_type or row.get("rca_type")
        row["confidence"] = args.confidence or row.get("confidence")
        row["explanation"] = args.explanation or row.get("explanation")
        if args.fix_surface:
            row["suggested_fix_surface"] = [x.strip() for x in args.fix_surface.split(",") if x.strip()]
        row["manual_override"] = True
        row["manual_notes"] = args.manual_notes
        row["updated_at"] = _utc_now()
        updated = True
        break

    if not updated:
        print(f"question_id {args.question_id} not found in rca.jsonl")
        return 1

    _write_jsonl(rca_path, rows)
    _append_jsonl(
        run_dir / "rca_updates.jsonl",
        {
            "updated_at": _utc_now(),
            "question_id": args.question_id,
            "tag": args.tag,
            "rca_type": args.rca_type,
            "confidence": args.confidence,
            "explanation": args.explanation,
            "fix_surface": args.fix_surface,
            "manual_notes": args.manual_notes,
            "source": "cli_update",
        },
    )
    print(f"Updated RCA row for {args.question_id} in run {args.run_id}")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args.run_id)
    agg_path = run_dir / "aggregate_rca.json"
    if agg_path.exists():
        print(agg_path.read_text(encoding="utf-8"))
        return 0

    rows = read_jsonl(run_dir / "rca.jsonl")
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("primary_cause") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    payload = {"failed_questions": len(rows), "primary_cause_counts": counts}
    print(json.dumps(payload, indent=2))
    return 0


def cmd_link_fix(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args.run_id)
    if not run_dir.exists():
        print(f"Run folder not found: {run_dir.as_posix()}")
        return 1
    _append_jsonl(
        run_dir / "fix_links.jsonl",
        {
            "linked_at": _utc_now(),
            "run_id": args.run_id,
            "question_id": args.question_id,
            "fix_id": args.fix_id,
            "notes": args.notes,
        },
    )
    print(f"Linked fix {args.fix_id} to question {args.question_id} in run {args.run_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RCA utilities for analytics-agent-autoresearch.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="Generate deterministic RCA from failures.")
    g.add_argument("--run-id", required=True)
    g.set_defaults(func=cmd_generate)

    u = sub.add_parser("update", help="Update one RCA row for a question.")
    u.add_argument("--run-id", required=True)
    u.add_argument("--question-id", required=True)
    u.add_argument("--tag", required=True)
    u.add_argument("--rca-type", default=None, help="candidate_fix | harness_issue | uncertain")
    u.add_argument("--confidence", default="medium")
    u.add_argument("--explanation", default=None)
    u.add_argument("--fix-surface", default=None, help="Comma-separated file paths.")
    u.add_argument("--manual-notes", default=None)
    u.set_defaults(func=cmd_update)

    s = sub.add_parser("summarize", help="Print RCA summary.")
    s.add_argument("--run-id", required=True)
    s.set_defaults(func=cmd_summarize)

    l = sub.add_parser("link-fix", help="Link a fix id to a question RCA record.")
    l.add_argument("--run-id", required=True)
    l.add_argument("--question-id", required=True)
    l.add_argument("--fix-id", required=True)
    l.add_argument("--notes", default=None)
    l.set_defaults(func=cmd_link_fix)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
