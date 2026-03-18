# analytics-agent-autoresearch

Local-first analytics agent benchmark loop with:

1. LangGraph built-in ReAct runtime
2. Local run artifacts (`runs/<run_id>/...`)
3. Deterministic RCA generation and update CLI
4. Prompt governance checks
5. Git branch helpers for restart-from-version workflows

## Runtime Structure

1. `candidate/agent_graph.py`
   Thin ReAct execution wrapper only.
2. `candidate/response_parser.py`
   Final answer JSON and SQL extraction helpers.
3. `candidate/observability.py`
   Default OpenInference + Phoenix instrumentation bootstrap.
4. `candidate/tracing.py`
   Local trace artifact capture used for RCA files.
5. `harness/benchmark.py`
    Benchmark loop writing manifest/predictions/failures/traces/rca.

This separation keeps the ReAct runtime readable and keeps tracing concerns isolated.

Research notes:

1. `analytics-agent-autoresearch/docs/react-tracing-research.md`

## Quick Start

1. Set env vars in repo root `.env` or `analytics-agent-autoresearch/.env`:
   - `MISTRAL_API_KEY`
   - `SPIDER_ROOT` (optional if local Spider path differs)
   - `PHOENIX_COLLECTOR_ENDPOINT` (optional, default `http://127.0.0.1:6006/v1/traces`)
   - `TRACE_BACKEND` (optional, default `phoenix`; set `none` to disable)
2. Use parent venv Python:
   - `.\.venv\Scripts\python.exe --version`
3. Run smoke benchmark:
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/run.py --split smoke --limit 10`
   - optional fail-fast network check: add `--preflight`
4. Generate/inspect RCA:
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/rca.py generate --run-id <run_id>`
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/rca.py summarize --run-id <run_id>`
5. Prompt guard checks:
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/prompt_guard.py check`
6. Branch status/create:
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/branch.py status`
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/branch.py create --series s1 --lane small --iteration 0`

## Run Metadata (CLI)

You can pass rich metadata through run CLI for artifact logging:

`--experiment-name`, `--notes`, `--candidate-version`, `--prompt-version`, `--tags`, `--metadata-json`

## Current Findings

Current recorded results from `results.tsv`:

| Commit | Run ID | Lane | Split | Accuracy | Status | Notes |
|---|---|---|---|---:|---|---|
| `1844c71` | `run_1_xk2hr6zt` | `small` | `slice_0_100` | 0.2100 | keep | baseline |
| `34d75d4` | `run_2_ank4a2aw` | `small` | `slice_0_100` | 0.6900 | keep | big gain after contract and tool fixes |
| `e6d031a` | `run_3_9xild9wl` | `small` | `slice_100_100` | 0.3700 | keep | different slice exposed generalization gaps |
| `eeef51e` | `run_4_0uz4zvcz` | `small` | `slice_200_100` | 0.5300 | keep | after schema tool and SQL no case recovery improved over run_3 |

What these runs tell us:

1. The project demonstrates a clear eval-driven improvement arc, from a `21.0%` baseline to substantially better performance after prompt and tool-contract fixes.
2. The largest early gain came from enforcing a strict scalar `answer_value` contract and making tool usage more reliable.
3. Cross-slice performance variation shows why agent evals need broader benchmark coverage: improvements on one slice do not guarantee generalization.
4. The core value of the project is not just higher accuracy, but the infrastructure around it: reproducible runs, local artifacts, deterministic RCA, trace capture, and structured iteration lineage.

Immediate next iteration areas:

1. Push beyond prompt-only gains by tightening schema reasoning and SQL recovery behavior.
2. Use RCA summaries to separate output-contract failures from true reasoning failures.
3. Run broader and more randomized benchmark slices so future gains are measured on generalization, not just local improvements.

## Prompt-Iteration Accuracy Timeline

This is the headline improvement story carried into the submission:

| Version | Eval set | Accuracy | What changed |
|---|---|---:|---|
| v0 | 100q | 21.0% | Baseline prompt and weak scalar contract |
| v3 | 100q | 74.4% | Enforced scalar `answer_value` and improved extraction flow |
| v5 | 39q | 89.7% | Added few-shot examples on a small curated slice |
| v6 | 100q randomized | 82.0% | Expanded to a larger randomized set and exposed generalization gaps |
| v7 | 100q randomized | 83.0% | Refined examples with deterministic ordering and safer joins |
| v8 | 100q randomized | 84.0% | Added real-data examples and clearer column extraction reasoning |

## Connectivity Note

If your execution environment blocks outbound sockets, model calls fail with connection errors.
Use:

`.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/run.py --split smoke --limit 1 --preflight`

If preflight fails in a sandbox, run unsandboxed or allow outbound network.

## Tracing Defaults

Tracing is enabled by default via OpenInference + OTLP export to Phoenix.

1. Start Phoenix collector/UI (example):
   - `set PHOENIX_WORKING_DIR=%CD%\\analytics-agent-autoresearch\\.phoenix`
   - `.\.venv\Scripts\phoenix.exe serve`
2. Run benchmark normally; traces export automatically.
3. Disable only when needed:
   - `set TRACE_BACKEND=none`
4. If collector is not reachable, runtime auto-disables tracing for that process (`disabled_reason=collector_unreachable` in artifacts).
5. Per-question run trace files now include `phoenix_spans` JSON captured from OpenTelemetry spans.
