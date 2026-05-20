"""FastMCP server exposing search / insert / aggregate tools and schema resources."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

# Make the implementation directory importable when launched by absolute path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import SQLiteAdapter, ValidationError  # noqa: E402
from init_db import DEFAULT_DB_PATH, create_database  # noqa: E402


DB_PATH = Path(os.environ.get("MCP_LAB_DB", str(DEFAULT_DB_PATH)))
if not DB_PATH.exists():
    create_database(DB_PATH)

adapter = SQLiteAdapter(DB_PATH)

mcp = FastMCP("SQLite Lab MCP Server")


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **payload}


@mcp.tool(name="search")
def search(
    table: str,
    filters: list[dict[str, Any]] | None = None,
    columns: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
    order_by: str | None = None,
    descending: bool = False,
) -> dict[str, Any]:
    """Search rows in a table.

    Args:
        table: target table name (e.g. "students").
        filters: list of {"column": str, "op": str, "value": any}.
            Allowed ops: eq, neq, lt, lte, gt, gte, like, in.
        columns: subset of columns to return; defaults to all.
        limit: max rows (1-500, default 20).
        offset: rows to skip (>= 0, default 0).
        order_by: column to order by.
        descending: sort descending if true.
    """
    try:
        result = adapter.search(
            table=table,
            columns=columns,
            filters=filters,
            limit=limit,
            offset=offset,
            order_by=order_by,
            descending=descending,
        )
        return _ok(result)
    except ValidationError as exc:
        return _error(str(exc))


@mcp.tool(name="insert")
def insert(table: str, values: dict[str, Any]) -> dict[str, Any]:
    """Insert a row into a table.

    Args:
        table: target table name.
        values: column -> value mapping. Must be non-empty and use known columns.

    Returns the inserted row including any generated primary key.
    """
    try:
        result = adapter.insert(table=table, values=values)
        return _ok(result)
    except ValidationError as exc:
        return _error(str(exc))


@mcp.tool(name="aggregate")
def aggregate(
    table: str,
    metric: str,
    column: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    group_by: str | None = None,
) -> dict[str, Any]:
    """Compute an aggregate metric on a table.

    Args:
        table: target table name.
        metric: one of count, avg, sum, min, max.
        column: column to aggregate (required for non-count metrics).
        filters: optional WHERE filters, same format as `search`.
        group_by: optional grouping column.
    """
    try:
        result = adapter.aggregate(
            table=table,
            metric=metric,
            column=column,
            filters=filters,
            group_by=group_by,
        )
        return _ok(result)
    except ValidationError as exc:
        return _error(str(exc))


@mcp.resource("schema://database")
def database_schema() -> str:
    """Return the full database schema as JSON."""
    return json.dumps(
        {"database": str(DB_PATH), "tables": adapter.full_schema()},
        indent=2,
        default=str,
    )


@mcp.resource("schema://table/{table_name}")
def table_schema(table_name: str) -> str:
    """Return a single table's schema as JSON."""
    try:
        return json.dumps(
            {"table": table_name, "columns": adapter.get_table_schema(table_name)},
            indent=2,
            default=str,
        )
    except ValidationError as exc:
        return json.dumps({"error": str(exc)}, indent=2)


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "stdio":
        mcp.run()
    elif transport in {"http", "streamable-http"}:
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", "8000"))
        mcp.run(transport="streamable-http", host=host, port=port)
    elif transport == "sse":
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", "8000"))
        mcp.run(transport="sse", host=host, port=port)
    else:
        raise SystemExit(f"unknown MCP_TRANSPORT: {transport}")


if __name__ == "__main__":
    main()
