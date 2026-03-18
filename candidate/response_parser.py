from __future__ import annotations

"""
Response parsing helpers for ReAct agent outputs.
"""

import json
from typing import Any


def extract_answer_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        output = result.get("output")
        if isinstance(output, str):
            return output
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict):
                content = last.get("content")
                if isinstance(content, str):
                    return content
            content = getattr(last, "content", None)
            if isinstance(content, str):
                return content
    return str(result)


def parse_json_block(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            return json.loads(stripped)
        except Exception:
            return {}
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except Exception:
            return {}
    return {}


def scalar_from_any(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return scalar_from_any(value[0])
    if isinstance(value, dict):
        for key in ("answer_value", "value", "result"):
            if key in value:
                return scalar_from_any(value[key])
        if value:
            return scalar_from_any(next(iter(value.values())))
    return None


def kind_from_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "scalar"
    if isinstance(value, str):
        return "text"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "rowset"
    return "unknown"


def extract_final_sql(events: list[dict[str, Any]], parsed: dict[str, Any]) -> str | None:
    if isinstance(parsed.get("sql"), str) and parsed.get("sql", "").strip():
        return str(parsed["sql"]).strip()
    for event in reversed(events):
        if event.get("type") != "tool_call":
            continue
        payload = event.get("payload", {})
        if payload.get("tool_name") == "execute_sql":
            args = payload.get("args") or payload.get("inputs") or {}
            sql = args.get("sql")
            if isinstance(sql, str):
                return sql
    return None
