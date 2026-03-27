# run_ingest.ps1
# Wrapper called by Windows Task Scheduler.
# Logs output to run_ingest.log next to this script.

$ScriptDir    = $PSScriptRoot
$ProjectRoot  = Split-Path $ScriptDir -Parent
$IngestScript = Join-Path $ScriptDir "hooprec_master_ingest.py"
$LogFile      = Join-Path $ScriptDir "run_ingest.log"

# Auto-detect Python from a .venv in the project root.
# Checks .venv first, then falls back to any .venv-* directory.
$Python = $null
foreach ($VenvName in @(".venv", ".venv-1", ".venv-2")) {
    $Candidate = Join-Path $ProjectRoot "$VenvName\Scripts\python.exe"
    if (Test-Path $Candidate) {
        $Python = $Candidate
        break
    }
}
if (-not $Python) {
    $Python = "python"  # fall back to PATH python if no venv found
}

# Force UTF-8 output so crawl4ai's Unicode progress chars don't crash on
# Windows cp1252 encoding when writing to a log file.
$env:PYTHONUTF8 = '1'

# Disable rich/crawl4ai fancy terminal output — prevents binary ANSI escape
# codes and null bytes from corrupting the log file.
$env:NO_COLOR = '1'

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $LogFile "[$timestamp] Starting ingestion..."

& $Python $IngestScript 2>&1 | Tee-Object -Append -FilePath $LogFile

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $LogFile "[$timestamp] Done. Exit code: $LASTEXITCODE`n"
