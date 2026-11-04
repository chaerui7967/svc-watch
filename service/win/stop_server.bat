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

set PORT=8765
for /f "tokens=2 delims==" %%a in ('findstr /r "^PORT=" "%~dp0..\..\.env" 2^>nul') do set PORT=%%a

REM run_server.bat은 서버가 죽으면 자동으로 다시 띄운다. 일부러 끄는 것임을 알리려고
REM 끄기 전에 server.stop 파일을 만든다(run_server.bat이 보고 지운다).
type nul > "%~dp0server.stop"

set FOUND=0
REM 블록 안의 echo에서 괄호는 ^로 escape해야 한다. 안 그러면 )가 블록을 닫아버림.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr "LISTENING"') do (
    echo 대시보드 서버^(PID %%p^) 종료 중...
    taskkill /PID %%p /F
    set FOUND=1
)

if %FOUND% == 0 (
    echo 실행 중인 대시보드 서버를 찾지 못했습니다. ^(포트 %PORT%^)
    REM 끌 서버가 없었으면 표시 파일을 남겨두지 않는다 - 남아 있으면 나중에 서버가
    REM 진짜로 죽었을 때 "일부러 끈 것"으로 오해해서 다시 안 띄운다.
    del "%~dp0server.stop" >nul 2>&1
)

pause
