"""
Simple ReAct agent executor with trace capturing.

Keeps it minimal: build agent once, invoke, capture traces, return result.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from .llm import create_llm
from .prompt import SYSTEM_PROMPT
from .react import build_react_agent as build_custom_react_agent
from .response_parser import (
    extract_answer_text,
    extract_final_sql,
    kind_from_value,
    parse_json_block,
    scalar_from_any,
)
from .tools import build_v0_tools
from .tracing import LocalTraceCallbackHandler, events_from_messages
from .types import AnswerEnvelope, CandidateRunResult


# ── Simple timer helper ───────────────────────────────────────────────

def _ts(label: str, t0: float) -> float:
    now = time.perf_counter()
    elapsed = now - t0
    print(f"  [{elapsed:7.2f}s] {label}", file=sys.stderr, flush=True)
    return now


def load_env() -> None:
    here = Path(__file__).resolve()
    project_dir = here.parents[1]
    repo_root = project_dir.parent
    for env_path in [project_dir / ".env", repo_root / ".env"]:
        if env_path.exists():
            load_dotenv(env_path, override=False)


def resolve_ghost_db_id() -> str:
    """Get the Ghost database ID from env."""
    db_id = os.environ.get("GHOST_DB_ID")
    if not db_id:
        raise RuntimeError("GHOST_DB_ID is not set. Run 'ghost list' to find your DB ID.")
    return db_id


# ── Module-level agent cache ──────────────────────────────────────────
_AGENT_CACHE: dict[str, object] = {}


def _get_or_build_agent(ghost_db_id: str, schema: str, model_name: str):
    """Return cached agent for this ghost_db + schema, or build a new one."""
    cache_key = f"{ghost_db_id}::{schema}::{model_name}"
    if cache_key not in _AGENT_CACHE:
        t0 = time.perf_counter()
        print(f"\n== Building agent (ghost={ghost_db_id} schema={schema}) ==", file=sys.stderr, flush=True)

        load_env()
        t0 = _ts("load_env", t0)

        llm = create_llm()
        t0 = _ts("create_llm", t0)

        tools = build_v0_tools(ghost_db_id=ghost_db_id, schema=schema)
        t0 = _ts("build_v0_tools", t0)

        agent = build_custom_react_agent(llm=llm, tools=tools, system_prompt=SYSTEM_PROMPT)
        _ts("build_react_agent", t0)

        _AGENT_CACHE[cache_key] = agent
        print("== Agent ready ==\n", file=sys.stderr, flush=True)
    return _AGENT_CACHE[cache_key]


def run_candidate_question(
    question: str,
    db_id: str,
    ghost_db_id: str | None = None,
    model_name: str = "openai",
) -> CandidateRunResult:
    """Execute one question through ReAct agent and return normalized result.

    db_id is used as the PostgreSQL schema name (e.g. 'alien').
    ghost_db_id is the Ghost database instance ID.
    """
    wall_start = time.perf_counter()
    resolved_ghost_id = ghost_db_id or resolve_ghost_db_id()
    schema = db_id  # db_id maps directly to schema name
    trace_handler = LocalTraceCallbackHandler()
    result = None

    print(f"\n{'='*60}", file=sys.stderr, flush=True)
    print(f"QUESTION: {question[:100]}...", file=sys.stderr, flush=True)
    print(f"GHOST: {resolved_ghost_id}  SCHEMA: {schema}", file=sys.stderr, flush=True)
    print(f"{'='*60}", file=sys.stderr, flush=True)

    try:
        t0 = time.perf_counter()
        agent = _get_or_build_agent(resolved_ghost_id, schema, model_name)
        t0 = _ts("get_or_build_agent", t0)

        print("  >> agent.invoke() starting...", file=sys.stderr, flush=True)
        result = agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"callbacks": [trace_handler]},
        )
        t0 = _ts("agent.invoke() DONE", t0)

        final_text = extract_answer_text(result)
        parsed = parse_json_block(final_text)
        t0 = _ts("parse response", t0)

        messages = result.get("messages") if isinstance(result, dict) else []
        msg_list = messages if isinstance(messages, list) else []
        events = trace_handler.to_events() or events_from_messages(msg_list)
        t0 = _ts("extract trace events", t0)

        raw_answer = parsed.get("answer_value")
        normalized = scalar_from_any(raw_answer)
        answer = AnswerEnvelope(
            raw=raw_answer,
            normalized=normalized,
            kind=kind_from_value(normalized if normalized is not None else raw_answer),
        )
        final_sql = extract_final_sql(events=events, parsed=parsed)
        _ts("build answer envelope", t0)

        status = "success"
        error = None

    except Exception as exc:
        import traceback

        _ts("EXCEPTION hit", wall_start)
        print(f"  ERROR: {exc}", file=sys.stderr, flush=True)

        events = trace_handler.to_events()
        if not events and isinstance(result, dict):
            msg_list = result.get("messages", [])
            if isinstance(msg_list, list):
                events = events_from_messages(msg_list)

        answer = AnswerEnvelope(raw=None, normalized=None, kind="null")
        parsed = {}
        final_text = ""
        final_sql = extract_final_sql(events=events, parsed=parsed)
        status = "error"
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

    latency_ms = int((time.perf_counter() - wall_start) * 1000)

    # ── Print trace event summary ──
    print(f"\n  TRACE EVENTS:", file=sys.stderr, flush=True)
    for ev in events:
        etype = ev.get("type", "?")
        step = ev.get("step_index", "?")
        payload = ev.get("payload", {})
        if etype == "llm_start":
            meta = payload.get("metadata", {})
            print(f"    step={step} {etype:15s}  model={meta.get('ls_model_name', '?')}", file=sys.stderr, flush=True)
        elif etype == "llm_end":
            usage = payload.get("llm_output", {}).get("token_usage", {})
            print(f"    step={step} {etype:15s}  tokens={usage.get('total_tokens', '?')} (prompt={usage.get('prompt_tokens', '?')}, completion={usage.get('completion_tokens', '?')})", file=sys.stderr, flush=True)
        elif etype == "tool_call":
            print(f"    step={step} {etype:15s}  tool={payload.get('tool_name', '?')}", file=sys.stderr, flush=True)
        elif etype == "tool_result":
            res = str(payload.get("result", ""))[:80]
            print(f"    step={step} {etype:15s}  result={res}...", file=sys.stderr, flush=True)
        else:
            print(f"    step={step} {etype:15s}", file=sys.stderr, flush=True)

    print(f"\n  TOTAL: {latency_ms}ms  status={status}  sql={'yes' if final_sql else 'no'}", file=sys.stderr, flush=True)
    print(f"{'='*60}\n", file=sys.stderr, flush=True)

    return CandidateRunResult(
        question=question,
        db_id=db_id,
        status=status,
        answer=answer,
        final_sql=final_sql,
        steps_used=len([e for e in events if e.get("type") == "tool_call"]),
        artifacts={
            "trace_events": events,
            "latency_ms": latency_ms,
            "raw_final_content": final_text,
            "answer_text": parsed.get("answer_text") if status == "success" else None,
            "error": error,
            "observability": {"initialized": True, "backend": "none", "enabled": False},
            "phoenix_spans": [],
        },
        model_name=model_name,
        error=error,
    )
