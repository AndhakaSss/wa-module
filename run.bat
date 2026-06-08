@echo off
cd /d "%~dp0"

echo Checking Node.js...
where node >nul 2>&1
if errorlevel 1 (
    echo Node.js is required. Install from https://nodejs.org/
    pause
    exit /b 1
)

echo Checking WhatsApp bridge on port 3001...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:3001/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 (
    echo Bridge is already running - not starting a second copy.
    goto start_flask
)

echo Freeing port 3001...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :3001 ^| findstr LISTENING') do (
    taskkill /PID %%a /F >nul 2>&1
)
powershell -NoProfile -Command "Get-Process chrome -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*puppeteer*' } | Stop-Process -Force -ErrorAction SilentlyContinue" >nul 2>&1
ping 127.0.0.1 -n 3 >nul

echo Starting WhatsApp bridge on port 3001...
start "WhatsApp Bridge" cmd /k "cd /d "%~dp0whatsapp-bridge" && if not exist node_modules npm install && npm start"

echo Waiting for bridge to start...
timeout /t 5 /nobreak >nul

:start_flask
echo Starting Flask server on port 5000...
python server.py
