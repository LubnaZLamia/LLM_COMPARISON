@echo off
REM ============================================================
REM  LLM Health Assistant - one-click start (Windows)
REM  First run: creates a virtual environment, installs packages,
REM  asks for your OpenRouter key, and sets up the database.
REM ============================================================
cd /d "%~dp0"
title LLM Health Assistant

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo During install, tick "Add python.exe to PATH". Then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv || goto :error
)

set "VPY=.venv\Scripts\python.exe"

echo Installing / checking packages...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :error

"%VPY%" scripts\setup_env.py || goto :error
"%VPY%" manage.py migrate --noinput -v 0 || goto :error
"%VPY%" manage.py seed_dataset || goto :error
"%VPY%" manage.py seed_models --repair || goto :error
if exist "data\questions.csv" "%VPY%" manage.py import_datasets --questions data\questions.csv --responses data\responses.csv || goto :error
if exist "exports\runs.csv" "%VPY%" manage.py import_exports || goto :error
"%VPY%" manage.py ensure_admin || goto :error

echo.
echo  App running at http://127.0.0.1:8000   (close this window to stop)
echo.
start "" http://127.0.0.1:8000
"%VPY%" manage.py runserver 127.0.0.1:8000
goto :eof

:error
echo.
echo Something went wrong. Scroll up to see the error message.
pause
exit /b 1
