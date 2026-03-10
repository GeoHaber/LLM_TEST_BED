@echo off
title ZenAIos Dashboard

:: ── Optional: pin a default model (comment out to let Local_LLM auto-select) ─
set DEFAULT_MODEL=C:\AI\Models\Qwen3.5-4B-Q4_K_M.gguf

echo Starting ZenAIos Smart Server on http://localhost:8787 ...
echo.
echo  App    -^>  http://localhost:8787/login.html
echo  Admin  -^>  http://localhost:8787/__admin
if defined DEFAULT_MODEL echo  Model  -^>  %DEFAULT_MODEL%
echo.
start "" http://localhost:8787/login.html
start "" http://localhost:8787/__admin
python server.py
pause
