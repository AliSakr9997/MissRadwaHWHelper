@echo off
setlocal
cd /d "%~dp0"

set "APP_URL=http://127.0.0.1:8000"

rem Stop duplicate MissRadwaHWHelper servers on old preview ports.
rem Only matching app.server processes on ports 8790/8765 are targeted.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ports=8790,8765; foreach($port in $ports){Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {$proc=Get-CimInstance Win32_Process -Filter ('ProcessId='+$_.OwningProcess) -ErrorAction SilentlyContinue; if($proc -and $proc.CommandLine -match '(-m\s+app\.server|app\\server\.py)'){Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue}}}"

if not exist "config\client_secret.json" (
    echo Google Classroom is not configured yet.
    echo Add the Desktop OAuth JSON at config\client_secret.json before signing in.
    echo The app will still start so you can review offline features.
)

rem Wait for the server, then open the browser even if dependencies need installing.
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(120); while((Get-Date) -lt $deadline){try{if((Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -WarningAction SilentlyContinue).TcpTestSucceeded){Start-Process '%APP_URL%'; exit}}catch{}; Start-Sleep -Seconds 1}"

where py.exe >nul 2>&1
if not errorlevel 1 (
    py -c "import PIL, pypdf, googleapiclient, google_auth_oauthlib" >nul 2>&1
    if errorlevel 1 (
        echo Installing required packages...
        py -m pip install -r requirements.txt
        if errorlevel 1 (
            echo Dependency installation failed.
            pause
            goto :end
        )
    )
    py -m app.server
    goto :end
)

where python.exe >nul 2>&1
if not errorlevel 1 (
    python -c "import PIL, pypdf, googleapiclient, google_auth_oauthlib" >nul 2>&1
    if errorlevel 1 (
        echo Installing required packages...
        python -m pip install -r requirements.txt
        if errorlevel 1 (
            echo Dependency installation failed.
            pause
            goto :end
        )
    )
    python -m app.server
    goto :end
)

echo Python was not found. Install Python 3.10 or newer, then double-click this file again.
pause

:end
endlocal
