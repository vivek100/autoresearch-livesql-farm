# analytics-agent-autoresearch program.md

This file is the main operating manual for autonomous improvement.
Read this first before editing code or running experiments.

## 1) Mission

Improve SQL generation accuracy on LiveSQLBench benchmark using a strict keep/discard loop.

This is an **autoresearch repo** — AI agents (like Claude Code) are expected to:
- Read prior run results and RCA
- Form hypotheses about failure patterns
- Edit candidate code (prompt, tools, agent wiring)
- Run experiments and evaluate
- Keep or discard changes based on metric deltas
- Repeat autonomously without human intervention

The runtime is:

1. **OpenAI GPT-5.4-mini** via langchain-openai ChatOpenAI
2. LangGraph ReAct agent (`langgraph.prebuilt.create_react_agent`)
3. **Ghost PostgreSQL** `describe_schema` + `execute_sql` tools (via Ghost CLI)
4. Execution-based scoring: predicted SQL vs gold SQL both run against PostgreSQL via Ghost
5. Local JSON/JSONL artifact logging with timing instrumentation

### Scoring Model

Two metrics are tracked per run:

- **executable_rate**: fraction of questions where the agent's SQL executes without error
- **result_match_rate**: fraction of questions where both predicted and gold SQL execute and produce identical normalized rows (this is the primary accuracy metric)

Gold SQL is native PostgreSQL (uses STDDEV, JSON functions, CTEs, lateral joins, etc.). Scoring runs both predicted and gold SQL on the same Ghost PostgreSQL instance.

## 2) Repository Surfaces

### Mutable during normal candidate loop

1. `candidate/prompt.py` — system prompt (primary improvement lever)
2. `candidate/tools.py` — Ghost PostgreSQL tool definitions
3. `candidate/agent_graph.py` — agent wiring, caching, and run logic
4. `candidate/response_parser.py` — output parsing
5. `candidate/llm.py` — LLM configuration (model, temperature, timeout)
6. `candidate/react.py` — ReAct agent builder with safe tool wrapping
7. `candidate/tracing.py` — local callback trace handler

### Read-only during normal candidate loop

1. `harness/*` — benchmark runner, scorer, RCA, trace writer
2. `data/livesqlbench/dataset.json` — benchmark questions + gold SQL
3. `data/livesqlbench/livesqlbench_gt_kg_testcases_0528.jsonl` — full 270-question ground truth

Harness changes are allowed only in explicit harness-maintenance mode.

## 3) Required Context Before Any Iteration

Read these files every session:

1. `program.md` (this file)
2. `results.tsv` (latest rows)
3. Latest run folder summary:
   - `runs/<latest_run>/summary.json`
   - `runs/<latest_run>/aggregate_rca.json`
   - `runs/<latest_run>/predictions.jsonl` (scan for failure patterns)

## 4) Environment + Data Checks

Use project venv:

1. `.venv\Scripts\python.exe --version`

Env variables (set in `.env`):

1. `OPENAI_API_KEY` (required — OpenAI API key)
2. `OPENAI_MODEL` (optional — defaults to `gpt-5.4-mini`)
3. `GHOST_DB_ID` (required — Ghost database instance ID, get from `ghost list`)
4. `LIVESQL_ROOT` (optional — path to LiveSQLBench data, defaults to `data/livesqlbench`)
5. `TRACE_BACKEND` (optional — `none`, default `none`)

Ghost CLI must be installed at `C:\Users\shukl\AppData\Local\Programs\Ghost\ghost.exe`.

Benchmark data expected at `LIVESQL_ROOT` or `data/livesqlbench/`:

1. `dataset.json` — LiveSQLBench questions with `sol_sql` gold SQL arrays
2. `livesqlbench_gt_kg_testcases_0528.jsonl` — full ground truth + knowledge IDs

Ghost database must have schemas matching `db_id` values in dataset (e.g. `alien`).

### Ghost Database Setup

