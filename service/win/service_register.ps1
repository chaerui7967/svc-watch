$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "SvcWatchServer"

$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$scriptDir\run_server_hidden.vbs`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\$env:USERNAME"
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "svc.watch 로컬 서비스 모니터 자동 시작 (로그인 시)" -Force | Out-Null

Write-Host "등록 완료. 다음 로그인부터 자동으로 대시보드 서버가 시작됩니다."
Write-Host "지금 바로 켜려면 service_restart.bat 을 실행하세요."
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
