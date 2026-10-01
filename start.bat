@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] creating virtual environment...
  python -m venv .venv || goto :err
) else (
  echo [1/3] virtual environment ready
)

set PY=.venv\Scripts\python.exe

"%PY%" -c "import httpx, pystray, PIL, win32clipboard, comtypes" 2>nul || (
  echo [2/3] installing dependencies...
  "%PY%" -m pip install -q --upgrade pip
  "%PY%" -m pip install -q -r requirements.txt || goto :err
)

echo [3/3] starting WordGrab in the background...
start "WordGrab" /min "%PY%" -m wordgrab %*
exit /b 0

:err
echo Setup failed. Check Python and your network connection.
pause
exit /b 1