"""
Ghost PostgreSQL toolset for the candidate agent.

Uses Ghost CLI or direct psycopg2 connection to execute SQL against
remote PostgreSQL databases.  When GHOST_PG_URI is set, psycopg2 is
used (no Ghost CLI binary needed — ideal for sandboxes).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

GHOST_EXE = os.getenv(
    "GHOST_EXE", r"C:\Users\shukl\AppData\Local\Programs\Ghost\ghost.exe"
)
GHOST_PG_URI = os.getenv("GHOST_PG_URI", "")


def _run_pg_direct(uri: str, sql: str) -> str:
    """Execute SQL via direct psycopg2 connection. Returns tabular text."""
    import psycopg2

    conn = psycopg2.connect(uri, connect_timeout=15)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Execute all statements (SET search_path + query)
            statements = [s.strip() for s in sql.split(";") if s.strip()]
            for stmt in statements[:-1]:
                cur.execute(stmt)
            if not statements:
                return ""
            cur.execute(statements[-1])
            if cur.description is None:
                return ""
            col_names = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            # Format as tabular text (same style as Ghost CLI output)
            lines = [" | ".join(col_names)]
            lines.append("-+-".join("-" * len(c) for c in col_names))
            for row in rows:
                lines.append(" | ".join(str(v) if v is not None else "" for v in row))
            lines.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
            return "\n".join(lines)
    finally:
        conn.close()


def _run_ghost(db_id: str, sql: str) -> str:
    """Run a SQL query via Ghost CLI (piped via stdin to avoid quoting issues)."""
    # Use direct PostgreSQL if URI is configured
    if GHOST_PG_URI:
        return _run_pg_direct(GHOST_PG_URI, sql)

    result = subprocess.run(
        [GHOST_EXE, "sql", db_id],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Ghost command failed.")
    return result.stdout.strip()


# ── Tool input schemas ────────────────────────────────────────────────

class SchemaToolInput(BaseModel):
    table_name: str | None = Field(
        default=None,
        description="Optional table name to describe. If omitted, describes all tables.",
    )


class SQLToolInput(BaseModel):
    sql: str = Field(description="SQL query to execute.")


# ── Tool factories ────────────────────────────────────────────────────

def create_describe_schema_tool(ghost_db_id: str, schema: str):
    @tool("describe_schema", args_schema=SchemaToolInput)
    def describe_schema(table_name: str | None = None) -> str:
        """Describe table schema (columns, types, keys) in the PostgreSQL database."""
        if table_name:
            sql = f"""
            SET search_path TO "{schema}";
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = '{schema}' AND table_name = '{table_name}'
            ORDER BY ordinal_position;
            """
        else:
            sql = f"""
            SET search_path TO "{schema}";
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = '{schema}'
            ORDER BY table_name, ordinal_position;
            """
        return _run_ghost(ghost_db_id, sql)

    return describe_schema


def create_execute_sql_tool(ghost_db_id: str, schema: str):
    @tool("execute_sql", args_schema=SQLToolInput)
    def execute_sql(sql: str) -> str:
        """Execute a SQL query against the PostgreSQL database and return results."""
        full_sql = f'SET search_path TO "{schema}"; {sql}'
        return _run_ghost(ghost_db_id, full_sql)

    return execute_sql


def build_v0_tools(ghost_db_id: str, schema: str):
    """Build the tool list for one Ghost DB + schema."""
    return [
        create_describe_schema_tool(ghost_db_id, schema),
        create_execute_sql_tool(ghost_db_id, schema),
    ]
