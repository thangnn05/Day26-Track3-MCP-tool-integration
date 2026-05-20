"""Unit tests for the SQLite adapter and the FastMCP server surface."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

IMPL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(IMPL_DIR))

from db import SQLiteAdapter, ValidationError  # noqa: E402
from init_db import create_database  # noqa: E402


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    create_database(path)
    return path


@pytest.fixture()
def adapter(db_path: Path) -> SQLiteAdapter:
    return SQLiteAdapter(db_path)


# ------------------------------------------------------------------ adapter

class TestSchema:
    def test_list_tables(self, adapter: SQLiteAdapter) -> None:
        assert set(adapter.list_tables()) == {"students", "courses", "enrollments"}

    def test_table_schema_shape(self, adapter: SQLiteAdapter) -> None:
        cols = {c["name"]: c for c in adapter.get_table_schema("students")}
        assert {"id", "name", "cohort", "email", "score"} <= cols.keys()
        assert cols["id"]["primary_key"] is True

    def test_full_schema(self, adapter: SQLiteAdapter) -> None:
        schema = adapter.full_schema()
        assert "students" in schema and "courses" in schema


class TestSearch:
    def test_basic_filter(self, adapter: SQLiteAdapter) -> None:
        result = adapter.search(
            "students",
            filters=[{"column": "cohort", "op": "eq", "value": "A1"}],
        )
        assert result["count"] == 3
        assert all(r["cohort"] == "A1" for r in result["rows"])

    def test_order_and_limit(self, adapter: SQLiteAdapter) -> None:
        result = adapter.search(
            "students", order_by="score", descending=True, limit=2
        )
        scores = [r["score"] for r in result["rows"]]
        assert scores == sorted(scores, reverse=True)
        assert len(scores) == 2

    def test_pagination_offset(self, adapter: SQLiteAdapter) -> None:
        first = adapter.search("students", order_by="id", limit=2, offset=0)
        second = adapter.search("students", order_by="id", limit=2, offset=2)
        assert [r["id"] for r in first["rows"]] != [r["id"] for r in second["rows"]]

    def test_in_operator(self, adapter: SQLiteAdapter) -> None:
        result = adapter.search(
            "students",
            filters=[{"column": "cohort", "op": "in", "value": ["A1", "C3"]}],
        )
        assert {r["cohort"] for r in result["rows"]} == {"A1", "C3"}

    def test_like_operator(self, adapter: SQLiteAdapter) -> None:
        result = adapter.search(
            "students",
            filters=[{"column": "email", "op": "like", "value": "%example.com"}],
        )
        assert result["count"] >= 6

    def test_select_columns(self, adapter: SQLiteAdapter) -> None:
        result = adapter.search("students", columns=["name", "cohort"], limit=1)
        assert set(result["rows"][0].keys()) == {"name", "cohort"}

    def test_unknown_table(self, adapter: SQLiteAdapter) -> None:
        with pytest.raises(ValidationError, match="unknown table"):
            adapter.search("ghosts")

    def test_unknown_column(self, adapter: SQLiteAdapter) -> None:
        with pytest.raises(ValidationError, match="unknown column"):
            adapter.search("students", columns=["nope"])

    def test_bad_operator(self, adapter: SQLiteAdapter) -> None:
        with pytest.raises(ValidationError, match="unsupported operator"):
            adapter.search(
                "students",
                filters=[{"column": "cohort", "op": "regex", "value": ".*"}],
            )

    def test_bad_limit(self, adapter: SQLiteAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.search("students", limit=0)


class TestInsert:
    def test_insert_returns_row(self, adapter: SQLiteAdapter) -> None:
        result = adapter.insert(
            "students",
            {"name": "Zed", "cohort": "Z9", "email": "zed@example.com", "score": 70},
        )
        assert result["row"]["name"] == "Zed"
        assert isinstance(result["inserted_id"], int)

    def test_empty_values_rejected(self, adapter: SQLiteAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.insert("students", {})

    def test_unknown_column_rejected(self, adapter: SQLiteAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.insert("students", {"name": "x", "ghost": 1})

    def test_unknown_table_rejected(self, adapter: SQLiteAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.insert("ghosts", {"x": 1})


class TestAggregate:
    def test_count(self, adapter: SQLiteAdapter) -> None:
        result = adapter.aggregate("students", "count")
        assert result["rows"][0]["value"] == 6

    def test_avg_by_cohort(self, adapter: SQLiteAdapter) -> None:
        result = adapter.aggregate(
            "students", "avg", column="score", group_by="cohort"
        )
        groups = {row["group_key"]: row["value"] for row in result["rows"]}
        assert "A1" in groups and "B2" in groups
        assert groups["A1"] == pytest.approx((91.5 + 78.0 + 84.2) / 3, rel=1e-3)

    def test_min_max_sum(self, adapter: SQLiteAdapter) -> None:
        assert adapter.aggregate("students", "min", column="score")["rows"][0]["value"] == 66.0
        assert adapter.aggregate("students", "max", column="score")["rows"][0]["value"] == 95.0
        sum_row = adapter.aggregate("students", "sum", column="score")["rows"][0]
        assert sum_row["value"] == pytest.approx(91.5 + 78.0 + 84.2 + 66.0 + 72.5 + 95.0)

    def test_bad_metric(self, adapter: SQLiteAdapter) -> None:
        with pytest.raises(ValidationError, match="unsupported metric"):
            adapter.aggregate("students", "median", column="score")

    def test_avg_requires_column(self, adapter: SQLiteAdapter) -> None:
        with pytest.raises(ValidationError):
            adapter.aggregate("students", "avg")


# ------------------------------------------------------------------ mcp wire

@pytest.fixture()
def mcp_instance(monkeypatch, tmp_path: Path):
    """Spin up the mcp_server module against a fresh tmp database."""
    db = tmp_path / "wire.db"
    create_database(db)
    monkeypatch.setenv("MCP_LAB_DB", str(db))
    # Re-import the server so it picks up the env var.
    for mod in ("mcp_server",):
        if mod in sys.modules:
            del sys.modules[mod]
    import mcp_server  # noqa: WPS433

    return mcp_server.mcp


def _read_resource_text(blocks) -> str:
    return "".join(getattr(b, "text", "") or "" for b in blocks)


class TestMCPWire:
    def test_tools_discoverable(self, mcp_instance) -> None:
        from fastmcp import Client

        async def go() -> set[str]:
            async with Client(mcp_instance) as c:
                tools = await c.list_tools()
                return {t.name for t in tools}

        names = asyncio.run(go())
        assert {"search", "insert", "aggregate"} <= names

    def test_resources_discoverable(self, mcp_instance) -> None:
        from fastmcp import Client

        async def go() -> tuple[set[str], set[str]]:
            async with Client(mcp_instance) as c:
                res = {str(r.uri) for r in await c.list_resources()}
                templates = {t.uriTemplate for t in await c.list_resource_templates()}
                return res, templates

        resources, templates = asyncio.run(go())
        assert "schema://database" in resources
        assert any("schema://table/" in t for t in templates)

    def test_search_call_via_mcp(self, mcp_instance) -> None:
        from fastmcp import Client

        async def go():
            async with Client(mcp_instance) as c:
                r = await c.call_tool(
                    "search",
                    {
                        "table": "students",
                        "filters": [{"column": "cohort", "op": "eq", "value": "A1"}],
                    },
                )
                text = "".join(getattr(b, "text", "") or "" for b in r.content)
                return json.loads(text)

        payload = asyncio.run(go())
        assert payload["ok"] is True
        assert payload["count"] == 3

    def test_invalid_call_returns_error_payload(self, mcp_instance) -> None:
        from fastmcp import Client

        async def go():
            async with Client(mcp_instance) as c:
                r = await c.call_tool("search", {"table": "ghosts"})
                text = "".join(getattr(b, "text", "") or "" for b in r.content)
                return json.loads(text)

        payload = asyncio.run(go())
        assert payload["ok"] is False
        assert "unknown table" in payload["error"]

    def test_schema_resource_via_mcp(self, mcp_instance) -> None:
        from fastmcp import Client

        async def go():
            async with Client(mcp_instance) as c:
                blocks = await c.read_resource("schema://database")
                return _read_resource_text(blocks)

        payload = json.loads(asyncio.run(go()))
        assert "students" in payload["tables"]
