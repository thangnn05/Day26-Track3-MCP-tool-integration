# Launch MCP Inspector against this server on Windows.
# Requires Node.js / npx on PATH.

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { (Get-Command python).Source }

if (-not $PythonBin) {
    Write-Error "python interpreter not found; set `$env:PYTHON_BIN before running."
}

$CacheDir = Join-Path $Here ".npm-cache"
if (-not (Test-Path $CacheDir)) {
    New-Item -ItemType Directory -Path $CacheDir | Out-Null
}

$env:NPM_CONFIG_CACHE = $CacheDir
& npx -y "@modelcontextprotocol/inspector" $PythonBin (Join-Path $Here "mcp_server.py")
