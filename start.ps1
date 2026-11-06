# Attendance Check-In launcher. Windows PowerShell 5.1 compatible. Restart loop; uvicorn output to server.log.
# Pattern copied from C:\EI\tunnel\start.ps1. Runs uvicorn directly (no --reload, no run.py dev mode).
$ErrorActionPreference = 'Continue'
Set-Location -Path $PSScriptRoot
$env:PATHEXT = '.COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC;.CPL'
$log = Join-Path $PSScriptRoot 'server.log'
while ($true) {
    Add-Content -Path $log -Value "[$(Get-Date -Format o)] starting uvicorn" -Encoding UTF8
    & 'C:\Users\mgrot\AppData\Local\Programs\Python\Python314\python.exe' -m uvicorn app.main:app --host 0.0.0.0 --port 8000 2>&1 | Add-Content -Path $log -Encoding UTF8
    Add-Content -Path $log -Value "[$(Get-Date -Format o)] uvicorn exited; restarting in 10s" -Encoding UTF8
    Start-Sleep -Seconds 10
}
