# Handoff Document — LiveSQLBench + Tinker API Migration

**Date:** April 9, 2026  
**Session:** Migration from Spider/Mistral to LiveSQLBench/Tinker API with Kimi-K2.5

---

## Objective

Migrate the `analytics-agent-autoresearch` submission folder to use the agent and model architecture from `kimiGoBrr`, specifically:
- Adopt Tinker API model (Kimi-K2.5) instead of Mistral
- Use custom ReAct agent harness with Kimi text-emitted tool call compatibility
- Switch benchmark from Spider to LiveSQLBench
- Update all components: prompt, benchmarking harness, scoring, environment configs
- Use SQLite-only execution-based scoring (not PostgreSQL)

---

## What Was Done

### 1. Agent Migration (kimiGoBrr → submission)

**Copied and verified Kimi-specific files:**
- `candidate/llm.py` — Tinker API LLM configuration with `langchain-openai.ChatOpenAI`
- `candidate/react.py` — Custom ReAct agent with safe tool wrapping + Kimi compat chain
- `candidate/kimi_compat.py` — Parser for Kimi text-emitted tool calls (`<|tool_call_begin|>`, etc.)

**Bug fix applied:**
- Fixed `kimi_compat.py` line 52: changed `.replace("\n", "")` to `.replace("", "")` — Kimi emits think tags, not newlines

**Verification:**
- All imports verified working
- Kimi compat chain (`llm → bind_tools → kimi_compat_model → create_react_agent`) confirmed

### 2. Tools Already SQLite-Native

The submission's `candidate/tools.py` already uses SQLite:
- `describe_schema` — uses `PRAGMA table_info`, `PRAGMA foreign_key_list`
- `execute_sql` — uses `sqlite3.connect` directly
- No Ghost/PostgreSQL dependency — good for LiveSQLBench

### 3. LiveSQLBench Dataset Setup

**Downloaded and placed at `data/livesqlbench/`:**
- `dataset.json` — LiveSQLBench questions with `sol_sql` gold SQL (array format)
- `alien.sqlite` — SQLite database for the alien observatory schema
- Additional SQLite files for other databases

**Environment variables in `.env`:**
```
TINKER_API_KEY=tml-JoVJoi0rjlxuYuBqMF9JmW9EF17WcOaMek4HVXIzIv6R7OAH808jVyrenKHFhqcpDAAAA
TINKER_MODEL_PATH=moonshotai/Kimi-K2.5
TINKER_BASE_URL=https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1
LIVESQL_ROOT=data/livesqlbench
TRACE_BACKEND=none
```

### 4. Benchmark Harness Updates

**Modified `harness/benchmark.py`:**
- Updated to handle `sol_sql` as array (extract first element)
- Integrated `score_execution` from new scorer
- Updated benchmark loop to collect detailed metrics:
  - `executable_count`, `executable_rate`
  - `gold_executable_count`
  - `result_scoreable_count`, `result_match_count`, `result_match_rate`
  - `accuracy` = `result_match_rate`
- Updated model_name handling (still shows "mistral-small-latest" in output — cosmetic issue)

### 5. SQLite Execution-Based Scoring

**Rewrote `harness/scorer.py`:**
- New `score_execution()` function:
  - Executes both predicted and gold SQL against SQLite
  - Strips PostgreSQL block comments from gold SQL
  - Normalizes rows (stringified, lowercased, sorted)
  - Returns detailed dict with executability and result match status
- Handles PostgreSQL syntax incompatibility:
  - Gold SQL often uses `PERCENTILE_CONT`, `STDDEV`, `FILTER (WHERE ...)` — these fail on SQLite
  - These questions are marked `gold_executable: false` and excluded from `result_match_rate`

### 6. Environment Setup

**Created virtual environment:**
```bash
py -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
```

**Updated `requirements.txt`:**
- Changed from `langchain-mistralai` to `langchain-openai>=0.2.0`
- Kept all other dependencies (langgraph, langchain-core, pydantic, etc.)

