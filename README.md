# analytics-agent-autoresearch

Local-first analytics agent benchmark loop with:

1. Custom ReAct runtime with Kimi compatibility layer
2. Tinker API LLM integration
3. LiveSQLBench benchmark dataset
4. Local run artifacts (`runs/<run_id>/...`)
5. Deterministic RCA generation and update CLI
6. Prompt governance checks
7. Git branch helpers for restart-from-version workflows

## Runtime Structure

1. `candidate/agent_graph.py`
   Thin ReAct execution wrapper only.
2. `candidate/llm.py`
   Tinker API LLM integration.
3. `candidate/react.py`
   Custom ReAct agent builder with safe tool wrapping and Kimi compatibility.
4. `candidate/kimi_compat.py`
   Compatibility helpers for Kimi text-emitted tool calls.
5. `candidate/response_parser.py`
   Final answer JSON and SQL extraction helpers.
6. `candidate/observability.py`
   Default OpenInference + Phoenix instrumentation bootstrap.
7. `candidate/tracing.py`
   Local trace artifact capture used for RCA files.
8. `harness/benchmark.py`
    Benchmark loop writing manifest/predictions/failures/traces/rca for LiveSQLBench.

This separation keeps the ReAct runtime readable and keeps tracing concerns isolated.

Research notes:

1. `analytics-agent-autoresearch/docs/react-tracing-research.md`

## Quick Start

1. Set env vars in repo root `.env` or `analytics-agent-autoresearch/.env`:
   - `TINKER_API_KEY` (required)
   - `TINKER_MODEL_PATH` (required, e.g., `moonshotai/Kimi-K2.5`)
   - `TINKER_BASE_URL` (optional, default: `https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1`)
   - `LIVESQL_ROOT` (optional, default: `data/livesqlbench`)
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

**Migrated to LiveSQLBench + Tinker API**

This submission has been migrated from Spider/Mistral to LiveSQLBench/Tinker API with the Kimi-compatible ReAct agent. A new baseline needs to be established on the LiveSQLBench dataset.

**Key infrastructure value:**
- Local-first eval harness with reproducible runs
- Deterministic RCA generation for systematic debugging
- Trace capture and structured iteration lineage
- Custom ReAct agent with safe tool wrapping and Kimi compatibility

**Next steps:**
1. Run initial smoke benchmark to establish baseline on LiveSQLBench
2. Use RCA summaries to identify failure patterns
3. Iterate on prompts and tool contracts for improvement |

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
