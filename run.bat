@echo off
rem Start VAST Test Bench (Windows). Double-click it, or from cmd / PowerShell:
rem   run.bat                         pick or create a test site in the browser
rem   run.bat sites\acme-poc          open that test site directly
rem   run.bat --port 8081             another port (default: %PORT%, the site's app.port, or 8080)
rem   run.bat --no-browser            don't open the browser
rem Stop it with Ctrl+C in this window, or run stop.bat.
setlocal
cd /d "%~dp0"

rem the newest Python 3.10+: the "py" launcher first, then "python" (skips the Microsoft Store placeholder)
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo VAST Test Bench needs Python 3.10 or newer, and none was found.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  echo.
  pause
  exit /b 1
)

%PY% start.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
