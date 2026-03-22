# Rebuild Prompt — Zen LLM Compare

> A step-by-step prompt that any capable LLM can follow to rebuild the entire
> application from zero. All file names, folder layout, dependencies, API
> signatures, and sample I/O are given explicitly so there is **nothing to guess**.

---

## 0. Before You Begin

| Prereq | Version |
|--------|---------|
| Python | ≥ 3.10 (tested on 3.13) |
| pip | latest |
| OS | Windows 10/11 (primary), Linux/macOS (supported) |
| GPU | Optional — Vulkan-capable GPU (AMD, NVIDIA, Intel Arc) for acceleration |
| Disk | ≥ 500 MB free for models |

---

## 1. Folder Layout

Create a single flat project directory with these files:

```
LLM_TEST_BED/
├── comparator_backend.py        # Python HTTP backend (~1850 lines)
├── model_comparator.html        # Single-file SPA frontend (~3700 lines)
├── _patch_catalog.py            # Utility: update MODEL_CATALOG in HTML
├── Run_me.bat                   # Windows one-click launcher
├── pyproject.toml               # Project metadata & tool config
├── requirements.txt             # Pinned dependencies
├── HOW_TO_USE.md                # Usage guide (consumed by Zena AI)
├── LLM_COMPARE_2026.md          # Landscape analysis / spec
├── README.md                    # Repo readme
├── CHANGELOG.md                 # Release notes
├── LICENSE                      # MIT License
└── tests/
    ├── conftest.py              # Shared fixtures (app module import)
    ├── test_bug_fixes.py        # 103 unit tests
    ├── test_completeness_audit.py  # 78 spec-vs-implementation tests
    ├── test_xray_comprehensive.py  # 119 functional tests
    ├── test_llm_integration.py   # Live inference integration test
    └── test_comparator.html      # Browser Mocha test suite
```

**Important:** There are exactly **two source files** (backend + frontend). No
framework, no build step, no node_modules. The HTML file includes Tailwind CSS
via CDN. The backend serves it via Python's `http.server`.

---

## 2. Install Dependencies

```bash
pip install psutil==7.2.1 huggingface-hub==0.36.0 llama-cpp-python==0.3.16 \
    py-cpuinfo>=9.0 nvidia-ml-py3>=7.352 tiktoken>=0.5
```

For Vulkan GPU acceleration (AMD Radeon, Intel Arc):
```bash
pip install llama-cpp-python==0.3.16 --force-reinstall --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/vulkan
```

For CUDA (NVIDIA):
```bash
pip install llama-cpp-python==0.3.16 --force-reinstall --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
```

Dev tools:
```bash
pip install pytest>=8.0 ruff>=0.4 bandit>=1.7 pyright>=1.1
```

---

## 3. Create `pyproject.toml`

```toml
[project]
name = "zen-llm-compare"
version = "0.1.0"
description = "Local LLM model comparator with judge scoring and GPU acceleration"
requires-python = ">=3.10"
dependencies = [
    "psutil==7.2.1",
    "huggingface_hub==0.36.0",
    "llama-cpp-python==0.3.16",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.4", "bandit>=1.7", "pyright>=1.1"]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.bandit]
exclude_dirs = [".venv", "venv", "__pycache__", "node_modules", "tests"]
skips = ["B603", "B607"]
```

---

## 4. Create `requirements.txt`

```
psutil==7.2.1
huggingface-hub==0.36.0
llama-cpp-python==0.3.16
py-cpuinfo>=9.0
nvidia-ml-py3>=7.352
tiktoken>=0.5
pytest>=7.0
```

---

## 5. Create `comparator_backend.py`

This is a single Python file (~1850 lines) using only stdlib + the dependencies
above. Follow these exact specifications:

### 5.1 Imports & Globals

```python
import ipaddress, json, os, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse
```

Set Vulkan env var before any llama_cpp import:
```python
_vk_devices = os.environ.get('GGML_VK_VISIBLE_DEVICES', '0')
os.environ['GGML_VK_VISIBLE_DEVICES'] = _vk_devices
```

### 5.2 ThreadingHTTPServer

```python
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
```

