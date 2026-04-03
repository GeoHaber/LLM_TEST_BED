@echo off
REM Fix llama-cpp-python on Windows (build from source with AVX2 off).
REM Activate venv if present, then run the PowerShell script.
REM
REM Flags (pass through to PowerShell script):
REM   (none)           -- install latest prebuilt wheel
REM   -UpdateBinary    -- also download latest llama-server.exe (Vulkan) to C:\AI\_bin
REM   -UpdatePython    -- upgrade llama-cpp-python Python wheel to latest PyPI release
REM   -CheckUpdate     -- print version status only (no install)
REM   -BuildFromSource -- rebuild from source with AVX2 off (needs Visual Studio)
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo No .venv found. Create one with: py -3.12 -m venv .venv
    echo Then run: .venv\Scripts\activate.bat
    echo Then run this script again.
    exit /b 1
)

powershell -ExecutionPolicy Bypass -File "%~dp0install_llama_cpp_windows.ps1" %*
exit /b %ERRORLEVEL%
