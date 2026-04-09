"""Download LiveSQLBench dataset and SQLite files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from datasets import load_dataset
from huggingface_hub import hf_hub_download


DATASET_ID = "birdsql/livesqlbench-base-lite"
SQLITE_DATASET_ID = "birdsql/livesqlbench-base-lite-sqlite"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="alien", help="Benchmark database to target.")
    parser.add_argument("--limit", type=int, default=10, help="Number of questions to keep.")
    parser.add_argument(
        "--dataset-output",
        default="data/livesqlbench/dataset.json",
        help="Path to write the filtered dataset JSON.",
    )
    parser.add_argument(
        "--sqlite-output-dir",
        default="data/livesqlbench",
        help="Directory to copy the SQLite template into.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    dataset = load_dataset(DATASET_ID, split="dev")
    filtered = dataset.filter(lambda row: row["selected_database"] == args.database)
    subset = filtered.select(range(min(args.limit, len(filtered))))

    rows = []
    for row in subset:
        rows.append(
            {
                "instance_id": row["instance_id"],
                "db_id": row["selected_database"],
                "question": row["query"],
                "category": row["category"],
                "difficulty": row["difficulty_tier"],
                "high_level": row["high_level"],
                "conditions": row["conditions"],
                "preprocess_sql": row["preprocess_sql"],
                "clean_up_sqls": row["clean_up_sqls"],
                "external_knowledge": row["external_knowledge"],
                "test_cases": row["test_cases"],
                "sol_sql": row["sol_sql"],
            }
        )

    dataset_output = Path(args.dataset_output)
    dataset_output.parent.mkdir(parents=True, exist_ok=True)
    dataset_output.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    sqlite_cache_path = Path(
        hf_hub_download(
            repo_id=SQLITE_DATASET_ID,
            repo_type="dataset",
            filename=f"{args.database}/{args.database}_template.sqlite",
        )
    )
    sqlite_output_dir = Path(args.sqlite_output_dir)
    sqlite_output_dir.mkdir(parents=True, exist_ok=True)
    sqlite_output_path = sqlite_output_dir / f"{args.database}.sqlite"
    sqlite_output_path.write_bytes(sqlite_cache_path.read_bytes())

    print(f"Saved {len(rows)} questions to {dataset_output}")
    print(f"Copied SQLite template to {sqlite_output_path}")


if __name__ == "__main__":
    main()
