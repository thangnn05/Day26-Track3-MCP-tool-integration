"""End-to-end verification of the MCP server.

Drives the live FastMCP server through an in-process client so the checks
exercise the real MCP wire protocol (tool discovery, tool calls, resource
reads) instead of bypassing it. Exits non-zero on any failure.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parent))

from init_db import create_database  # noqa: E402
from mcp_server import mcp  # noqa: E402


PASS = "PASS"
FAIL = "FAIL"


def _content_text(result) -> str:
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _structured(result) -> dict | None:
    payload = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if payload:
        return payload
    text = _content_text(result)
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return None


async def run() -> int:
    create_database()
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        marker = PASS if ok else FAIL
        print(f"[{marker}] {label}" + (f" :: {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    async with Client(mcp) as client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        check(
            "tool discovery",
            tool_names >= {"search", "insert", "aggregate"},
            detail=f"found={sorted(tool_names)}",
        )

        resources = await client.list_resources()
        resource_uris = {str(r.uri) for r in resources}
        templates = await client.list_resource_templates()
        template_uris = {t.uriTemplate for t in templates}
        check(
            "schema resource",
            "schema://database" in resource_uris,
            detail=f"resources={sorted(resource_uris)}",
        )
        check(
            "schema template",
            any("schema://table/" in u for u in template_uris),
            detail=f"templates={sorted(template_uris)}",
        )

        # --- search ---------------------------------------------------------
        result = await client.call_tool(
            "search",
            {
                "table": "students",
                "filters": [{"column": "cohort", "op": "eq", "value": "A1"}],
                "order_by": "score",
                "descending": True,
                "limit": 5,
            },
        )
        payload = _structured(result) or {}
        check(
            "search valid call",
            payload.get("ok") is True and len(payload.get("rows", [])) == 3,
            detail=f"rows={len(payload.get('rows', []))}",
        )

        result = await client.call_tool(
            "search", {"table": "nonexistent"}
        )
        payload = _structured(result) or {}
        check(
            "search rejects unknown table",
            payload.get("ok") is False and "unknown table" in payload.get("error", ""),
            detail=str(payload.get("error")),
        )

        result = await client.call_tool(
            "search",
            {
                "table": "students",
                "filters": [{"column": "cohort", "op": "regex", "value": ".*"}],
            },
        )
        payload = _structured(result) or {}
        check(
            "search rejects bad operator",
            payload.get("ok") is False
            and "unsupported operator" in payload.get("error", ""),
            detail=str(payload.get("error")),
        )

        # --- insert ---------------------------------------------------------
        result = await client.call_tool(
            "insert",
            {
                "table": "students",
                "values": {
                    "name": "Verify Bot",
                    "cohort": "Z9",
                    "email": "verify-bot@example.com",
                    "score": 88.0,
                },
            },
        )
        payload = _structured(result) or {}
        check(
            "insert valid call",
            payload.get("ok") is True and payload.get("row", {}).get("cohort") == "Z9",
            detail=f"inserted_id={payload.get('inserted_id')}",
        )

        result = await client.call_tool(
            "insert", {"table": "students", "values": {}}
        )
        payload = _structured(result) or {}
        check(
            "insert rejects empty values",
            payload.get("ok") is False,
            detail=str(payload.get("error")),
        )

        result = await client.call_tool(
            "insert",
            {"table": "students", "values": {"name": "X", "ghost_col": 1}},
        )
        payload = _structured(result) or {}
        check(
            "insert rejects unknown column",
            payload.get("ok") is False
            and "unknown column" in payload.get("error", ""),
            detail=str(payload.get("error")),
        )

        # --- aggregate ------------------------------------------------------
        result = await client.call_tool(
            "aggregate", {"table": "students", "metric": "count"}
        )
        payload = _structured(result) or {}
        rows = payload.get("rows", [])
        check(
            "aggregate count",
            payload.get("ok") is True and rows and rows[0].get("value") >= 6,
            detail=f"rows={rows}",
        )

        result = await client.call_tool(
            "aggregate",
            {
                "table": "students",
                "metric": "avg",
                "column": "score",
                "group_by": "cohort",
            },
        )
        payload = _structured(result) or {}
        check(
            "aggregate avg group_by",
            payload.get("ok") is True and len(payload.get("rows", [])) >= 2,
            detail=f"groups={len(payload.get('rows', []))}",
        )

        result = await client.call_tool(
            "aggregate", {"table": "students", "metric": "median", "column": "score"}
        )
        payload = _structured(result) or {}
        check(
            "aggregate rejects bad metric",
            payload.get("ok") is False
            and "unsupported metric" in payload.get("error", ""),
            detail=str(payload.get("error")),
        )

        # --- resources ------------------------------------------------------
        full = await client.read_resource("schema://database")
        full_text = "".join(getattr(b, "text", "") or "" for b in full)
        full_json = json.loads(full_text) if full_text else {}
        check(
            "read schema://database",
            "students" in full_json.get("tables", {}),
            detail=f"tables={sorted(full_json.get('tables', {}))}",
        )

        per_table = await client.read_resource("schema://table/students")
        per_table_text = "".join(getattr(b, "text", "") or "" for b in per_table)
        per_table_json = json.loads(per_table_text) if per_table_text else {}
        check(
            "read schema://table/students",
            any(c["name"] == "cohort" for c in per_table_json.get("columns", [])),
            detail=f"columns={[c['name'] for c in per_table_json.get('columns', [])]}",
        )

    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("\nAll verification checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