### 5.3 System Info Detection

Create these functions:

| Function | Returns | Notes |
|----------|---------|-------|
| `get_cpu_count()` | `int` | `os.cpu_count()` with fallback to 1 |
| `get_memory_gb()` | `float` | Via psutil; fallback 8.0 |
| `get_cpu_info()` | `dict` | Keys: `brand`, `name`, `cores`, `avx2`, `avx512`. Try `py-cpuinfo`, fall back to `platform.processor()` and env vars |
| `detect_gpus()` | `list[dict]` | Each dict: `name`, `vram_mb`, `type` ("nvidia"/"amd"/"integrated"). Try pynvml, then WMI, then `GGML_VK_VISIBLE_DEVICES` |
| `recommend_build()` | `str` | Returns "vulkan", "cuda", or "cpu" based on detected GPUs |
| `get_system_info()` | `dict` | Aggregates all above + scans models. Keys: `cpu_brand`, `cpu_count`, `cpu_name`, `cpu_avx2`, `memory_gb`, `gpus`, `recommended_build`, `has_llama_cpp`, `model_count`, `models`, `timestamp` |

### 5.4 Model Scanning

```python
MODEL_DIRS = [str(Path.home() / "AI" / "Models")]
INCOMPATIBLE_QUANTS = {"IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S"}
```

`scan_models()` → `list[dict]`:
- Walk each dir in `MODEL_DIRS` recursively for `*.gguf` files
- For each file return: `name`, `path`, `size_mb`, `quant` (extracted from filename, e.g., "Q4_K_M")
- Skip files whose quant is in `INCOMPATIBLE_QUANTS`
- Wrap each dir scan in try/except to handle missing directories

### 5.5 Token Counting

```python
def count_tokens(text: str) -> int:
```
Use `tiktoken` with `cl100k_base` encoding. Fallback: `len(text) // 4`.

### 5.6 Judge Score Extraction — 5-Layer Fallback

```python
def extract_judge_scores(text: str) -> dict:
```

Try each method in order, stop when `overall` is found:

1. **JSON parse** — `json.loads(text)` or extract `{...}` from text  
2. **Markdown fences** — find ````json ... ``` `` blocks, parse JSON
3. **Brace extraction** — `re.search(r'\{[^{}]+\}', text)`, parse JSON
4. **Regex NLP** — named patterns like `overall[\s:]+(\d+)`, `accuracy[\s:]+(\d+)`, etc.
5. **Keyword fallback** — `score[:\s]*(\d+)` near "score" keyword

If no `overall` but other scores exist, average them. Clamp all values to 0–10.

### 5.7 URL & Path Validation

```python
def validate_download_url(url: str) -> bool:
```
- Must start with `https://huggingface.co/` or `https://hf-mirror.com/`
- Resolve the hostname and reject if it maps to a private/reserved IP (SSRF prevention)
- Reject `localhost`, `127.x.x.x`, `10.x.x.x`, `172.16-31.x.x`, `192.168.x.x`, link-local

```python
def safe_model_path(raw: str) -> str:
```
- Reject path traversal (`..`)
- Reject paths outside `MODEL_DIRS`
- Return the realpath if safe

### 5.8 Rate Limiter

```python
class _RateLimiter:
    def __init__(self, max_requests=30, window_sec=60.0)
    def allow(self, ip: str) -> bool
```
Per-IP sliding window using `collections.deque`.

### 5.9 HTTP Handler — `ComparatorHandler(BaseHTTPRequestHandler)`