### 7. Documentation Updates

**Rewrote `program.md`:**
- Updated mission to LiveSQLBench
- Documented Tinker API + Kimi-K2.5 runtime
- Explained execution-based scoring model (two metrics: `executable_rate`, `result_match_rate`)
- Updated environment variables (TINKER_* instead of MISTRAL_*)
- Updated benchmark data paths (LiveSQLBench instead of Spider)
- Updated benchmark commands (use `.venv\Scripts\python.exe`)
- Added "Key Improvement Levers" section prioritizing prompt.py
- Updated keep/discard decision policy for new metrics

**Updated `results.tsv`:**
- Changed format to: `run_id | split | total | executable_rate | result_match_rate | status | description`
- Recorded baseline run: `smoke-20260409-112954-bfciiu` with 3/3 executable, 0% result match

**Updated `README.md`:**
- Updated "Current Findings" section to reflect LiveSQLBench/Tinker API migration
- Noted new baseline needs to be established

### 8. CLI Updates

**Modified `cli/run.py`:**
- Changed default model from `mistral-small-latest` to `tinker-api`
- Updated preflight endpoint from Mistral API to Tinker API
- Updated preflight error message

---

## Current State

### Working
- ✅ Kimi compat chain verified (imports work, tool call parsing works)
- ✅ Agent runs end-to-end (3/3 executable SQL in first smoke test)
- ✅ SQLite execution scoring works (both predicted and gold SQL executed)
- ✅ RCA auto-classification works (identifies failure patterns)
- ✅ Trace capture works (full tool call chain captured)
- ✅ Virtual environment with all dependencies installed

### Issues Identified

**High Latency:**
- First smoke test: ~63s mean latency per question
- Full test (3 questions): ~592s mean latency per question
- One question took 27 minutes (1656s)
- Likely cause: Tinker API slowness or agent getting stuck in loops

**Agent Crashes on Some Questions:**
- In full test (3 questions):
  - Question 1: Success (27 min latency, valid SQL)
  - Question 2: "Connection error." status, `steps_used: 0`, `final_sql: null`
  - Question 3: "Connection error." status, `steps_used: 0`, `final_sql: null`
- Trace shows empty events array — agent never initialized
- Error message not captured in traces (only "Connection error." string)
- Added enhanced error logging with traceback to `agent_graph.py` (not yet tested)

**Gold SQL Incompatibility:**
- LiveSQLBench gold SQL uses PostgreSQL-specific syntax
- Only 1/3 gold SQL executed successfully on SQLite in full test
- Scorer handles this by excluding from `result_match_rate`, but this limits evaluation coverage

**Cosmetic Issue:**
- Model name in output still shows "mistral-small-latest" instead of "tinker-api" or "Kimi-K2.5"

---

## Test Results

### First Smoke Test (`smoke-20260409-112954-bfciiu`)
```
questions_total: 3
executable_count: 3
executable_rate: 1.0000
gold_executable_count: 1
result_scoreable_count: 1
result_match_count: 0
result_match_rate: 0.0
latency_mean_ms: 62823.0
```

### Full Test (`full-20260409-115446-vfmrb0`)
```
questions_total: 3
executable_count: 1
executable_rate: 0.3333
gold_executable_count: 1
result_scoreable_count: 0
result_match_count: 0
result_match_rate: 0.0
latency_mean_ms: 592214.67
```

**RCA Summary:**
- `empty_result_mishandled`: 2
- `wrong_aggregation_or_filter`: 1 (smoke test)
- `tool_recovery_failed`: 2 (full test — indicates agent crashes)

---

## Key Files Modified

### Agent Files
- `candidate/llm.py` — Tinker API configuration (copied from kimiGoBrr)
- `candidate/react.py` — Custom ReAct with Kimi compat (copied from kimiGoBrr)
- `candidate/kimi_compat.py` — Kimi tool call parser (copied from kimiGoBrr, bug fix applied)
- `candidate/agent_graph.py` — Agent wiring (enhanced error logging added)
- `candidate/prompt.py` — SQLite/LiveSQLBench system prompt (already existed)
- `candidate/tools.py` — SQLite tools (already existed, no changes needed)

