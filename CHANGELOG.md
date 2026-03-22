# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `test_zombie_and_process_audit.py` — 21 tests for daemon threads, port binding, connection error handling, model browser cleanup, loading spinners
- Loading spinners on Discover grid, Catalog grid, and Download button
- ModelScope as third model discovery source alongside HuggingFace and Discover
- ConnectionAbortedError/ConnectionResetError/BrokenPipeError handling on all write paths (`_send_json`, `do_OPTIONS`, `_sse`, static file serving, `log_message`)
- Enhancement plan updated with latest research: GGUF metadata extraction, epistemic reliability scoring, contamination detection, memory estimation
- Total test count: 406 (was 385)

### Fixed
- Backend no longer crashes with stack trace when browser tab is closed mid-response
- Removed non-functional GitHub tab from model download browser

### Changed
- Rebuild_Prompt.md updated with connection error handling guidance and new test suite
- Enhance_plan.md updated with July 2026 research, process hygiene audit results, and new enhancement items (E14-E17)

## [Previous]

### Added
- Vulkan GPU backend support for AMD Radeon 890M (RDNA 3.5)
- ThreadingHTTPServer — inference no longer blocks UI polling requests
- Backend now serves `model_comparator.html` directly (single-server setup)
- Auto-judge selection: picks largest model when no judge is manually chosen
- Chat bar "Ask Zena" with `/__chat` endpoint and HOW_TO_USE.md system prompt
- `/__download-status` and `/__install` endpoints for model management
- BitNet / incompatible quantization formats (i2_s, i1, i2, i3) skipped at scan time
- `test_completeness_audit.py` — 78 spec-vs-implementation validation tests
- `test_discovery_install.py` — 85 local tests for Hugging Face discovery, llama.cpp install, hardware detection, and model-card data (total: 385 Python tests)

### Fixed
- Backend default port changed 8787 → 8123 (was silently refusing all UI requests)
- `_autoPickJudge()` used `size_mb` but backend returns `size_gb` — fixed unit math
- Judge option values were `local:N` indices; now use real file paths
- Judge scoring now fires with just `judge_model + local_models` (no longer requires
  `judge_system_prompt` to be non-empty — falls back to built-in scoring prompt)
- Model library now scans `C:\AI\Models` by default in addition to home/repo-local locations
- Hugging Face discovery updated for current `huggingface_hub` API (`trendingScore` sort)
- `__del__` traceback from llama_cpp on model unload suppressed

### Changed
- `Run_me.bat` simplified to single server on port 8123
- `requirements.txt` — dependencies pinned to exact installed versions
- Local model library table: family badges now use solid, high-contrast dark-mode colors (replaces faint semi-transparent overlays)
- Local model rows now show the parent directory path instead of the full GGUF file path (filename is already visible in the row label)
- Model name column uses flexible overflow truncation instead of a fixed 170 px cap
