from __future__ import annotations

import os
import re
import subprocess
from typing import Any


GHOST_EXE = os.getenv(
    "GHOST_EXE", r"C:\Users\shukl\AppData\Local\Programs\Ghost\ghost.exe"
)
GHOST_PG_URI = os.getenv("GHOST_PG_URI", "")


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


def _normalize_rows(rows: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Normalize row values to strings for stable comparison."""
    normalized = []
    for row in rows:
        normalized.append(tuple(str(v).strip().lower() if v is not None else "" for v in row))
    return sorted(normalized)


def _strip_pg_comments(sql: str) -> str:
    """Strip block comments from SQL (gold SQL often has intent comments)."""
    return re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL).strip()


def _parse_ghost_output(output: str) -> list[tuple[str, ...]]:
    """Parse Ghost CLI tabular output into rows of string tuples.

    Ghost output looks like:
     col1 | col2 | col3
    ------+------+------
     val1 | val2 | val3
     val4 | val5 | val6
    (2 rows)
    """
    lines = output.strip().split("\n")
    rows: list[tuple[str, ...]] = []
    data_started = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip SET responses, empty lines
        if stripped == "SET":
            continue
        # Separator: dashes, box-drawing chars (─ ═), or +
        if re.match(r"^[-─═+┼╪│|]+$", stripped.replace(" ", "")):
            data_started = True
            continue
        # Row count footer like "(2 rows)" or "(1 row)"
        if re.match(r"^\(\d+ rows?\)$", stripped):
            continue
        if not data_started:
            continue
        # Parse data row — split on │ (box-drawing) or | (ASCII)
        cells = tuple(c.strip() for c in re.split(r"[│|]", stripped))
        # Filter out empty edge cells from leading/trailing separators
        cells = tuple(c for c in cells if c != "")
        if cells:
            rows.append(cells)
    return rows


def _execute_sql_pg_direct(uri: str, schema: str, sql: str) -> tuple[bool, list[tuple[str, ...]], str]:
    """Execute SQL via direct psycopg2 connection. Returns (success, rows, error)."""
    import psycopg2

    try:
        conn = psycopg2.connect(uri, connect_timeout=15)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}"')
                cur.execute(sql)
                if cur.description is None:
                    return True, [], ""
                rows = cur.fetchall()
                str_rows = [tuple(str(v).strip() if v is not None else "" for v in row) for row in rows]
                return True, str_rows, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, [], f"{type(exc).__name__}: {str(exc)[:500]}"


def execute_sql_ghost(ghost_db_id: str, schema: str, sql: str) -> tuple[bool, list[tuple[str, ...]], str]:
    """Execute SQL against PostgreSQL via Ghost CLI or direct psycopg2. Returns (success, rows, error)."""
    if not sql or not sql.strip():
        return False, [], "empty_sql"
    cleaned = _strip_pg_comments(sql)
    if not cleaned:
        return False, [], "empty_after_comment_strip"

    # Use direct PostgreSQL if URI is configured
    if GHOST_PG_URI:
        return _execute_sql_pg_direct(GHOST_PG_URI, schema, cleaned)

    full_sql = f'SET search_path TO "{schema}"; {cleaned}'
    try:
        result = subprocess.run(
            [GHOST_EXE, "sql", ghost_db_id],
            input=full_sql,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "Ghost command failed"
            return False, [], err[:500]
        rows = _parse_ghost_output(result.stdout)
        return True, rows, ""
    except subprocess.TimeoutExpired:
        return False, [], "timeout_30s"
    except Exception as exc:
        return False, [], f"{type(exc).__name__}: {str(exc)[:500]}"


def score_execution(
    ghost_db_id: str,
    schema: str,
    predicted_sql: str,
    gold_sql: str,
) -> dict[str, Any]:
    """Score predicted SQL against gold SQL by executing both on PostgreSQL via Ghost."""
    pred_ok, pred_rows, pred_err = execute_sql_ghost(ghost_db_id, schema, predicted_sql)
    gold_ok, gold_rows, gold_err = execute_sql_ghost(ghost_db_id, schema, gold_sql)

    result_match = None
    if pred_ok and gold_ok:
        pred_norm = _normalize_rows(pred_rows)
        gold_norm = _normalize_rows(gold_rows)
        result_match = pred_norm == gold_norm

    return {
        "predicted_executable": pred_ok,
        "gold_executable": gold_ok,
        "result_match": result_match,
        "predicted_row_count": len(pred_rows) if pred_ok else 0,
        "gold_row_count": len(gold_rows) if gold_ok else 0,
        "predicted_error": pred_err,
        "gold_error": gold_err,
    }
