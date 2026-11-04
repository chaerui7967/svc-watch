$taskName = "SvcWatchServer"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "자동 시작 등록을 해제했습니다."
    Write-Host "(지금 실행 중인 서버는 그대로 유지됩니다 - 끄려면 stop_server.bat 실행)"
} else {
    Write-Host "등록된 자동 시작 작업이 없습니다."
}
