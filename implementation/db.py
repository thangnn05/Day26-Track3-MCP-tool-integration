"""SQLite adapter for the MCP lab.

The adapter validates every identifier against the live database schema before
building any SQL string, then executes the actual statement with bound
parameters. This keeps the tool surface safe from SQL injection while still
letting MCP clients pass dynamic table, column, and filter input.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping


ALLOWED_OPERATORS: dict[str, str] = {
    "eq": "=",
    "neq": "!=",
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
    "like": "LIKE",
    "in": "IN",
}

ALLOWED_METRICS: dict[str, str] = {
    "count": "COUNT",
    "avg": "AVG",
    "sum": "SUM",
    "min": "MIN",
    "max": "MAX",
}

MAX_LIMIT = 500


class ValidationError(Exception):
    """Raised when a request cannot be safely executed."""


class SQLiteAdapter:
    """Thin wrapper around sqlite3 with schema-aware validation."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ schema

    def list_tables(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]

    def get_table_schema(self, table: str) -> list[dict[str, Any]]:
        self._require_table(table)
        with self._connect() as conn:
            rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        return [
            {
                "name": r["name"],
                "type": r["type"],
                "notnull": bool(r["notnull"]),
                "default": r["dflt_value"],
                "primary_key": bool(r["pk"]),
            }
            for r in rows
        ]

    def full_schema(self) -> dict[str, list[dict[str, Any]]]:
        return {t: self.get_table_schema(t) for t in self.list_tables()}

    # -------------------------------------------------------------- validation

    def _require_table(self, table: str) -> None:
        if not isinstance(table, str) or not table:
            raise ValidationError("table must be a non-empty string")
        if table not in self.list_tables():
            raise ValidationError(f"unknown table: {table}")

    def _column_names(self, table: str) -> set[str]:
        return {col["name"] for col in self.get_table_schema(table)}

    def _require_columns(self, table: str, columns: Iterable[str]) -> None:
        known = self._column_names(table)
        for col in columns:
            if not isinstance(col, str) or col not in known:
                raise ValidationError(f"unknown column for {table}: {col!r}")

    # ------------------------------------------------------------------ search

    def search(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: list[Mapping[str, Any]] | None = None,
        limit: int = 20,
        offset: int = 0,
        order_by: str | None = None,
        descending: bool = False,
    ) -> dict[str, Any]:
        self._require_table(table)

        if columns:
            self._require_columns(table, columns)
            select_cols = ", ".join(f'"{c}"' for c in columns)
        else:
            columns = sorted(self._column_names(table))
            select_cols = "*"

        where_sql, params = self._build_where(table, filters or [])

        order_sql = ""
        if order_by is not None:
            self._require_columns(table, [order_by])
            direction = "DESC" if descending else "ASC"
            order_sql = f' ORDER BY "{order_by}" {direction}'

        limit = self._normalize_limit(limit)
        offset = self._normalize_offset(offset)

        sql = (
            f'SELECT {select_cols} FROM "{table}"'
            f"{where_sql}{order_sql} LIMIT ? OFFSET ?"
        )
        params = [*params, limit, offset]

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return {
            "table": table,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "rows": [dict(r) for r in rows],
        }

    # ------------------------------------------------------------------ insert

    def insert(self, table: str, values: Mapping[str, Any]) -> dict[str, Any]:
        self._require_table(table)
        if not isinstance(values, Mapping) or not values:
            raise ValidationError("values must be a non-empty mapping")
        self._require_columns(table, values.keys())

        cols = list(values.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_sql = ", ".join(f'"{c}"' for c in cols)
        sql = f'INSERT INTO "{table}" ({col_sql}) VALUES ({placeholders})'

        with self._connect() as conn:
            cur = conn.execute(sql, [values[c] for c in cols])
            conn.commit()
            row_id = cur.lastrowid
            inserted = conn.execute(
                f'SELECT * FROM "{table}" WHERE rowid = ?', [row_id]
            ).fetchone()

        return {
            "table": table,
            "inserted_id": row_id,
            "row": dict(inserted) if inserted else dict(values),
        }

    # --------------------------------------------------------------- aggregate

    def aggregate(
        self,
        table: str,
        metric: str,
        column: str | None = None,
        filters: list[Mapping[str, Any]] | None = None,
        group_by: str | None = None,
    ) -> dict[str, Any]:
        self._require_table(table)
        metric_lower = (metric or "").lower()
        if metric_lower not in ALLOWED_METRICS:
            raise ValidationError(
                f"unsupported metric: {metric!r}. "
                f"allowed: {sorted(ALLOWED_METRICS)}"
            )
        func = ALLOWED_METRICS[metric_lower]

        if metric_lower == "count" and column is None:
            target = "*"
        else:
            if column is None:
                raise ValidationError(f"metric {metric_lower!r} requires a column")
            self._require_columns(table, [column])
            target = f'"{column}"'

        where_sql, params = self._build_where(table, filters or [])

        select_parts = [f"{func}({target}) AS value"]
        group_sql = ""
        if group_by is not None:
            self._require_columns(table, [group_by])
            select_parts.insert(0, f'"{group_by}" AS group_key')
            group_sql = f' GROUP BY "{group_by}"'

        sql = (
            f"SELECT {', '.join(select_parts)} "
            f'FROM "{table}"{where_sql}{group_sql}'
        )

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return {
            "table": table,
            "metric": metric_lower,
            "column": column,
            "group_by": group_by,
            "rows": [dict(r) for r in rows],
        }

    # ----------------------------------------------------------------- helpers

    def _build_where(
        self, table: str, filters: list[Mapping[str, Any]]
    ) -> tuple[str, list[Any]]:
        if not filters:
            return "", []
        if not isinstance(filters, list):
            raise ValidationError("filters must be a list of objects")

        clauses: list[str] = []
        params: list[Any] = []
        for f in filters:
            if not isinstance(f, Mapping):
                raise ValidationError("each filter must be an object")
            col = f.get("column")
            op = (f.get("op") or f.get("operator") or "eq")
            value = f.get("value")
            if col is None:
                raise ValidationError("filter is missing 'column'")
            self._require_columns(table, [col])

            op_key = str(op).lower()
            if op_key not in ALLOWED_OPERATORS:
                raise ValidationError(
                    f"unsupported operator: {op!r}. "
                    f"allowed: {sorted(ALLOWED_OPERATORS)}"
                )
            sql_op = ALLOWED_OPERATORS[op_key]

            if op_key == "in":
                if not isinstance(value, (list, tuple)) or not value:
                    raise ValidationError(
                        "'in' operator requires a non-empty list value"
                    )
                placeholders = ", ".join("?" for _ in value)
                clauses.append(f'"{col}" IN ({placeholders})')
                params.extend(value)
            else:
                clauses.append(f'"{col}" {sql_op} ?')
                params.append(value)

        return " WHERE " + " AND ".join(clauses), params

    @staticmethod
    def _normalize_limit(limit: Any) -> int:
        try:
            value = int(limit)
        except (TypeError, ValueError):
            raise ValidationError("limit must be an integer")
        if value <= 0:
            raise ValidationError("limit must be > 0")
        return min(value, MAX_LIMIT)

    @staticmethod
    def _normalize_offset(offset: Any) -> int:
        try:
            value = int(offset)
        except (TypeError, ValueError):
            raise ValidationError("offset must be an integer")
        if value < 0:
            raise ValidationError("offset must be >= 0")
        return value
