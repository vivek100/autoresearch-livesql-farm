# analytics-agent-autoresearch program.md

This file is the main operating manual for autonomous improvement.
Read this first before editing code or running experiments.

## 1) Mission

Improve question-answer accuracy on Spider benchmark slices using a strict keep/discard loop.

The runtime is fixed to:

1. LangGraph built-in ReAct agent
2. SQL + schema tools (v0)
3. local JSON/JSONL artifact logging

## 2) Repository Surfaces

### Mutable during normal candidate loop

1. `analytics-agent-autoresearch/candidate/prompt.py`
2. `analytics-agent-autoresearch/candidate/tools.py`
3. `analytics-agent-autoresearch/candidate/agent_graph.py`
4. `analytics-agent-autoresearch/candidate/response_parser.py`
5. `analytics-agent-autoresearch/candidate/tracing.py`
6. `analytics-agent-autoresearch/candidate/observability.py`

### Read-only during normal candidate loop

1. `analytics-agent-autoresearch/harness/*`
2. benchmark dataset and gold SQL
3. scoring version logic

Harness changes are allowed only in explicit harness-maintenance mode.

## 3) Required Context Before Any Iteration

Read these files every session:

1. `analytics-agent-autoresearch/README.md`
2. `analytics-agent-autoresearch/program.md` (this file)
3. `analytics-agent-autoresearch/results.tsv` (latest rows)
4. latest run folder summary:
   - `analytics-agent-autoresearch/runs/<latest_run>/summary.json`
   - `analytics-agent-autoresearch/runs/<latest_run>/aggregate_rca.json`

## 4) Environment + Data Checks

Use parent venv:

1. `.\.venv\Scripts\python.exe --version`

Env variables:

1. `MISTRAL_API_KEY` (required)
2. `SPIDER_ROOT` (optional override)
3. `TRACE_BACKEND` (optional; default `phoenix`, set `none` to disable)
4. `PHOENIX_COLLECTOR_ENDPOINT` (optional; default `http://127.0.0.1:6006/v1/traces`)

If collector endpoint is unreachable, tracing is auto-disabled for that process and execution continues.
Run trace artifacts include `phoenix_spans` JSON per question for local inspection.

Benchmark data expected:

1. `<SPIDER_ROOT>/dev.json`
2. `<SPIDER_ROOT>/database/<db_id>/<db_id>.sqlite`

If not using `SPIDER_ROOT`, fallback paths are handled in code.

## 5) Branch + Lineage Workflow

Always run experiments on explicit experiment branches.

Useful commands:

1. `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/branch.py status`
2. `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/branch.py create --series s1 --lane small --iteration 0 --switch`
3. `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/branch.py start-from-run --run-id <run_id> --switch`

Lineage fields must be passed on run commands when restarting:

1. `--from-run <run_id>`
2. `--from-git-ref <commit_or_tag>`

## 6) Benchmark Commands

Smoke loop:

1. `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/run.py --split smoke --limit 25 --lane small`

Full check:

1. `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/run.py --split full --limit 150 --lane small`

Recommended metadata fields each run:

1. `--experiment-name "short-name"`
2. `--notes "what changed"`
3. `--candidate-version "candidate_vX"`
4. `--prompt-version "prompt_vX"`
5. `--tags "tag1,tag2"`
6. `--metadata-json "{\"hypothesis\":\"...\",\"owner\":\"agent\"}"`

## 7) RCA Workflow

For each run:

1. Generate RCA:
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/rca.py generate --run-id <run_id>`
2. Summarize RCA:
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/rca.py summarize --run-id <run_id>`
3. If deterministic tags are wrong, update rows:
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/rca.py update --run-id <run_id> --question-id <qid> --tag <tag> --rca-type candidate_fix --confidence high --manual-notes "reason"`
4. Link known fixes:
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/rca.py link-fix --run-id <run_id> --question-id <qid> --fix-id <fix_id>`

RCA outputs:

1. `runs/<run_id>/rca.jsonl`
2. `runs/<run_id>/aggregate_rca.json`
3. `runs/<run_id>/rca_updates.jsonl`
4. `runs/<run_id>/fix_links.jsonl`

## 8) Prompt Governance Rules

Before accepting prompt changes:

1. `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/prompt_guard.py check --max-chars 5000 --max-tokens-est 1400`
2. If comparing prompt revisions:
   - `.\.venv\Scripts\python.exe analytics-agent-autoresearch/cli/prompt_guard.py diff --old-prompt-file <old> --new-prompt-file <new> --max-delta-chars 500`

Prompt updates require:

1. repeated RCA pattern evidence (not one-off failures)
2. rationale in run `--notes` and `--metadata-json`

## 9) Keep/Discard Decision Policy

After each smoke run:

1. Compare to prior best run in same lane + harness version tuple.
2. Keep if accuracy improves and failure profile does not regress materially.
3. Discard if accuracy regresses or improvement is noisy/unjustified complexity.

Log every decision in `results.tsv`:

`commit	run_id	model_lane	split	accuracy	status	description`

Statuses:

1. `keep`
2. `discard`
3. `crash`

## 10) Iteration Loop (Autonomous)

Repeat:

1. Read latest `results.tsv` and latest RCA summary.
2. Choose one focused hypothesis.
3. Edit candidate files only.
4. Run smoke benchmark.
5. Generate RCA and review.
6. Apply keep/discard decision.
7. Periodically run full benchmark for confirmation.

Do not stop to ask for permission between iterations unless blocked by missing credentials/data or critical runtime failures.

## 11) Harness-Maintenance Mode (Separate)

Only in dedicated maintenance branch:

1. change scorer logic
2. change benchmark selection policy
3. change trace schema
4. change deterministic RCA classifier

When done:

1. bump versions in `harness/versions.py`
2. mark new experiment series
3. avoid direct cross-series comparisons without caveats
