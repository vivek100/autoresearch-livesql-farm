from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from harness.benchmark import run_benchmark  # noqa: E402


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


def _preflight_api(timeout_seconds: float = 8.0) -> tuple[bool, str]:
    """
    Check basic outbound reachability to Mistral API endpoint.
    Note: API root may return 404; that still proves network path works.
    """
    try:
        resp = requests.get("https://api.mistral.ai", timeout=timeout_seconds)
        return True, f"reachable (status={resp.status_code})"
    except Exception as exc:
        return False, f"unreachable ({exc})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run analytics-agent-autoresearch benchmark.")
    parser.add_argument("--split", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--model", default="mistral-small-latest")
    parser.add_argument("--lane", default="small", help="Model lane id (small/medium/large/etc).")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--from-run", default=None, help="Parent run id for lineage.")
    parser.add_argument("--from-git-ref", default=None, help="Parent git ref for lineage.")
    parser.add_argument("--experiment-name", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--candidate-version", default="candidate_v0")
    parser.add_argument("--prompt-version", default="prompt_v0")
    parser.add_argument("--tags", default=None, help="Comma-separated tags.")
    parser.add_argument("--metadata-json", default=None, help="JSON object for extra manifest metadata.")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run connectivity preflight before benchmark execution.",
    )
    parser.add_argument(
        "--preflight-timeout",
        type=float,
        default=8.0,
        help="Seconds for API preflight timeout.",
    )
    args = parser.parse_args()

    if args.preflight:
        ok, note = _preflight_api(timeout_seconds=args.preflight_timeout)
        print(f"preflight={note}")
        if not ok:
            print(
                "Mistral API preflight failed. "
                "If you are running in a restricted sandbox, run unsandboxed or allow outbound network."
            )
            return 3

    extra_metadata = _parse_json_obj(args.metadata_json)
    tags = _parse_csv(args.tags)

    result = run_benchmark(
        split=args.split,
        limit=args.limit,
        offset=args.offset,
        model_name=args.model,
        lane=args.lane,
        run_id=args.run_id,
        parent_run_id=args.from_run,
        parent_git_ref=args.from_git_ref,
        experiment_name=args.experiment_name,
        notes=args.notes,
        candidate_version=args.candidate_version,
        prompt_version=args.prompt_version,
        tags=tags,
        extra_metadata=extra_metadata,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
