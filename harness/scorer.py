from __future__ import annotations

from typing import Any


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except Exception:
        return None


def extract_gold_value(gold_rows: list[tuple[Any, ...]] | list[list[Any]]) -> Any:
    if not gold_rows:
        return None
    row = gold_rows[0]
    if not row:
        return None
    return row[0]


def score(agent_value: Any, gold_rows: list[tuple[Any, ...]] | list[list[Any]], decimals: int = 2) -> bool:
    gold_value = extract_gold_value(gold_rows)
    a = _as_float(agent_value)
    g = _as_float(gold_value)
    if a is not None and g is not None:
        return round(a, decimals) == round(g, decimals)
    return str(agent_value).strip() == str(gold_value).strip()

