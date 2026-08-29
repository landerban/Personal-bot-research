@echo off
REM ============================================================================
REM  xsmom paper bot - START      (Stage 14 B.2.3)
REM
REM  Double-click this. It activates the venv, starts the supervisor
REM  (dashboard + daily cycle), and logs to logs\xsmom.log (rotating).
REM
REM  If the bot is already running, the single-instance lock refuses and this
REM  window says so. That is deliberate: two supervisors would place the same
REM  orders twice.
REM ============================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "REPO=C:\Stock"
set "APP=%~dp0"
set "VENV=%APP%.venv"
set "PYTHONPATH=%REPO%"
set "PYTHONIOENCODING=utf-8"

if not exist "%VENV%\Scripts\python.exe" (
    echo  [FAIL] No virtual environment. Run install.bat first.
    pause
    exit /b 1
)

REM ---- credentials: loaded from OUTSIDE the repo, never stored here --------
set "ENVFILE=%USERPROFILE%\.binance_testnet.env"
if not exist "%ENVFILE%" (
    echo  [WARN] No credentials file at %ENVFILE%
    echo         The supervisor will exit with code 3. See RUNBOOK.md.
) else (
    for /f "usebackq tokens=1,* delims==" %%a in ("%ENVFILE%") do (
        set "K=%%a"
        if not "!K!"=="" if not "!K:~0,1!"=="#" set "!K!=%%b"
    )
)

echo.
echo   xsmom paper bot starting...
echo   dashboard : http://127.0.0.1:8787
echo   logs      : %APP%logs\xsmom.log
echo   stop      : close this window, or run stop_bot.bat
echo.

"%VENV%\Scripts\python.exe" -m xsmom --port 8787 --log-dir "%APP%logs"
set "RC=%ERRORLEVEL%"

if "%RC%"=="2" (
    echo.
    echo   ALREADY RUNNING - this launch was refused on purpose.
    echo   Two supervisors would place the same orders twice.
)
if "%RC%"=="3" (
    echo.
    echo   CONFIG ERROR - most likely missing testnet keys.
    echo   Expected: %USERPROFILE%\.binance_testnet.env
    echo   See RUNBOOK.md.
)

echo.
echo   exited with code %RC%
pause
exit /b %RC%
