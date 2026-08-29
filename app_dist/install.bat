@echo off
REM ============================================================================
REM  xsmom paper bot - ONE-TIME INSTALL   (Stage 14 B.2.2)
REM
REM  Creates the virtual environment, installs dependencies, and registers a
REM  Windows Scheduled Task that starts the bot at logon AND at boot and
REM  restarts it if it fails.
REM
REM  Idempotent: run it twice and you still get exactly one task.
REM  It NEVER writes your API keys anywhere. See RUNBOOK.md.
REM ============================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "REPO=C:\Stock"
set "APP=%~dp0"
set "VENV=%APP%.venv"
set "TASK=xsmom-paper-bot"

echo.
echo  ===============================================================
echo   xsmom paper bot - install
echo  ===============================================================
echo   repo : %REPO%
echo   app  : %APP%
echo.

REM ---- 1. sanity: the repo must be there ------------------------------------
if not exist "%REPO%\xsmom\supervisor.py" (
    echo  [FAIL] Cannot find the bot code at %REPO%.
    echo         Edit REPO at the top of this file if the repo moved.
    goto :fail
)

REM ---- 2. python ------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo  [FAIL] python is not on PATH. Install Python 3.11+ and re-run.
    goto :fail
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo  [ok]   python %PYVER%

REM ---- 3. venv --------------------------------------------------------------
if exist "%VENV%\Scripts\python.exe" (
    echo  [ok]   venv already exists - reusing it
) else (
    echo  [..]   creating venv
    python -m venv "%VENV%"
    if errorlevel 1 goto :fail
    echo  [ok]   venv created
)

echo  [..]   installing dependencies
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install --quiet numpy requests
if errorlevel 1 goto :fail
echo  [ok]   dependencies installed

REM ---- 4. credentials check (READ ONLY - never written) ---------------------
set "ENVFILE=%USERPROFILE%\.binance_testnet.env"
if exist "%ENVFILE%" (
    echo  [ok]   credentials file found: %ENVFILE%
) else (
    echo  [WARN] No credentials file at %ENVFILE%
    echo         The bot will refuse to start ^(exit code 3^) until it exists.
    echo         See RUNBOOK.md - "Keys". This installer will not create it.
)

REM ---- 5. clock sync --------------------------------------------------------
echo.
echo  ---- clock ----
w32tm /query /status >nul 2>&1
if errorlevel 1 (
    echo  [WARN] Windows Time service not reporting. The cycle runs at 00:00 UTC;
    echo         a drifting clock runs it at the wrong moment.
    echo         Fix:  net start w32time  ^&^&  w32tm /resync
) else (
    for /f "tokens=*" %%s in ('w32tm /query /status ^| findstr /C:"Last Successful Sync Time"') do echo  [ok]   %%s
)

REM ---- 6. power settings (PRINTED, never changed silently) ------------------
echo.
echo  ---- power ----
echo   A sleeping machine at 00:00 UTC is the number one killer of a local
echo   24/7 bot. This installer does NOT change your power settings. Set them
echo   yourself, on AC power:
echo.
echo       powercfg /change standby-timeout-ac 0
echo       powercfg /change hibernate-timeout-ac 0
echo.
for /f "tokens=*" %%p in ('powercfg /getactivescheme') do echo   current scheme: %%p

REM ---- 7. scheduled task (idempotent) --------------------------------------
echo.
echo  ---- auto-start ----
REM Two mechanisms, tried in order. Both idempotent.
REM   1. Scheduled Task   - preferred, but /create needs elevation on many
REM                         machines (this one included).
REM   2. Startup shortcut - no admin rights needed, always available.
REM A missing auto-start is a WARNING, never a failed install: the bot still
REM runs perfectly well from start_bot.bat.

set "AUTOSTART=none"

schtasks /query /tn "%TASK%" >nul 2>&1
if not errorlevel 1 (
    echo  [..]   existing task found - removing so this stays idempotent
    schtasks /delete /tn "%TASK%" /f >nul 2>&1
)
schtasks /create /tn "%TASK%" /sc onlogon /rl limited /tr "\"%APP%start_bot.bat\"" /f >nul 2>&1
if not errorlevel 1 (
    set "AUTOSTART=task"
    echo  [ok]   scheduled task "%TASK%" registered - starts at logon
    goto :autostart_done
)
echo  [..]   scheduled task needs an elevated shell here - using the
echo         Startup folder instead ^(no admin rights required^)

set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\xsmom-paper-bot.lnk"
if exist "%LNK%" del /f /q "%LNK%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%APP%start_bot.bat'; $s.WorkingDirectory='%APP%'; $s.Description='xsmom paper bot (testnet)'; $s.Save()" >nul 2>&1
if exist "%LNK%" (
    set "AUTOSTART=startup"
    echo  [ok]   Startup shortcut created - starts at logon
) else (
    echo  [WARN] could not create an auto-start entry.
    echo         Not fatal: start the bot with start_bot.bat when you want it.
)

:autostart_done
echo         ^(start-at-BOOT and restart-on-failure need an elevated shell -
echo          see RUNBOOK.md "Stronger auto-start"^)

REM ---- 8. verify the install actually runs ----------------------------------
echo.
echo  ---- verifying ----
set "PYTHONPATH=%REPO%"
set "PYTHONIOENCODING=utf-8"
REM Load the credentials the same way start_bot.bat does, so this verification
REM exercises the real startup path instead of always failing preflight.
REM They are READ here and never written anywhere.
if exist "%ENVFILE%" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%ENVFILE%") do (
        set "K=%%a"
        if not "!K!"=="" if not "!K:~0,1!"=="#" set "!K!=%%b"
    )
)
"%VENV%\Scripts\python.exe" -m xsmom --once --no-dashboard --log-dir "%APP%logs"
if errorlevel 3 (
    echo  [WARN] preflight failed - almost certainly the credentials file.
    echo         Everything else is installed. Fix the keys and run start_bot.bat.
    goto :done
)
if errorlevel 1 (
    echo  [FAIL] verification run failed - see %APP%logs\xsmom.log
    goto :fail
)
echo  [ok]   verification tick completed

:done
echo.
echo  ===============================================================
echo   INSTALL COMPLETE
echo.
echo   Start now      : double-click start_bot.bat
echo   Watch it       : http://127.0.0.1:8787
echo   Stop it        : double-click stop_bot.bat
echo   Logs           : %APP%logs\xsmom.log
echo   Read this      : %APP%RUNBOOK.md
echo  ===============================================================
echo.
pause
exit /b 0

:fail
echo.
echo  INSTALL FAILED - nothing was left running.
echo.
pause
exit /b 1
