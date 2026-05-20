#!/usr/bin/env bash
# Launch MCP Inspector against this server.
# Requires Node/npx. Uses a project-local npm cache to avoid global perms.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python)}"

if [[ -z "$PYTHON_BIN" ]]; then
    echo "python interpreter not found; set PYTHON_BIN=/path/to/python" >&2
    exit 1
fi

mkdir -p "$HERE/.npm-cache"
NPM_CONFIG_CACHE="$HERE/.npm-cache" \
    npx -y @modelcontextprotocol/inspector \
        "$PYTHON_BIN" "$HERE/mcp_server.py"
