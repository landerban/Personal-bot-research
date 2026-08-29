@echo off
REM ============================================================================
REM  xsmom paper bot - STOP       (Stage 14 B.2.6)
REM
REM  Asks the supervisor to shut down CLEANLY: it finishes or safely abandons
REM  the in-flight step, writes a final status.json and releases its lock.
REM  Recovery on the next start goes through reconcile, which is the tested
REM  path - so stopping is safe and does NOT reset the 28-day clock.
REM
REM  It asks with a stop FILE rather than a signal: on Windows `taskkill`
REM  without /f only posts WM_CLOSE to GUI windows, so a console supervisor
REM  never sees it and gets force-killed instead - skipping clean shutdown and
REM  leaving a stale lock behind. Force is kept only as a last resort below.
REM ============================================================================
setlocal EnableDelayedExpansion

set "STATE=C:\Stock\live\state"
set "LOCK=%STATE%\supervisor.lock"
set "STOPFILE=%STATE%\stop"

if not exist "%LOCK%" (
    echo   No lock file - the bot does not appear to be running.
    pause
    exit /b 0
)

for /f "tokens=2 delims=:," %%p in ('findstr /C:"\"pid\"" "%LOCK%"') do set "PID=%%p"
set "PID=%PID: =%"

echo   Asking supervisor pid %PID% to stop cleanly...
echo stop > "%STOPFILE%"

REM Give it up to ~20 seconds to notice (it checks once a second) and finish.
set /a TRIES=0
:wait
REM `ping` rather than `timeout`: timeout aborts when stdin is redirected,
REM which happens whenever this is run from a scheduler or another script.
ping -n 3 127.0.0.1 >nul
set /a TRIES+=1
tasklist /fi "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto stopped
if %TRIES% LSS 10 goto wait

echo   Still running after 20s - forcing.
del /f /q "%STOPFILE%" >nul 2>&1
taskkill /pid %PID% /f >nul 2>&1
echo   Forced. The next start will reclaim the stale lock and reconcile.
pause
exit /b 0

:stopped
del /f /q "%STOPFILE%" >nul 2>&1
if exist "%LOCK%" (
    echo   Stopped, but the lock file is still present.
    echo   The next start will reclaim it and reconcile - that is safe.
) else (
    echo   Stopped cleanly. Lock released, final status written.
)
echo   Restarts are safe: the clock is paused, never reset.
pause
exit /b 0
