@echo off
:: Flowsurface Monitor — arranca MongoDB (si no está) y el monitor headless.
:: El monitor corre hasta que se cierre la ventana o se mate el proceso.

set REPO=c:\Users\inkam\Documents\flow-surface\flowsurface

:: ── MongoDB ──────────────────────────────────────────────────────────────────
tasklist /FI "IMAGENAME eq mongod.exe" 2>NUL | find /I "mongod.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    echo [monitor] Starting MongoDB on port 27018...
    start "" /B cmd /c "%REPO%\start-mongo.bat"
    :: Give MongoDB 5 seconds to initialize
    timeout /t 5 /nobreak >NUL
) else (
    echo [monitor] MongoDB already running.
)

:: ── Monitor binary ───────────────────────────────────────────────────────────
set MONGODB_URI=mongodb://localhost:27018
set MONGODB_DB=flowsurface
set SYMBOL=BTCUSDT
set TIMEFRAME_MIN=5
set RUST_BACKTRACE=1

echo [monitor] Starting flowsurface monitor...
"%REPO%\target\release\monitor.exe"
