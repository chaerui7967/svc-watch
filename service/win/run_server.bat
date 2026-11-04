@echo off
REM 이 파일은 CP949(ANSI 한국어)로 저장할 것. UTF-8로 저장하면 cmd가 파일을
REM 잘못 읽어 줄 중간부터 실행하는 오류가 난다. (.gitattributes가 체크아웃 시 CP949로 바꿔 준다)

REM 서버가 죽으면 시각/종료 코드를 server.log에 남기고 10초 뒤 다시 띄운다.
REM - 종료 코드 1: 외부에서 강제 종료(taskkill /F 등), 0xC000.... : 파이썬 자체 크래시
REM - stop_server.bat / service_restart.bat은 끄기 전에 server.stop 파일을 만들어서
REM   "일부러 끈 것"임을 알린다 - 이 파일이 있으면 다시 띄우지 않고 빠진다.

cd /d "%~dp0..\.."

set PORT=8765
for /f "tokens=2 delims==" %%a in ('findstr /r "^PORT=" ".env" 2^>nul') do set PORT=%%a

set STOP_FLAG=%~dp0server.stop
set RESTARTS=0

REM 대시보드 설정: 포트를 넘겨주고, 자동 실행 때는 브라우저를 열지 않는다.
REM 파이썬 출력은 버퍼링 없이 바로 server.log에 쓴다.
set SVCWATCH_PORT=%PORT%
set SVCWATCH_NO_BROWSER=1
set PYTHONUNBUFFERED=1
REM bat이 쓰는 줄(CP949)과 섞여도 메모장에서 깨지지 않게 파이썬 출력도 CP949로 맞춘다.
set PYTHONIOENCODING=cp949:replace

if not exist ".venv\Scripts\python.exe" (
    echo [%date% %time%] .venv가 없습니다. 프로젝트 폴더의 run.bat을 한 번 실행해 설치하세요>> server.log
    exit /b 1
)

:loop
REM 다른 실행이 이미 이 포트로 서버를 띄워놨으면(중복 실행) 나는 조용히 빠진다.
REM (여기서 server.log에 남기려 해도 돌고 있는 서버가 파일을 붙잡고 있어서 쓸 수 없다)
netstat -ano | findstr /C:":%PORT% " | findstr "LISTENING" >nul
if not errorlevel 1 exit /b 0

echo [%date% %time%] 서버 시작 ^(포트 %PORT%^)>> server.log
".venv\Scripts\python.exe" -m backend >> server.log 2>&1
echo [%date% %time%] 서버 종료 ^(종료 코드 %errorlevel%^)>> server.log

if exist "%STOP_FLAG%" (
    del "%STOP_FLAG%" >nul 2>&1
    echo [%date% %time%] 정지 요청으로 끈 것이라 다시 띄우지 않습니다>> server.log
    exit /b 0
)

REM 계속 바로 죽는 상황(설정 오류 등)에서 무한히 재시작하며 로그만 불리지 않도록 상한을 둔다.
set /a RESTARTS+=1
if %RESTARTS% GTR 20 (
    echo [%date% %time%] 자동 재시작이 20번을 넘어 멈춥니다. server.log를 확인하세요>> server.log
    exit /b 1
)

echo [%date% %time%] 10초 뒤 자동으로 다시 띄웁니다 ^(%RESTARTS%번째^)>> server.log
ping -n 11 127.0.0.1 >nul
goto loop
