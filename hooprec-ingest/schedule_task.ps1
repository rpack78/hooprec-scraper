# schedule_task.ps1
# Run this ONCE (as Administrator) to register the scheduled task.
# It will run the ingestion script every day at 6:00 AM.
#
# Usage:
#   Right-click schedule_task.ps1 -> "Run with PowerShell" (as Admin)
# Or from an elevated terminal:
#   powershell -ExecutionPolicy Bypass -File schedule_task.ps1
#
# To remove the task later:
#   Unregister-ScheduledTask -TaskName "BasketballIngest" -Confirm:$false

$TaskName   = "BasketballIngest"
$ScriptPath = (Resolve-Path "$PSScriptRoot\run_ingest.ps1").Path
$Action     = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File `"$ScriptPath`""

# Daily at 6:00 AM. Change the time here if you prefer a different schedule.
$Trigger    = New-ScheduledTaskTrigger -Daily -At "06:00"

$Settings   = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `       # run at next opportunity if machine was off at trigger time
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action   $Action `
    -Trigger  $Trigger `
    -Settings $Settings `
    -RunLevel Highest `
    -Force

Write-Host ""
Write-Host "Task '$TaskName' registered. It will run daily at 06:00 AM."
Write-Host "To run it immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To view logs:          notepad '$PSScriptRoot\run_ingest.log'"
Write-Host "To remove the task:    Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