Ghost databases are cloud PostgreSQL instances managed via the Ghost CLI:

```
ghost list                          # show available databases
ghost connect <id>                  # get connection string
ghost sql <id> "SELECT 1"           # run a query
echo "SELECT 1" | ghost sql <id>    # run via stdin (preferred — avoids quoting issues)
```

The benchmark uses `ghost sql` via stdin for both agent tools and scoring.

## 5) Benchmark Commands

Smoke loop (fast iteration, 3 questions):

1. `.venv\Scripts\python.exe -m cli.run --split smoke --limit 3`

Medium check (10 questions = full alien dataset):

1. `.venv\Scripts\python.exe -m cli.run --split full --limit 10`

Full check:

1. `.venv\Scripts\python.exe -m cli.run --split full`

Recommended metadata fields each run:

1. `--experiment-name "short-name"`
2. `--notes "what changed"`
3. `--candidate-version "candidate_vX"`
4. `--prompt-version "prompt_vX"`
5. `--tags "tag1,tag2"`

## 6) Keep/Discard Decision Policy

After each run:

1. Compare to prior best run in same lane + harness version tuple.
2. Primary metric: `result_match_rate`.
3. Secondary metric: `executable_rate`.
4. Keep if result_match_rate improves and executable_rate does not regress.
5. Discard if either metric regresses or improvement is noisy/unjustified complexity.

Log every decision in `results.tsv`:

`run_id	split	total	executable_rate	result_match_rate	status	description`

Statuses: `keep`, `discard`, `crash`

## 7) Iteration Loop (Autonomous)

Repeat:

1. Read latest `results.tsv` and latest RCA summary.
2. Scan `predictions.jsonl` for failure patterns (predicted_error, gold_error, result_match).
3. Choose one focused hypothesis targeting:
   - Non-executable SQL → fix schema inspection or SQL generation in prompt
   - Executable but wrong results → fix aggregation/filter/join logic in prompt
   - Empty result handling → improve null/empty response contract
   - Missing domain knowledge → add external knowledge hints to prompt
4. Edit candidate files only (primarily `prompt.py`).
5. Run smoke benchmark (`--limit 3`).
6. Generate RCA and review.
7. Apply keep/discard decision.
8. Periodically run full benchmark (`--limit 10`) for confirmation.

Do not stop to ask for permission between iterations unless blocked by missing credentials/data or critical runtime failures.

### Key Improvement Levers

In priority order:

1. **System prompt** (`candidate/prompt.py`) — workflow instructions, SQL rules, few-shot examples
2. **Tool descriptions** (`candidate/tools.py`) — help the model understand what each tool returns
3. **Response parser** (`candidate/response_parser.py`) — extract SQL and answers more reliably
4. **Agent wiring** (`candidate/agent_graph.py`) — max iterations, fallback behavior

### Known Challenges

1. **External knowledge**: Questions reference domain-specific formulas (TOLS, SNQI, LIF, etc.) via `external_knowledge` IDs. The knowledge base files (`*_kb.jsonl`) are not yet available locally. The agent must infer meaning from column names or the prompt must provide hints.
2. **Ghost flakiness**: Ghost CLI occasionally returns "database is not yet ready" — retries may be needed.
3. **Result comparison**: Scorer normalizes all values to lowercase strings for comparison. Floating-point precision differences or column ordering can cause false negatives.

## 8) Architecture History

- **v0 (Kimi-K2.5 + SQLite)**: Initial setup using Tinker API proxy to Kimi-K2.5. Extremely slow (~5-7 min/question) due to thinking mode and Tinker proxy latency. Gold SQL was PostgreSQL dialect that mostly failed on SQLite (3/10 executable). Abandoned.
- **v1 (GPT-5.4-mini + Ghost PostgreSQL)**: Current setup. ~6-9s per question. Gold SQL runs natively on PostgreSQL. Scoring works correctly. First correct answer achieved on alien_2.