### Harness Files
- `harness/benchmark.py` — Updated for LiveSQLBench format + execution scoring
- `harness/scorer.py` — Rewritten for SQLite execution-based scoring

### Configuration Files
- `.env` — Created with TINKER_* and LIVESQL_ROOT variables
- `requirements.txt` — Updated to use `langchain-openai` instead of `langchain-mistralai`

### Documentation Files
- `program.md` — Rewritten for LiveSQLBench + Tinker API autoresearch loop
- `results.tsv` — Updated format and recorded baseline
- `README.md` — Updated current findings

### CLI Files
- `cli/run.py` — Updated default model and preflight endpoint

---

## Next Steps

### Immediate (Debugging)
1. **Fix agent crashes** — Run single question with enhanced error logging to see actual crash
2. **Investigate high latency** — Check if Tinker API is slow or agent is looping
3. **Fix cosmetic model_name** — Update `agent_graph.py` to pass correct model name to result

### Short Term (Autoresearch Loop)
1. Run RCA on completed experiments to analyze failure patterns
2. Make one focused improvement based on RCA (likely prompt tuning for:
   - Empty result handling
   - Aggregation/filter logic
   - Domain knowledge gaps like LIF formula)
3. Run small benchmark (3-5 questions) to test improvement
4. Log results in `results.tsv`
5. Iterate using keep/discard policy

### Medium Term
1. Run larger benchmark (25+ questions) to establish proper baseline
2. Consider improving gold SQL compatibility (SQLite-compatible gold subset)
3. Optimize agent latency (caching, fewer tool calls, better stopping criteria)

---

## Environment Variables

Required:
- `TINKER_API_KEY` — Tinker API key for Kimi-K2.5
- `TINKER_MODEL_PATH` — Model path (e.g., `moonshotai/Kimi-K2.5`)
- `TINKER_BASE_URL` — Tinker API endpoint

Optional:
- `LIVESQL_ROOT` — Path to LiveSQLBench data (default: `data/livesqlbench`)
- `TRACE_BACKEND` — `phoenix` or `none` (default: `none`)
- `PHOENIX_COLLECTOR_ENDPOINT` — Phoenix endpoint (default: `http://127.0.0.1:6006/v1/traces`)

---

## Running Benchmarks

### Smoke test (fast iteration)
```bash
.venv\Scripts\python.exe cli/run.py --split smoke --limit 10
```

### Medium test
```bash
.venv\Scripts\python.exe cli/run.py --split smoke --limit 25
```

### Full test
```bash
.venv\Scripts\python.exe cli/run.py --split full
```

### With metadata
```bash
.venv\Scripts\python.exe cli/run.py --split smoke --limit 10 \
  --experiment-name "baseline-v0" \
  --notes "Initial LiveSQLBench baseline" \
  --candidate-version "candidate_v0" \
  --prompt-version "prompt_v0" \
  --tags "baseline,livesqlbench"
```

---

## RCA Workflow

```bash
# Generate RCA
.venv\Scripts\python.exe cli/rca.py generate --run-id <run_id>

# Summarize RCA
.venv\Scripts\python.exe cli/rca.py summarize --run-id <run_id>

# Update RCA tag manually if needed
.venv\Scripts\python.exe cli/rca.py update --run-id <run_id> --question-id <qid> --tag <tag> --rca-type candidate_fix --confidence high --manual-notes "reason"
```

---

## Notes

- The agent uses Kimi-K2.5 via Tinker API with custom text-emitted tool call compatibility
- Gold SQL from LiveSQLBench is PostgreSQL-centric; only subset is SQLite-compatible
- Execution-based scoring runs both predicted and gold SQL against SQLite and compares normalized rows
- The system is designed for autonomous improvement via the `program.md` iteration loop
- Trace capture and RCA provide detailed failure analysis for systematic debugging
