@echo off
REM ============================================================================
REM  xsmom paper bot - STOP       (Stage 14 B.2.6)
REM
REM  Asks the supervisor to shut down cleanly: it finishes or aborts the
REM  in-flight step safely, writes a final status.json and releases the lock.
REM  Recovery on the next start goes through reconcile, which is the tested
REM  path - so stopping is safe and does NOT reset the 28-day clock.
REM ============================================================================
setlocal EnableDelayedExpansion
set "LOCK=C:\Stock\live\state\supervisor.lock"

if not exist "%LOCK%" (
    echo   No lock file - the bot does not appear to be running.
    pause
    exit /b 0
)

for /f "tokens=2 delims=:," %%p in ('findstr /C:"\"pid\"" "%LOCK%"') do set "PID=%%p"
set "PID=%PID: =%"

echo   Stopping supervisor pid %PID% ...
taskkill /pid %PID% >nul 2>&1
if errorlevel 1 (
    echo   Could not signal it gently; forcing.
    taskkill /pid %PID% /f >nul 2>&1
)

echo   Done. Restarts are safe: the clock is paused, never reset.
pause
exit /b 0
