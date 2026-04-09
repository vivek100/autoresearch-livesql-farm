"""
CLI for the Experiment Farm — parallel sandbox benchmark runner.

Usage:
    # Quick smoke test (12 questions, 3 sandboxes of 4 each)
    python -m cli.farm --split smoke --limit 12

    # Full 150-question eval across 38 sandboxes
    python -m cli.farm --split full --limit 150 --concurrency 15

    # Custom batch size
    python -m cli.farm --split full --limit 150 --per-sandbox 6 --concurrency 20

    # With experiment metadata
    python -m cli.farm --split full --limit 150 --experiment-name "prompt-v3-eval" --notes "Testing new schema inspection rules"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_json_obj(raw: str | None) -> dict:
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("--metadata-json must be a JSON object")
    return parsed


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Experiment Farm — run LiveSQLBench evaluation across parallel sandboxes."
    )

    # Dataset selection
    parser.add_argument("--split", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)

    # Farm configuration
    parser.add_argument(
        "--per-sandbox", type=int, default=4,
        help="Questions per sandbox (default: 4)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=15,
        help="Max parallel sandboxes (default: 15)",
    )
    parser.add_argument(
        "--timeout-per-question", type=int, default=180,
        help="Timeout per question in seconds (default: 180)",
    )

    # Repo & infrastructure
    parser.add_argument(
        "--repo-url", default=None,
        help="Git URL of this project (or set FARM_REPO_URL env var)",
    )
    # Model & versioning
    parser.add_argument("--model", default="openai")
    parser.add_argument("--lane", default="small")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--from-run", default=None, help="Parent run id for lineage.")
    parser.add_argument("--from-git-ref", default=None, help="Parent git ref for lineage.")

    # Experiment metadata
    parser.add_argument("--experiment-name", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--candidate-version", default="candidate_v0")
    parser.add_argument("--prompt-version", default="prompt_v0")
    parser.add_argument("--tags", default=None, help="Comma-separated tags.")
    parser.add_argument("--metadata-json", default=None, help="JSON object for extra manifest metadata.")

    args = parser.parse_args()

    from harness.farm import run_farm  # noqa: E402

    extra_metadata = _parse_json_obj(args.metadata_json)
    tags = _parse_csv(args.tags)

    result = asyncio.run(run_farm(
        split=args.split,
        limit=args.limit,
        offset=args.offset,
        model_name=args.model,
        lane=args.lane,
        per_sandbox=args.per_sandbox,
        concurrency=args.concurrency,
        run_id=args.run_id,
        parent_run_id=args.from_run,
        parent_git_ref=args.from_git_ref,
        experiment_name=args.experiment_name,
        notes=args.notes,
        candidate_version=args.candidate_version,
        prompt_version=args.prompt_version,
        tags=tags,
        extra_metadata=extra_metadata,
        repo_url=args.repo_url,
        timeout_per_question=args.timeout_per_question,
    ))

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
