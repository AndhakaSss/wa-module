@echo off
echo Stopping WhatsApp bridge on port 3001...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :3001 ^| findstr LISTENING') do (
    echo Killing bridge PID %%a
    taskkill /PID %%a /F >nul 2>&1
)

echo Stopping Puppeteer Chrome processes...
powershell -NoProfile -Command "Get-Process chrome -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*puppeteer*' -or $_.Path -like '*chrome-win64*' } | Stop-Process -Force -ErrorAction SilentlyContinue; Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*wa-business-hub*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

ping 127.0.0.1 -n 3 >nul
echo Done.
