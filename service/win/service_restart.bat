@echo off
REM 이 파일은 CP949(ANSI 한국어)로 저장할 것. UTF-8로 저장하면 cmd가 파일을
REM 잘못 읽어 줄 중간부터 실행하는 오류가 난다. (.gitattributes가 체크아웃 시 CP949로 바꿔 준다)

REM 자동 실행이 관리자 권한(가장 높은 수준)으로 돌기 때문에, 등록/해제와 서버 끄기도 관리자 권한이 필요하다.
REM 관리자 창이 아니면 UAC 확인 창을 띄워 이 파일을 관리자 권한으로 다시 연다.
net session >nul 2>&1
if errorlevel 1 (
    echo 관리자 권한이 필요합니다. 확인 창에서 [예]를 눌러 주세요.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo 대시보드 서버를 재시작합니다...

set PORT=8765
for /f "tokens=2 delims==" %%a in ('findstr /r "^PORT=" "%~dp0..\..\.env" 2^>nul') do set PORT=%%a

REM run_server.bat은 서버가 죽으면 자동으로 다시 띄운다 - 기존 실행이 알아서 되살리지
REM 않고 조용히 빠지도록, 끄기 전에 server.stop 파일로 "일부러 끈 것"임을 알린다.
set FOUND=0
REM 블록 안의 echo에서 괄호는 ^로 escape해야 한다. 안 그러면 )가 블록을 닫아버림.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr "LISTENING"') do (
    type nul > "%~dp0server.stop"
    echo 기존 서버^(PID %%p^) 종료 중...
    taskkill /PID %%p /F >nul 2>&1
    set FOUND=1
)

REM 기존 run_server.bat이 표시 파일을 보고 빠질 시간을 준다.
timeout /t 3 /nobreak >nul
if exist "%~dp0server.stop" del "%~dp0server.stop" >nul 2>&1

echo 서버를 다시 시작합니다...
REM 등록된 작업으로 띄워야 작업 설정(관리자 권한)대로 실행된다. 등록 전이면 vbs로 직접 띄운다.
schtasks /run /tn "SvcWatchServer" >nul 2>&1
if errorlevel 1 wscript.exe "%~dp0run_server_hidden.vbs"

REM 서버 부팅 완료 대기 (최대 60초, 2초 간격으로 확인하여 기동 즉시 완료 처리)
set STARTED=0
for /l %%i in (1,1,30) do (
    timeout /t 2 /nobreak >nul
    netstat -ano | findstr /C:":%PORT% " | findstr "LISTENING" >nul
    if not errorlevel 1 (
        set STARTED=1
        goto :server_ready
    )
)

:server_ready
if "%STARTED%"=="1" (
    echo 재시작 완료: 대시보드가 http://127.0.0.1:%PORT% 에서 실행 중입니다.
) else (
    echo 경고: 서버가 아직 안 켜진 것 같습니다. server.log를 확인해주세요.
)

pause
