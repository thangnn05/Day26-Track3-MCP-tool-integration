# SQLite Lab MCP Server

A FastMCP server that exposes a small SQLite database (`students`, `courses`,
`enrollments`) through three MCP tools and two schema resources.

## Quick start

```bash
# 1. install
python -m venv .venv
.venv\Scripts\activate         # Windows PowerShell:  .\.venv\Scripts\Activate.ps1
pip install -r implementation/requirements.txt

# 2. seed the database
python implementation/init_db.py

# 3. run the end-to-end verification
python implementation/verify_server.py

# 4. run the unit tests
python -m pytest implementation/tests -v

# 5. launch the server for an MCP client (stdio)
python implementation/mcp_server.py
```

The database lives at `implementation/lab.db` by default. Override with the
`MCP_LAB_DB` environment variable.

## Tool surface

| Tool | Purpose | Notable args |
| --- | --- | --- |
| `search` | Read rows from a table | `table`, `filters[]`, `columns[]`, `limit`, `offset`, `order_by`, `descending` |
| `insert` | Insert one row | `table`, `values{}` |
| `aggregate` | `count` / `avg` / `sum` / `min` / `max` | `table`, `metric`, `column?`, `filters[]?`, `group_by?` |

Filter shape: `{"column": "<name>", "op": "<eq|neq|lt|lte|gt|gte|like|in>", "value": <any>}`.
For `in`, `value` must be a non-empty list.

Every tool returns `{"ok": true, ...payload}` on success or
`{"ok": false, "error": "<reason>"}` on a validation failure. The server never
builds SQL by concatenating user input: identifiers are checked against the
live schema (`PRAGMA table_info`) and values flow through bound parameters.

## Resources

| URI | Description |
| --- | --- |
| `schema://database` | Full schema for every non-internal table as JSON |
| `schema://table/{table_name}` | Schema for one table as JSON |

## Example calls

```jsonc
// students in cohort A1, sorted by score desc, top 5
{ "tool": "search",
  "arguments": {
    "table": "students",
    "filters": [{"column": "cohort", "op": "eq", "value": "A1"}],
    "order_by": "score", "descending": true, "limit": 5 } }

// insert a student
{ "tool": "insert",
  "arguments": {
    "table": "students",
    "values": {"name": "Jordan", "cohort": "A1",
               "email": "jordan@example.com", "score": 82.0 } } }

// average score by cohort
{ "tool": "aggregate",
  "arguments": {
    "table": "students", "metric": "avg",
    "column": "score", "group_by": "cohort" } }
```

## Testing

- `python implementation/verify_server.py` — drives the live FastMCP server
  through an in-process MCP client and exercises tool discovery, resource
  discovery, valid calls, and error paths. Exits non-zero on failure.
- `python -m pytest implementation/tests -v` — unit tests for the adapter and
  the MCP wire surface against a throwaway database.

## MCP Inspector

```bash
# macOS/Linux
./implementation/start_inspector.sh

# Windows PowerShell
.\implementation\start_inspector.ps1
```

Or directly:

```bash
npx -y @modelcontextprotocol/inspector python implementation/mcp_server.py
```

Inspector should show:

- three tools: `search`, `insert`, `aggregate`
- one resource: `schema://database`
- one resource template: `schema://table/{table_name}`

## Client setup

The repo root contains a working `.mcp.json` for Claude Code. Replace
`${workspaceFolder}` with an absolute path if the client does not expand it.

### Claude Code

```json
{
  "mcpServers": {
    "sqlite-lab": {
      "type": "stdio",
      "command": "python",
      "args": ["/ABSOLUTE/PATH/TO/implementation/mcp_server.py"],
      "env": { "MCP_LAB_DB": "/ABSOLUTE/PATH/TO/implementation/lab.db" }
    }
  }
}
```

Then in Claude Code: `@sqlite-lab:schema://database` to view the schema, or
ask "use sqlite-lab to find students in cohort A1 ordered by score".

### Gemini CLI

```bash
gemini mcp add sqlite-lab /ABSOLUTE/PATH/TO/python /ABSOLUTE/PATH/TO/implementation/mcp_server.py \
    --description "SQLite lab FastMCP server" --timeout 10000
gemini mcp list
gemini --allowed-mcp-server-names sqlite-lab --yolo \
    -p "Use sqlite-lab to show the top 2 students by score."
```

### Codex

`~/.codex/config.toml`:

```toml
[mcp_servers.sqlite_lab]
command = "python"
args = ["/ABSOLUTE/PATH/TO/implementation/mcp_server.py"]
```

## Transports

By default the server runs over **stdio**. Set `MCP_TRANSPORT=http` (or `sse`)
plus `MCP_HOST` / `MCP_PORT` to expose an HTTP transport for remote tests.

## Layout

```
implementation/
  db.py             SQLite adapter + identifier/operator validation
  init_db.py        Schema + seed data, creates lab.db
  mcp_server.py     FastMCP entrypoint: tools + resources
  verify_server.py  End-to-end MCP verification (live in-process client)
  start_inspector.sh / .ps1
  requirements.txt
  tests/test_server.py
.mcp.json           Claude Code config pointing at this server
```
