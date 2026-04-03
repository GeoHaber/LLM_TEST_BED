@echo off
setlocal
title Zen LLM Compare

:: Global llama.cpp defaults (shared across repos)
if "%ZENAI_LLAMA_SERVER%"=="" set "ZENAI_LLAMA_SERVER=C:\Ai\_bin\llama-server.exe"
if "%SWARM_MODELS_DIR%"=="" set "SWARM_MODELS_DIR=C:\Ai\Models"
if "%PATH:C:\Ai\_bin;=%"=="%PATH%" set "PATH=C:\Ai\_bin;%PATH%"

set "ROOT=%~dp0"
set "PYEXE=%LocalAppData%\Microsoft\WindowsApps\python3.13.exe"
if exist "%PYEXE%" goto python_ready
where py >nul 2>&1
if not errorlevel 1 (
    set "PYEXE=py -3"
    goto python_ready
)
set "PYEXE=python"

:python_ready
echo Stopping any old servers on port 8123...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":8123 .*LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo Starting backend on port 8123...
start "Backend" /MIN cmd /c %PYEXE% "%ROOT%comparator_backend.py" 8123

echo Waiting for backend health check...
set "READY="
for /l %%i in (1,1,20) do (
    %PYEXE% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8123/__health', timeout=2).read()" >nul 2>&1 && (
        set "READY=1"
        goto backend_ready
    )
    timeout /t 1 /nobreak >nul
)

:backend_ready
if not defined READY (
    echo.
    echo Backend did not come up in time. Running it in this window so the error is visible...
    %PYEXE% "%ROOT%comparator_backend.py" 8123
    exit /b %errorlevel%
)

echo Opening browser...
start http://127.0.0.1:8123/

echo.
echo Backend running at http://127.0.0.1:8123
echo This launcher can be closed. The backend will keep running in its own window.
exit /b 0
