# Adsum launcher for Windows.
#
# Runs uvicorn in a restart loop and appends output to server.log. Intended to
# be driven by Task Scheduler (see docs/DEPLOY.md), which starts it at boot.
# PowerShell 5.1 compatible.
#
# Python is resolved in this order:
#   1. PYTHON=... in .env           (explicit, wins; use this on a machine
#                                    where `python` on PATH is the wrong one)
#   2. .venv\Scripts\python.exe      (the project virtualenv)
#   3. python / python3 on PATH
#
# Option 1 matters: on Windows `python` often resolves to a Store stub or an
# unrelated virtualenv that lacks this app's dependencies, and the service
# would then fail silently at boot.

$ErrorActionPreference = 'Continue'
Set-Location -Path $PSScriptRoot
$env:PATHEXT = '.COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC;.CPL'

$log = Join-Path $PSScriptRoot 'server.log'

# --- read PYTHON / HOST / PORT from .env, if present -------------------------
$bindHost = '0.0.0.0'
$port = '8000'
$pythonFromEnv = $null
$envFile = Join-Path $PSScriptRoot '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*HOST\s*=\s*(.+?)\s*$')   { $bindHost = $Matches[1] }
        if ($line -match '^\s*PORT\s*=\s*(.+?)\s*$')   { $port = $Matches[1] }
        if ($line -match '^\s*PYTHON\s*=\s*(.+?)\s*$') { $pythonFromEnv = $Matches[1] }
    }
}

# --- resolve the interpreter -------------------------------------------------
$python = $null
if ($pythonFromEnv -and (Test-Path $pythonFromEnv)) {
    $python = $pythonFromEnv
}
if (-not $python) {
    $venv = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (Test-Path $venv) { $python = $venv }
}
if (-not $python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) { $found = Get-Command python3 -ErrorAction SilentlyContinue }
    if ($found) { $python = $found.Source }
}
if (-not $python) {
    Add-Content -Path $log -Value "[$(Get-Date -Format o)] no interpreter found. Set PYTHON=<full path> in .env" -Encoding UTF8
    exit 1
}

# Fail loudly now rather than in a restart loop if the deps are missing.
& $python -c "import fastapi, uvicorn, sqlalchemy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $log -Value "[$(Get-Date -Format o)] $python cannot import the app dependencies. Set PYTHON=<full path> in .env, or create a .venv and pip install -r requirements.txt" -Encoding UTF8
    exit 1
}

Add-Content -Path $log -Value "[$(Get-Date -Format o)] launcher starting with $python" -Encoding UTF8

while ($true) {
    Add-Content -Path $log -Value "[$(Get-Date -Format o)] starting uvicorn on ${bindHost}:${port}" -Encoding UTF8
    & $python -m uvicorn app.main:app --host $bindHost --port $port 2>&1 |
        Add-Content -Path $log -Encoding UTF8
    Add-Content -Path $log -Value "[$(Get-Date -Format o)] uvicorn exited; restarting in 10s" -Encoding UTF8
    Start-Sleep -Seconds 10
}
