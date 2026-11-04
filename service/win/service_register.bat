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

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0service_register.ps1"
pause
