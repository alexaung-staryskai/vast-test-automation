@echo off
rem Stop VAST Test Bench (Windows). Double-click it, or:
rem   stop.bat               stop every instance started with run.bat
rem   stop.bat --port 8081   stop only the one on that port
setlocal
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10 or newer was not found.
  pause
  exit /b 1
)
%PY% start.py --stop %*
echo.
pause
