@echo off
rem svc.watch - start with normal privileges (monitoring only; service control and firewall need admin)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo [setup] creating virtual environment...
  python -m venv .venv || goto :error
  .venv\Scripts\python -m pip install -q -r requirements.txt || goto :error
)
.venv\Scripts\python -m backend
goto :eof
:error
echo Setup failed. Make sure Python 3.10+ is installed and on PATH.
pause
