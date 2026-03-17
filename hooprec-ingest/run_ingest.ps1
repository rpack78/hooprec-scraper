# run_ingest.ps1
# Wrapper called by Windows Task Scheduler.
# Logs output to run_ingest.log next to this script.

<<<<<<< HEAD
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python     = "$ScriptDir\..\..\.venv-1\Scripts\python.exe" | Resolve-Path -ErrorAction Stop
$IngestScript = "$ScriptDir\hooprec_master_ingest.py"
$LogFile    = "$ScriptDir\run_ingest.log"
=======
$ScriptDir    = "D:\dev\projects\hooprec-scraper\hooprec-ingest"
$Python       = "D:\dev\projects\hooprec-scraper\.venv-1\Scripts\python.exe"
$IngestScript = "$ScriptDir\hooprec_master_ingest.py"
$LogFile      = "$ScriptDir\run_ingest.log"
>>>>>>> f2793fc (feat: Added schedule to run nightly)

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $LogFile "[$timestamp] Starting ingestion..."

& $Python $IngestScript 2>&1 | Tee-Object -Append -FilePath $LogFile

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $LogFile "[$timestamp] Done. Exit code: $LASTEXITCODE`n"
