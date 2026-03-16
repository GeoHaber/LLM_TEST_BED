@echo off
title LLM Test Bed — AI Chat
cd /d "%~dp0"

echo.
echo  ================================================================
echo   LLM Test Bed — Local AI Chat
echo  ================================================================
echo.

:: ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Install Python 3.10+ and add to PATH.
    echo          https://www.python.org/downloads/
    pause
    exit /b 1
)

:: ── Install dependencies if needed ───────────────────────────────────────────
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo  [SETUP] Installing dependencies...
    pip install -r requirements.txt
    echo.
)

:: ── Check for llama-server ───────────────────────────────────────────────────
set LLAMA_EXE=
if exist "bin\llama-server.exe" set LLAMA_EXE=bin\llama-server.exe
if exist "C:\AI\bin\llama-server.exe" set LLAMA_EXE=C:\AI\bin\llama-server.exe

if not defined LLAMA_EXE (
    echo  [WARN] llama-server.exe not found in bin\ or C:\AI\bin\
    echo         Download from: https://github.com/ggml-org/llama.cpp/releases
    echo         Place in the bin\ folder next to this script.
    echo.
)

:: ── Check for models ─────────────────────────────────────────────────────────
set MODEL_DIR=C:\AI\Models
if not exist "%MODEL_DIR%\*.gguf" (
    echo  [WARN] No .gguf models found in %MODEL_DIR%
    echo         Download GGUF models and place them there.
    echo.
)

:: ── Launch ───────────────────────────────────────────────────────────────────
echo  Starting AI Chat on http://localhost:8080 ...
echo  Press Ctrl+C to stop.
echo.

python chat.py
pause
