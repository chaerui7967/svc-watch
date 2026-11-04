@echo off
rem svc.watch - start as administrator (needed to start/stop Windows services and apply firewall rules)
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
call "%~dp0run.bat"