CORS: Only allow `Origin` matching `localhost` (http://localhost:*, http://127.0.0.1:*).
Do NOT use `Access-Control-Allow-Origin: *`.

Rate limit check on every request. Return 429 JSON if exceeded.

#### GET Endpoints

| Path | Response |
|------|----------|
| `/` or `/index.html` | Serve `model_comparator.html` from same directory |
| `/__health` | `{"ok": true}` |
| `/__system-info` | Full system detection (see 5.3) |
| `/__config` | `{"vk_devices", "default_inference_timeout", "max_inference_timeout", "max_prompt_tokens", "rate_limit"}` |
| `/__download-status?job_id=X` | Download job progress |
| `/__install-status` | llama-cpp-python install progress |
| `/__discover-models?q=...&sort=...&limit=...` | HuggingFace GGUF search (cached 15 min) |
| `/__chat/history` | Return chat history from `_chat_history` list |

#### POST Endpoints (read JSON body, enforce Content-Length ≤ 10 MB)

| Path | Body Fields | Response |
|------|-------------|----------|
| `/__comparison/mixed` | `prompt`, `system_prompt`, `local_models[]`, `online_models[]`, `judge_model`, `judge_system_prompt`, `max_tokens`, `temperature`, `n_ctx`, `top_p`, `repeat_penalty`, `inference_timeout` | Ranked results with judge scores |
| `/__comparison/stream` | Same as above | SSE text/event-stream |
| `/__chat` | `model_path`, `system`, `messages[]`, `max_tokens`, `temperature` | `{"response": "..."}` |
| `/__download-model` | `model` (URL), `dest` (dir) | `{"ok": true, "job_id": "..."}` |
| `/__install-llama` | (empty body) | Triggers pip install in background |

### 5.10 Comparison Engine

`_handle_comparison(data)`:
1. Validate prompt is not empty
2. Count tokens; reject if > `MAX_PROMPT_TOKENS` (8192)
3. For each model in `local_models`, sequentially:
   a. Load with `llama_cpp.Llama(model_path, n_ctx, n_gpu_layers=-1, verbose=False)`
   b. Record RAM before/after via psutil
   c. Call `model.create_completion(prompt, max_tokens, temperature, top_p, repeat_penalty, stream=False)`
   d. Record: `response`, `time_ms`, `tokens`, `tokens_per_sec`, `ttft_ms`, `ram_delta_mb`
   e. Unload model explicitly with `del model`
4. If `judge_model` provided, run `_run_judge()` for each response
5. Rank by `judge_score` descending (or `tokens_per_sec` if no judge)

`_run_judge(judge_path, prompt, response, system_prompt)`:
- **Dual-pass position bias mitigation** (Zheng et al., NeurIPS 2023):
  - Pass 1: Present response in original order
  - Pass 2: Shuffle/randomize order
  - Average both passes' scores
- Load judge model, generate, extract via `extract_judge_scores()`
- Return dict with `overall`, `accuracy`, `reasoning`, `instruction`, `safety`, `explanation`, `bias_passes`

### 5.11 SSE Streaming

`_handle_stream_comparison(data)`:
- Set `Content-Type: text/event-stream`, `Cache-Control: no-cache`
- For each model, stream events:
  - `event: model_start\ndata: {"model": "...", "index": N}\n\n`
  - `event: token\ndata: {"token": "...", "index": N}\n\n` (per generated token)
  - `event: model_done\ndata: {...stats...}\n\n`
- After all models, if judge: `event: judge_start` → `event: judge_done`
- Final: `event: done\ndata: {...full results...}\n\n`

### 5.12 Download Worker

`_run_download(job_id, model, dest)`:
- If URL contains `huggingface.co` with a direct file path, download via `huggingface_hub.hf_hub_download(repo_id, filename, local_dir=dest)`
- Otherwise, use `huggingface_hub.hf_hub_download` with extracted repo_id and filename
- Update `_download_jobs[job_id]` dict with state/progress/path/error
- Support both direct URLs and `repo_id/filename` patterns

### 5.13 Chat Handler

`_handle_chat(data)`:
- Load model from `model_path`
- Build messages array (system + user messages)
- Generate with `model.create_chat_completion(messages, max_tokens, temperature)`
- Return `{"response": "..."}`
- Append to `_chat_history` list

### 5.14 Server Startup

```python
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
server = ThreadingHTTPServer(("127.0.0.1", PORT), ComparatorHandler)
print(f"Serving on http://127.0.0.1:{PORT}")
server.serve_forever()
```

---

## 6. Create `model_comparator.html`

Single-file SPA. No build step. ~3700 lines.

### 6.1 Head Section

```html
<!DOCTYPE html>
<html class="dark" lang="en">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Test LLMs</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
```

Tailwind config:
- Dark mode: `"class"` on `<html>`
- Primary color: `#6366f1` (Indigo)
- Font: Inter

### 6.2 Layout (CSS Grid/Flex)

Three-region layout:
1. **Left sidebar** (~280px): System status, model checkboxes, judge config, parameters
2. **Main area** (flex-1): Results table, streaming cards, Zena chat
3. **Tabs at top of sidebar**: Comparator | Zena | Model Library | Downloads | Discover

### 6.3 Sidebar Components

| Component | HTML ID | Purpose |
|-----------|---------|---------|
| System status | `systemStatus` | Shows CPU/RAM/GPU info from `/__system-info` |
| Model checkboxes | `modelCheckboxes` | Populated from `/__system-info` models array |
| Judge model dropdown | `judgeModel` | Select a model to use as judge |
| Judge template dropdown | `judgeTemplate` | Values: `medical`, `general`, `coding`, `reasoning`, `multilingual` |
| Prompt textarea | `prompt` | User's test prompt |
| System prompt textarea | `systemPrompt` | System instruction |
| Parameter sliders | `maxTokens`, `temperature`, `topP`, `repeatPenalty`, `inferenceTimeout`, `nCtx` | With live value display |
| Question bank chips | `questionBank` | Chips: 🚨 Emergency, 🏥 Ops, ❤️ Cardiology, 💻 Coding, 🧠 Reasoning, 🌍 Multilingual, 🎲 Random |
| Scenario presets | below question bank | Chips: 💻 Code Review, 🧠 Logic Duel, 🌍 Polyglot, ⚡ Speed Run, 🩺 Clinical, 🎲 Surprise |

### 6.4 Key JavaScript Functions (60+)

#### System & Init
- `checkSystemStatus()` — Fetch `/__system-info`, populate sidebar, show/hide GPU info
- `populateModelLibrary()` — Build model library grid from `MODEL_CATALOG` object
- `escHtml(str)` — XSS-safe HTML escaping (replace `&`, `<`, `>`, `"`, `'`)

#### Comparison
- `runComparison()` — Main entry: collect form data, POST to `/__comparison/mixed` or stream
- `_runStreamComparison(payload)` — Open EventSource to `/__comparison/stream`, process SSE
- `_showStreamingUI()` — Create streaming card UI for each model
- `_handleStreamEvent(event)` — Route `model_start|token|model_done|judge_start|judge_done|done`
- `displayResults(data)` — Render results table with 12 columns

#### ELO & History
- `_updateElo(results)` — Update ELO ratings from comparison results (K=32)
- `_renderLeaderboard()` — Draw leaderboard table from ELO data in localStorage
- `_saveToHistory(results)` — Persist run to localStorage `lc_history`
- `_shareReport()` — Generate shareable JSON blob from latest results

#### Batch Mode
- `_runBatch()` — Run comparisons across multiple prompts sequentially
- Progress tracking with abort capability

#### Model Library & Downloads
- `MODEL_CATALOG` — Object mapping model names to `{repo, file, size, quant, bestFor, params}` (~50 models)
- `_modelFitness(model)` — Score 0–100 for how well a model fits current hardware
- `downloadModel(repo, file)` — POST to `/__download-model`, start progress polling
- `switchRepo(tab, el)` — Switch between "Featured", "HuggingFace", "Discover" tabs in download modal

#### Discovery System
- `runDiscoverSearch()` — Fetch `/__discover-models`, render results
- `renderDiscoverResults(models)` — Build discover results grid

#### Zena Chat
- `sendZenaMessage()` — POST to `/__chat` with conversation history
- Markdown rendering in chat bubbles

#### Scenarios & Question Bank
```javascript
const _SCENARIOS = {
  code_review: { name:'Code Review', prompt:'...', system:'...', judgeTemplate:'coding', temp:0.1, maxTok:1024 },
  logic_duel:  { name:'Logic Duel', prompt:'...', system:'...', judgeTemplate:'reasoning', temp:0.3, maxTok:512 },
  polyglot:    { name:'Polyglot', ... },
  speed_run:   { name:'Speed Run', ... },
  clinical:    { name:'Clinical Showdown', judgeTemplate:'medical', ... },
  surprise:    { name:'Surprise Me', ... },
};

const _QUESTION_BANK = {
  ops: [{q:"...", sys:"..."}, ...],       // 5 questions
  emergency: [...],                        // 6 questions
  cardiology: [...],                       // 5 questions
  coding: [...],                           // 6 questions
  reasoning: [...],                        // 5 questions
  multilingual: [...],                     // 5 questions
};
```
`loadScenario(key)` — Populate all form fields from scenario config.
`loadQuestion(topic)` — Pick random question from bank, fill prompt + system prompt.

### 6.5 Results Table Columns

| # | Column | Source |
|---|--------|--------|
| 1 | Rank | Sorted by judge score or tokens/sec |
| 2 | Model | File name (without .gguf) |
| 3 | Response | Truncated text (expandable) |
| 4 | Judge Score | Overall 0–10 with color coding |
| 5 | Accuracy | 0–10 |
| 6 | Reasoning | 0–10 |
| 7 | Time (ms) | Inference wall time |
| 8 | Tokens | Generated token count |
| 9 | Tok/s | Tokens per second |
| 10 | TTFT (ms) | Time to first token |
| 11 | RAM Δ (MB) | Memory delta during inference |
| 12 | Details | Expandable judge explanation |

### 6.6 Security

- All user-supplied text rendered via `escHtml()` — never `innerHTML` with raw data
- Dark mode toggle persisted in localStorage
- No cookies, no external analytics

---

## 7. Create `Run_me.bat`

```batch
@echo off
title Zen LLM Compare
echo Starting backend on port 8123...

start "" python comparator_backend.py
set BACKEND_PID=
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq python.exe" /fo list ^| findstr /i "PID"') do set BACKEND_PID=%%a

timeout /t 2 /nobreak >nul
start "" http://localhost:8123

echo.
echo Press any key to stop the server...
pause >nul

if defined BACKEND_PID (
    taskkill /PID %BACKEND_PID% /F >nul 2>&1
)
```

---

## 8. Create `tests/conftest.py`

```python
import importlib, sys
from pathlib import Path

def _load_app():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    return importlib.import_module("comparator_backend")

app = _load_app()
```

---

## 9. Create Test Files

### 9.1 `tests/test_bug_fixes.py` — 100 unit tests

Test classes to include:

| Class | Tests | What it validates |
|-------|-------|-------------------|
| `TestTokenCounting` | ~6 | `count_tokens()` with various inputs including empty, unicode, long text |
| `TestCORS` | ~6 | Localhost allowed, external origins rejected, preflight OPTIONS |
| `TestJudgeRetry` | ~8 | 5-layer extraction: JSON, markdown fences, braces, regex NLP, keyword |
| `TestURLValidation` | ~10 | SSRF prevention: private IPs, localhost, valid HF URLs |
| `TestRateLimiter` | ~5 | Allow up to 30 requests, block 31st, window expiry |
| `TestInferenceTimeout` | ~4 | Timeout signal handling |
| `TestConfigEndpoint` | ~4 | `/__config` response structure |
| `TestMultiGPU` | ~6 | `detect_gpus()`, Vulkan env handling |
| `TestJudgeBias` | ~8 | Dual-pass randomization, score averaging |
| `TestSSEStreaming` | ~8 | Event format, model_start/token/done events |
| `TestFrontendEnhancements` | ~15 | HTML contains required elements (IDs, classes, functions) |
| `TestModelDiscovery` | ~10 | HuggingFace search, caching, error handling |

### 9.2 `tests/test_xray_comprehensive.py` — 119 functional tests

Full-coverage validation of every feature. See test file for details.

### 9.3 `tests/test_llm_integration.py` — Live inference test

Requires an actual GGUF model on disk. Tests:
1. Unit functions (system info, model scanning, token counting)
2. HTTP endpoints (start server, GET /__system-info, POST /__comparison/mixed)
3. Full LLM inference (load model, generate, verify output)

Run separately: `python tests/test_llm_integration.py`

---

## 10. Run & Verify

### Build Commands

```bash
# Run all fast tests (no GPU/model needed)
python -m pytest tests/test_bug_fixes.py tests/test_xray_comprehensive.py -v

# Run with coverage
python -m pytest tests/ -v --tb=short

# Lint
ruff check .
ruff format --check .

# Security scan
bandit -r comparator_backend.py

# Type check
pyright comparator_backend.py
```

### Expected Output

```
tests/test_bug_fixes.py            — 103 passed
tests/test_xray_comprehensive.py   — 119 passed
tests/test_completeness_audit.py   —  78 passed
tests/test_llm_integration.py      —   3 passed (requires model)
─────────────────────────────────────────────────
TOTAL                                303 passed, 3 warnings
```

### Manual Smoke Test

1. `python comparator_backend.py` → should print `Serving on http://localhost:8123`
2. Open `http://localhost:8123` → should see the UI with dark theme
3. Sidebar should populate with system info and any detected models
4. Place a `.gguf` file in `~/AI/Models/` → should appear after scan
5. Select 2 models, enter a prompt, click ▶ Run → should see streaming results
6. Judge scores should appear if a judge model is selected

### Sample API Calls

```bash
# Health check
curl http://localhost:8123/__health
# → {"ok": true}

# System info
curl http://localhost:8123/__system-info
# → {"cpu_brand":"AMD","cpu_count":16,"memory_gb":31.2,"gpus":[{"name":"Radeon RX 7900 XTX","vram_mb":24560,"type":"amd"}],...}

# Run comparison
curl -X POST http://localhost:8123/__comparison/mixed \
  -H "Content-Type: application/json" \
  -H "Origin: http://localhost:8123" \
  -d '{"prompt":"What is 2+2?","local_models":["C:\\AI\\Models\\model.gguf"],"max_tokens":64}'
# → {"prompt":"What is 2+2?","models_tested":1,"responses":[{...}]}

# Download a model
curl -X POST http://localhost:8123/__download-model \
  -H "Content-Type: application/json" \
  -H "Origin: http://localhost:8123" \
  -d '{"model":"https://huggingface.co/TheBloke/Llama-2-7B-GGUF/resolve/main/llama-2-7b.Q4_K_M.gguf","dest":"C:\\AI\\Models"}'
# → {"ok":true,"job_id":"a1b2c3d4"}
```

---

## 11. Key Design Decisions (Why This Architecture)

1. **Two files, zero build step** — Anyone can `python backend.py` + open browser. No npm, no webpack.
2. **Vanilla JS + Tailwind CDN** — No React/Vue/Svelte to learn. Tailwind via CDN means no purge step.
3. **ThreadingHTTPServer** — One thread per request. Inference blocks its thread but not the UI.
4. **Sequential model loading** — Only one GGUF model in VRAM at a time. Prevents OOM.
5. **Dual-pass judge** — Randomize response order across two passes to cancel position bias.
6. **5-layer score extraction** — LLMs are unpredictable in output format. Each layer catches a different failure mode.
7. **CORS restricted to localhost** — This is a local tool, not a public service.
8. **SSRF defense on downloads** — URLs resolve to HuggingFace only. Private IPs rejected.
9. **Rate limiter** — 30 req/min per IP. Prevents accidental infinite loops in frontend.
10. **Vulkan as default GPU backend** — Works on AMD, NVIDIA, and Intel Arc. CUDA is optional.

---

## 12. Common Pitfalls to Avoid

- **Do NOT** use `Access-Control-Allow-Origin: *`. Only allow `localhost` origins.
- **Do NOT** load multiple GGUF models simultaneously — they can each consume 4–16 GB VRAM.
- **Do NOT** use `innerHTML` with unescaped user data — always pass through `escHtml()`.
- **Do NOT** use `subprocess.run(shell=True)` — the install handler must use `shell=False`.
- **Do NOT** serve on `0.0.0.0` in production. This is a local development tool only.
- **Do NOT** forget to `del model` after inference — llama-cpp-python does not auto-release VRAM.
- **Do NOT** hardcode model paths — always use `MODEL_DIRS` and dynamic scanning.
