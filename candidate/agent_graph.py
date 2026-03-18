from __future__ import annotations

"""
Thin ReAct runtime wrapper for candidate execution.

Design intent:
- Always use LangGraph built-in ReAct agent.
- Keep the execution flow easy to read.
- Return a strict, scorer-friendly result envelope.
"""

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_mistralai import ChatMistralAI
from langgraph.prebuilt import create_react_agent

from .observability import (
    configure_observability,
    force_flush_observability,
    mark_span_offset,
    spans_since,
)
from .prompt import SYSTEM_PROMPT
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


def load_env() -> None:
    """Load env in this order: project-local, then repo-root fallback."""
    here = Path(__file__).resolve()
    project_dir = here.parents[1]
    repo_root = project_dir.parent
    local_env = project_dir / ".env"
    root_env = repo_root / ".env"

    if local_env.exists():
        load_dotenv(local_env)
    if root_env.exists():
        load_dotenv(root_env, override=False)


def resolve_default_db_path() -> str:
    """Resolve a default SQLite path for quick local smoke runs."""
    explicit = os.environ.get("DB_PATH")
    if explicit:
        return explicit

    spider_root = os.environ.get("SPIDER_ROOT")
    if spider_root:
        return str(Path(spider_root) / "database" / "concert_singer" / "concert_singer.sqlite")

    local_new = (
        Path("analytics-agent-autoresearch")
        / "data"
        / "spider"
        / "database"
        / "concert_singer"
        / "concert_singer.sqlite"
    )
    if local_new.exists():
        return local_new.as_posix()

    local_old = (
        Path("analytics-agent")
        / "data"
        / "spider"
        / "database"
        / "concert_singer"
        / "concert_singer.sqlite"
    )
    if local_old.exists():
        return local_old.as_posix()

    fallback = Path(r"C:\spider_data\spider_data\database\concert_singer\concert_singer.sqlite")
    return fallback.as_posix()


def build_react_agent(model_name: str, db_path: str):
    """Construct LangGraph built-in ReAct agent for the provided DB path."""
    load_env()
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY missing.")
    llm = ChatMistralAI(model=model_name, api_key=api_key, temperature=0)
    tools = build_v0_tools(db_path=db_path)
    return create_react_agent(model=llm, tools=tools, prompt=SYSTEM_PROMPT)


def run_candidate_question(
    question: str,
    db_id: str,
    db_path: str | None = None,
    model_name: str = "mistral-small-latest",
) -> CandidateRunResult:
    """
    Execute one question through ReAct agent and return normalized result.

    The function intentionally keeps logic linear:
    1) invoke agent
    2) parse output JSON
    3) extract SQL fallback from tool events
    4) return strict result contract
    """
    started = time.perf_counter()
    resolved_db_path = db_path or resolve_default_db_path()
    observability_state = configure_observability()
    span_offset = mark_span_offset()

    try:
        react_agent = build_react_agent(model_name=model_name, db_path=resolved_db_path)
        trace_handler = LocalTraceCallbackHandler()
        result = react_agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"callbacks": [trace_handler]},
        )
        final_text = extract_answer_text(result)
        parsed = parse_json_block(final_text)
        messages = result.get("messages") if isinstance(result, dict) else []
        msg_list = messages if isinstance(messages, list) else []
        callback_events = trace_handler.to_events()
        # Callback traces are preferred; message reconstruction is fallback.
        events = callback_events if callback_events else events_from_messages(msg_list)

        raw_answer = parsed.get("answer_value")
        normalized = scalar_from_any(raw_answer)

        answer = AnswerEnvelope(
            raw=raw_answer,
            normalized=normalized,
            kind=kind_from_value(normalized if normalized is not None else raw_answer),
        )
        answer_text = parsed.get("answer_text")
        final_sql = extract_final_sql(events=events, parsed=parsed)
        status = "success"
        error = None
    except Exception as exc:
        final_text = ""
        events = []
        answer = AnswerEnvelope(raw=None, normalized=None, kind="null")
        answer_text = None
        final_sql = None
        status = "error"
        error = str(exc)

    force_flush_observability()
    phoenix_spans = spans_since(span_offset)
    latency_ms = int((time.perf_counter() - started) * 1000)
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
            "answer_text": answer_text,
            "observability": observability_state,
            "phoenix_spans": phoenix_spans,
        },
        model_name=model_name,
        error=error,
    )
