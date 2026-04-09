"""
Experiment Farm — Parallel sandbox runner for LiveSQLBench evaluation.

Distributes benchmark questions across Blaxel sandboxes (N questions per sandbox),
runs them in parallel, and aggregates results into standard harness artifacts.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import string
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from blaxel.core import SandboxInstance

from .rca import generate_rca_for_run
from .traces import TraceWriter, build_run_paths
from .versions import BENCHMARK_VERSION, RCA_VERSION, SCORER_VERSION, TRACE_SCHEMA_VERSION

# ─── Configuration ───────────────────────────────────────────────────────────

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]

SANDBOX_IMAGE = os.getenv("FARM_SANDBOX_IMAGE", "blaxel/py-app:latest")
SANDBOX_MEMORY = int(os.getenv("FARM_SANDBOX_MEMORY", "4096"))
SANDBOX_REGION = os.getenv("BL_REGION", "us-pdx-1")
QUESTIONS_PER_SANDBOX = int(os.getenv("FARM_QUESTIONS_PER_SANDBOX", "4"))

# Git repo URL for cloning into sandboxes (the autoresearch submission repo)
REPO_URL = os.getenv("FARM_REPO_URL", "")

# Direct PostgreSQL URI (bypasses Ghost CLI entirely in sandboxes)
GHOST_PG_URI = os.getenv("GHOST_PG_URI", "")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _random_suffix(length: int = 6) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))


def _get_git_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _build_run_id(prefix: str = "farm") -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}-{_random_suffix()}"


# ─── Data types ──────────────────────────────────────────────────────────────

@dataclass
class SandboxBatch:
    """A batch of questions assigned to one sandbox."""
    batch_id: int
    questions: list[dict[str, Any]]
    start_index: int  # global sequence index of first question


@dataclass
class SandboxState:
    """Tracks a sandbox and its assigned batch."""
    batch: SandboxBatch
    sandbox_name: str
    instance: SandboxInstance | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    elapsed_seconds: float = 0.0


# ─── Question distribution ──────────────────────────────────────────────────

def distribute_questions(
    questions: list[dict[str, Any]],
    per_sandbox: int,
) -> list[SandboxBatch]:
    """Split questions into batches of `per_sandbox` size."""
    batches = []
    for i in range(0, len(questions), per_sandbox):
        chunk = questions[i : i + per_sandbox]
        batches.append(SandboxBatch(
            batch_id=len(batches),
            questions=chunk,
            start_index=i,
        ))
    return batches


# ─── Sandbox lifecycle ───────────────────────────────────────────────────────

async def create_sandbox(name: str) -> SandboxInstance | None:
    try:
        sb = await SandboxInstance.create_if_not_exists({
            "name": name,
            "image": SANDBOX_IMAGE,
            "memory": SANDBOX_MEMORY,
            "region": SANDBOX_REGION,
        })
        print(f"  [create] {name} ready")
        return sb
    except Exception as e:
        print(f"  [create] {name} FAILED: {e}")
        return None


async def setup_sandbox(
    sandbox: SandboxInstance,
    repo_url: str,
    env_vars: dict[str, str],
) -> bool:
    """Clone repo, install deps, and set env vars (no Ghost CLI needed — uses direct PG)."""
    name = sandbox.metadata.name
    try:
        # Clone the autoresearch repo
        print(f"  [setup]  {name} cloning repo...")
        result = await sandbox.process.exec({
            "name": "git-clone",
            "command": f"git clone --depth 1 {repo_url} /project",
            "wait_for_completion": True,
            "timeout": 120,
        })
        if hasattr(result, "exit_code") and result.exit_code != 0:
            stderr = getattr(result, "stderr", "")[:200]
            print(f"  [setup]  {name} git clone failed: {stderr}")
            return False

        # Install Python dependencies (slim sandbox requirements — no Phoenix/OTel)
        print(f"  [setup]  {name} pip installing...")
        req_file = "/project/requirements-sandbox.txt"
        # Fall back to full requirements if sandbox file doesn't exist
        check = await sandbox.process.exec({
            "name": "check-req",
            "command": f"test -f {req_file} && echo ok || echo missing",
            "wait_for_completion": True,
            "timeout": 10,
        })
        stdout = getattr(check, "stdout", "") or ""
        if "missing" in stdout:
            req_file = "/project/requirements.txt"
        result = await sandbox.process.exec({
            "name": "pip-install",
            "command": f"pip install -r {req_file}",
            "working_dir": "/project",
            "wait_for_completion": True,
            "timeout": 180,
        })
        if hasattr(result, "exit_code") and result.exit_code != 0:
            stderr = getattr(result, "stderr", "")[:200]
            print(f"  [setup]  {name} pip install failed: {stderr}")
            return False

        # Write env vars to .env file in project root
        env_content = "\n".join(f"{k}={v}" for k, v in env_vars.items())
        await sandbox.fs.write("/project/.env", env_content)

        print(f"  [setup]  {name} ready")
        return True
    except Exception as e:
        print(f"  [setup]  {name} FAILED: {e}")
        return False


async def delete_sandbox(name: str):
    try:
        await SandboxInstance.delete(name)
        print(f"  [delete] {name}")
    except Exception as e:
        print(f"  [delete] {name} failed: {e}")


# ─── Run batch in sandbox ───────────────────────────────────────────────────

async def run_batch_in_sandbox(
    state: SandboxState,
    model_name: str,
    ghost_db_id: str,
    timeout: int = 600,
) -> SandboxState:
    """Write batch file to sandbox, run worker, read back results."""
    sb = state.instance
    if sb is None:
        state.error = "sandbox not created"
        return state

    name = state.sandbox_name
    batch_path = f"/tmp/batch_{state.batch.batch_id}.json"
    output_path = f"/tmp/results_{state.batch.batch_id}.jsonl"
    start = time.time()

    try:
        # Write batch file
        batch_payload = json.dumps({
            "questions": state.batch.questions,
            "start_index": state.batch.start_index,
        })
        await sb.fs.write(batch_path, batch_payload)

        # Run the worker
        n = len(state.batch.questions)
        print(f"  [run]    {name} batch={state.batch.batch_id} ({n} questions, idx {state.batch.start_index}-{state.batch.start_index + n - 1})")

        # Calculate timeout: base + per-question allowance
        worker_timeout = max(timeout, n * 180)  # 3 min per question minimum

        result = await sb.process.exec({
            "name": f"worker-batch-{state.batch.batch_id}",
            "command": (
                f"cd /project && python -m harness.farm_worker"
                f" --batch {batch_path}"
                f" --output {output_path}"
                f" --ghost-db-id {ghost_db_id}"
                f" --model {model_name}"
            ),
            "working_dir": "/project",
            "wait_for_completion": True,
            "timeout": worker_timeout,
        })

        state.elapsed_seconds = round(time.time() - start, 2)

        exit_code = getattr(result, "exit_code", None)
        if exit_code is not None and exit_code != 0:
            stderr = ""
            try:
                stderr = await sb.fs.read(f"/tmp/stderr_batch_{state.batch.batch_id}.txt")
            except Exception:
                stderr = getattr(result, "stderr", "") or getattr(result, "logs", "") or ""
            state.error = f"Worker exit code {exit_code}: {stderr[:500]}"
        else:
            # Read results JSONL
            try:
                output = await sb.fs.read(output_path)
                for line in output.strip().split("\n"):
                    line = line.strip()
                    if line:
                        try:
                            state.results.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                state.error = f"Failed to read results: {e}"

    except Exception as e:
        state.elapsed_seconds = round(time.time() - start, 2)
        state.error = str(e)

    ok = len(state.results)
    total = len(state.batch.questions)
    status = f"{ok}/{total} ok" if not state.error else f"ERROR: {state.error[:80]}"
    print(f"  [done]   {name} batch={state.batch.batch_id}: {status} ({state.elapsed_seconds:.0f}s)")
    return state


# ─── Result aggregation ─────────────────────────────────────────────────────

def aggregate_results(
    states: list[SandboxState],
    run_id: str,
    manifest: dict[str, Any],
    writer: TraceWriter,
    split: str,
    lane: str,
    candidate_version: str,
    prompt_version: str,
    tags: list[str],
    notes: str | None,
    model_name: str,
) -> dict[str, Any]:
    """Merge all sandbox results into standard harness artifacts."""
    # Collect all question results, sorted by sequence_index
    all_results: list[dict[str, Any]] = []
    for s in states:
        all_results.extend(s.results)
    all_results.sort(key=lambda r: r.get("sequence_index", 0))

    total = 0
    correct_count = 0
    executable_count = 0
    gold_executable_count = 0
    result_match_count = 0
    result_scoreable_count = 0
    latencies: list[int] = []

    for r in all_results:
        total += 1
        latencies.append(int(r.get("latency_ms", 0)))

        if r.get("predicted_executable"):
            executable_count += 1
        if r.get("gold_executable"):
            gold_executable_count += 1
        if r.get("result_match") is not None:
            result_scoreable_count += 1
            if r.get("result_match"):
                result_match_count += 1
                correct_count += 1

        is_correct = r.get("result_match", False) or False

        # Build prediction row (matches benchmark.py format exactly)
        prediction_row = {
            "timestamp": _now_iso(),
            "sequence_index": r.get("sequence_index", 0),
            "question_id": r["question_id"],
            "question": r["question"],
            "db_id": r["db_id"],
            "correct": is_correct,
            "predicted_executable": r.get("predicted_executable", False),
            "gold_executable": r.get("gold_executable", False),
            "result_match": r.get("result_match"),
            "predicted_error": r.get("predicted_error", ""),
            "gold_error": r.get("gold_error", ""),
            "predicted_row_count": r.get("predicted_row_count", 0),
            "gold_row_count": r.get("gold_row_count", 0),
            "status": r.get("status", "error"),
            "error": r.get("error"),
            "answer_raw": r.get("answer_raw"),
            "answer_normalized": r.get("answer_normalized"),
            "answer_kind": r.get("answer_kind"),
            "answer_text": r.get("answer_text"),
            "final_sql": r.get("final_sql"),
            "gold_sql": r.get("gold_sql", ""),
            "steps_used": r.get("steps_used", 0),
            "latency_ms": r.get("latency_ms"),
            "model_name": model_name,
            "model_lane": lane,
            "split": split,
            "candidate_version": candidate_version,
            "prompt_version": prompt_version,
            "tags": tags,
            "run_id": run_id,
            "experiment_name": manifest["experiment_name"],
            "observability": r.get("observability"),
            "phoenix_span_count": len(r.get("phoenix_spans", [])),
        }
        writer.append_prediction(prediction_row)
        if not is_correct:
            writer.append_failure(prediction_row)

        # Write per-question trace
        trace_payload = {
            "trace_id": f"trace_{r['question_id']}",
            "question_id": r["question_id"],
            "question": r["question"],
            "db_id": r["db_id"],
            "candidate": {
                "git_sha": manifest["git_sha"],
                "model_name": model_name,
                "runtime": r.get("runtime", "langgraph.prebuilt.create_react_agent"),
                "candidate_version": candidate_version,
                "prompt_version": prompt_version,
            },
            "events": r.get("trace_events", []),
            "phoenix_spans": r.get("phoenix_spans", []),
            "final": {
                "status": r.get("status"),
                "error": r.get("error"),
                "final_sql": r.get("final_sql"),
                "answer_raw": r.get("answer_raw"),
                "answer_normalized": r.get("answer_normalized"),
                "answer_text": r.get("answer_text"),
            },
            "observability": r.get("observability"),
            "score": {
                "correct": is_correct,
                "predicted_executable": r.get("predicted_executable", False),
                "gold_executable": r.get("gold_executable", False),
                "result_match": r.get("result_match"),
            },
        }
        writer.write_trace(question_id=r["question_id"], trace_payload=trace_payload)

    # Compute summary (matches benchmark.py format exactly)
    exec_rate = (executable_count / total) if total else 0.0
    result_match_rate = (result_match_count / result_scoreable_count) if result_scoreable_count else 0.0
    accuracy = result_match_rate
    mean_latency = (sum(latencies) / len(latencies)) if latencies else 0.0

    # Farm-specific metadata
    sandbox_count = len(states)
    sandbox_errors = sum(1 for s in states if s.error)
    total_sandbox_time = sum(s.elapsed_seconds for s in states)
    wall_clock = max((s.elapsed_seconds for s in states), default=0.0)

    summary = {
        "run_id": run_id,
        "timestamp": _now_iso(),
        "experiment_name": manifest["experiment_name"],
        "notes": notes,
        "questions_total": total,
        "questions_correct": correct_count,
        "questions_skipped": 0,
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
        "tags": tags,
        "benchmark_version": BENCHMARK_VERSION,
        "scorer_version": SCORER_VERSION,
        "rca_version": RCA_VERSION,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "extra_metadata": manifest.get("extra_metadata", {}),
        # Farm-specific fields
        "farm": {
            "sandbox_count": sandbox_count,
            "sandbox_errors": sandbox_errors,
            "questions_per_sandbox": manifest.get("extra_metadata", {}).get("questions_per_sandbox", QUESTIONS_PER_SANDBOX),
            "concurrency": manifest.get("extra_metadata", {}).get("concurrency", 0),
            "total_sandbox_seconds": round(total_sandbox_time, 2),
            "wall_clock_seconds": round(wall_clock, 2),
        },
    }
    writer.write_summary(summary)

    # Generate RCA
    rca_rows, rca_summary = generate_rca_for_run(writer.paths.root)
    writer.write_rca_rows(rca_rows)
    writer.write_aggregate_rca(rca_summary)

    return {
        "run_id": run_id,
        "run_dir": writer.paths.root.as_posix(),
        "summary": summary,
        "rca_summary": rca_summary,
    }


# ─── Main orchestration ─────────────────────────────────────────────────────

async def run_farm(
    split: str = "smoke",
    limit: int = 25,
    offset: int = 0,
    model_name: str = "openai",
    lane: str = "small",
    per_sandbox: int = QUESTIONS_PER_SANDBOX,
    concurrency: int = 15,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    parent_git_ref: str | None = None,
    experiment_name: str | None = None,
    notes: str | None = None,
    candidate_version: str = "candidate_v0",
    prompt_version: str = "prompt_v0",
    tags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
    repo_url: str | None = None,
    timeout_per_question: int = 180,
) -> dict[str, Any]:
    """Run benchmark across parallel sandboxes."""
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT.parent / ".env", override=False)

    # Resolve config
    ghost_db_id = os.environ.get("GHOST_DB_ID")
    if not ghost_db_id:
        raise RuntimeError("GHOST_DB_ID is not set.")
    openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    resolved_repo_url = repo_url or REPO_URL
    if not resolved_repo_url:
        raise RuntimeError("FARM_REPO_URL env var or --repo-url required (git URL of this project).")
    resolved_pg_uri = os.environ.get("GHOST_PG_URI", GHOST_PG_URI)
    resolved_tags = tags or []

    # Load dataset
    livesql_root = os.environ.get("LIVESQL_ROOT")
    if livesql_root:
        dataset_path = Path(livesql_root) / "dataset.json"
    else:
        dataset_path = PROJECT_ROOT / "data" / "livesqlbench" / "dataset.json"
    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset.json not found at {dataset_path}")

    with dataset_path.open("r", encoding="utf-8") as f:
        dev_rows = json.load(f)

    # Slice dataset
    if split == "smoke":
        selected = dev_rows[offset : offset + min(limit, 30)]
    else:
        selected = dev_rows[offset : offset + limit]
    if not selected:
        raise RuntimeError("No benchmark rows selected.")

    # Distribute into batches
    batches = distribute_questions(selected, per_sandbox)
    num_sandboxes = len(batches)

    # Build run
    resolved_run_id = run_id or _build_run_id(prefix=f"farm-{split}")
    paths = build_run_paths(base_dir=PROJECT_ROOT / "runs", run_id=resolved_run_id)
    writer = TraceWriter(paths=paths)

    meta = extra_metadata or {}
    meta.update({
        "farm_mode": True,
        "questions_per_sandbox": per_sandbox,
        "concurrency": concurrency,
        "sandbox_count": num_sandboxes,
        "total_questions": len(selected),
    })

    manifest = {
        "run_id": resolved_run_id,
        "timestamp": _now_iso(),
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
        "tags": resolved_tags,
        "benchmark_version": BENCHMARK_VERSION,
        "scorer_version": SCORER_VERSION,
        "rca_version": RCA_VERSION,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "git_sha": _get_git_sha(),
        "parent_run_id": parent_run_id,
        "parent_git_ref": parent_git_ref,
        "ghost_db_id": ghost_db_id,
        "extra_metadata": meta,
    }
    writer.write_manifest(manifest)

    # Env vars to pass to each sandbox
    env_vars = {
        "GHOST_DB_ID": ghost_db_id,
        "GHOST_PG_URI": resolved_pg_uri,
        "OPENAI_API_KEY": openai_api_key,
        "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"),
        "TRACE_BACKEND": "none",
    }

    print("=" * 62)
    print("  EXPERIMENT FARM — Parallel Sandbox Benchmark Runner")
    print("=" * 62)
    print(f"  Run ID:         {resolved_run_id}")
    print(f"  Split:          {split} (limit={limit}, offset={offset})")
    print(f"  Questions:      {len(selected)}")
    print(f"  Per sandbox:    {per_sandbox}")
    print(f"  Sandboxes:      {num_sandboxes}")
    print(f"  Concurrency:    {concurrency}")
    print(f"  Model:          {model_name}")
    print(f"  Image:          {SANDBOX_IMAGE}")
    print()

    sem = asyncio.Semaphore(concurrency)
    states: list[SandboxState] = []

    for batch in batches:
        # Blaxel requires lowercase alphanumeric + hyphens, no double hyphens
        slug = _random_suffix(8)
        sname = f"farm-{slug}-b{batch.batch_id}"
        states.append(SandboxState(batch=batch, sandbox_name=sname))

    async def create_and_setup(state: SandboxState) -> bool:
        async with sem:
            sb = await create_sandbox(state.sandbox_name)
            if sb is None:
                state.error = "sandbox creation failed"
                return False
            state.instance = sb
            ok = await setup_sandbox(sb, resolved_repo_url, env_vars)
            if not ok:
                await delete_sandbox(state.sandbox_name)
                state.instance = None
                state.error = "sandbox setup failed"
                return False
            return True

    async def run_one(state: SandboxState) -> SandboxState:
        async with sem:
            return await run_batch_in_sandbox(
                state=state,
                model_name=model_name,
                ghost_db_id=ghost_db_id,
                timeout=per_sandbox * timeout_per_question,
            )

    try:
        # Phase 0: Create & setup all sandboxes
        print(f"[phase 0] Creating & setting up {num_sandboxes} sandboxes...")
        t0 = time.time()
        setup_tasks = [create_and_setup(s) for s in states]
        setup_ok = await asyncio.gather(*setup_tasks, return_exceptions=True)

        ready = [s for s, ok in zip(states, setup_ok) if ok is True]
        failed = num_sandboxes - len(ready)
        print(f"  {len(ready)}/{num_sandboxes} sandboxes ready ({time.time() - t0:.0f}s)")
        if failed:
            print(f"  WARNING: {failed} sandboxes failed setup")
        if not ready:
            raise RuntimeError("No sandboxes ready. Aborting.")

        # Phase 1: Run all batches in parallel
        print(f"\n[phase 1] Running {len(ready)} batches across sandboxes...")
        t1 = time.time()
        run_tasks = [run_one(s) for s in ready]
        await asyncio.gather(*run_tasks, return_exceptions=True)
        print(f"  Phase 1 done ({time.time() - t1:.0f}s)")

        # Phase 2: Aggregate results
        print(f"\n[phase 2] Aggregating results...")
        result = aggregate_results(
            states=states,
            run_id=resolved_run_id,
            manifest=manifest,
            writer=writer,
            split=split,
            lane=lane,
            candidate_version=candidate_version,
            prompt_version=prompt_version,
            tags=resolved_tags,
            notes=notes,
            model_name=model_name,
        )

        # Print summary
        summary = result["summary"]
        rca = result["rca_summary"]
        total_q = summary["questions_total"]
        print(f"\n{'=' * 62}")
        print(f"  RESULTS — {resolved_run_id}")
        print(f"{'=' * 62}")
        print(f"  Questions:        {total_q}")
        print(f"  Accuracy:         {summary['accuracy']:.1%} ({summary['result_match_count']}/{summary.get('result_scoreable_count', 0)})")
        print(f"  Executable rate:  {summary['executable_rate']:.1%} ({summary['executable_count']}/{total_q})")
        print(f"  Mean latency:     {summary['latency_mean_ms']:.0f}ms")
        farm_meta = summary.get("farm", {})
        print(f"  Wall clock:       {farm_meta.get('wall_clock_seconds', 0):.0f}s")
        print(f"  Sandbox errors:   {farm_meta.get('sandbox_errors', 0)}")
        if rca.get("primary_cause_counts"):
            print(f"  RCA breakdown:    {json.dumps(rca['primary_cause_counts'])}")
        print(f"  Run dir:          {result['run_dir']}")
        print(f"{'=' * 62}\n")

        return result

    finally:
        # Always clean up all sandboxes
        live = [s.sandbox_name for s in states if s.instance is not None]
        if live:
            print(f"\n[cleanup] Deleting {len(live)} sandboxes...")
            await asyncio.gather(*[delete_sandbox(n) for n in live])
        print("[done]")
