$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "SvcWatchServer"

$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$scriptDir\run_server_hidden.vbs`""
$user = "$env:COMPUTERNAME\$env:USERNAME"
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
# 가장 높은 수준의 권한: 로그인할 때 UAC 확인 없이 관리자 권한으로 실행된다(방화벽 적용, SYSTEM 작업 제어용).
$Principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings `
    -Description "svc.watch 로컬 서비스 모니터 자동 시작 (로그인 시)" -Force | Out-Null

Write-Host "등록 완료. 다음 로그인부터 관리자 권한으로 대시보드 서버가 자동 시작됩니다."
Write-Host "지금 바로 켜려면 service_restart.bat 을 실행하세요."
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State, @{n='RunLevel';e={$_.Principal.RunLevel}}
