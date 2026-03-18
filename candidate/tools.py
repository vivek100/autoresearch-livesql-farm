from __future__ import annotations

"""
Minimal SQL/schema toolset for v0 candidate.

Keeping this module local avoids coupling to the legacy analytics-agent package.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class SchemaToolInput(BaseModel):
    table_name: str | None = Field(
        default=None,
        description="Optional table name to describe. If omitted, describes all tables.",
    )
    include_foreign_keys: bool = Field(
        default=True,
        description="Whether to include foreign key relationships.",
    )


class SQLToolInput(BaseModel):
    sql: str = Field(description="SQL query to execute.")
    preview_rows: int = Field(default=5, description="Number of preview rows to return.")


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    safe = table.replace('"', '""')
    rows = conn.execute(f'PRAGMA table_info("{safe}")').fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "cid": int(r[0]),
                "name": str(r[1]),
                "type": str(r[2] or ""),
                "notnull": bool(r[3]),
                "default": r[4],
                "pk": bool(r[5]),
            }
        )
    return out


def _table_foreign_keys(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    safe = table.replace('"', '""')
    rows = conn.execute(f'PRAGMA foreign_key_list("{safe}")').fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": int(r[0]),
                "seq": int(r[1]),
                "ref_table": str(r[2]),
                "from_col": str(r[3]),
                "to_col": str(r[4]),
            }
        )
    return out


def _describe_schema_impl(
    db_path: str,
    table_name: str | None = None,
    include_foreign_keys: bool = True,
) -> dict[str, Any]:
    # Resolve requested tables and return structured schema JSON.
    path = Path(db_path)
    if not path.exists():
        return {"ok": False, "error": f"DB not found: {db_path}"}

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(path.as_posix())
        all_tables = _list_tables(conn)
        if table_name:
            tables = [t for t in all_tables if t.lower() == table_name.lower()]
            if not tables:
                return {"ok": False, "error": f"Table not found: {table_name}", "available_tables": all_tables}
        else:
            tables = all_tables

        data: list[dict[str, Any]] = []
        for table in tables:
            row = {"table": table, "columns": _table_columns(conn, table)}
            if include_foreign_keys:
                row["foreign_keys"] = _table_foreign_keys(conn, table)
            data.append(row)

        return {"ok": True, "table_count": len(data), "tables": data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if conn is not None:
            conn.close()


def _to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    return str(value)


def _execute_sql_impl(sql: str, db_path: str, preview_rows: int = 5) -> dict[str, Any]:
    # Execute SQL and return compact preview payload for agent iteration.
    path = Path(db_path)
    if not path.exists():
        return {"ok": False, "error": f"DB not found: {db_path}"}

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(path.as_posix())
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in (cur.description or [])]
        preview = rows[: max(1, preview_rows)]
        dtypes = {
            col: (type(preview[0][i]).__name__ if preview else "unknown")
            for i, col in enumerate(cols)
        }
        return {
            "ok": True,
            "row_count": len(rows),
            "columns": cols,
            "preview_rows": _to_json_safe(preview),
            "dtypes": dtypes,
            "executed_sql": sql,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "attempted_sql": sql}
    finally:
        if conn is not None:
            conn.close()


def create_describe_schema_tool(db_path: str):
    @tool("describe_schema", args_schema=SchemaToolInput)
    def describe_schema(table_name: str | None = None, include_foreign_keys: bool = True) -> str:
        """Describe schema in JSON format."""
        result = _describe_schema_impl(
            db_path=db_path,
            table_name=table_name,
            include_foreign_keys=include_foreign_keys,
        )
        return json.dumps(result)

    return describe_schema


def create_execute_sql_tool(db_path: str):
    @tool("execute_sql", args_schema=SQLToolInput)
    def execute_sql(sql: str, preview_rows: int = 5) -> str:
        """Execute SQL and return JSON result."""
        result = _execute_sql_impl(sql=sql, db_path=db_path, preview_rows=preview_rows)
        return json.dumps(result)

    return execute_sql


def build_v0_tools(db_path: str):
    return [create_describe_schema_tool(db_path), create_execute_sql_tool(db_path)]
