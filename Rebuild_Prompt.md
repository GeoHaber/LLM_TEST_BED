# Rebuild Prompt — Zen LLM Compare

A precise, step-by-step specification that lets you rebuild the entire
application from zero. Every requirement is actionable; nothing is assumed.

---

## 1. What You Are Building

A local, desktop-class web application for comparing multiple GGUF language
models side-by-side on a single prompt. It consists of two files:

| File | Role |
|------|------|
| `comparator_backend.py` | Python HTTP server (~1 800 lines) |
| `model_comparator.html` | Single-file SPA (~3 600 lines) |

Supporting files:

| File | Role |
|------|------|
| `requirements.txt` | Exact pinned dependencies |
| `pyproject.toml` | Package metadata + tool settings |
| `Run_me.bat` | Windows one-click launcher |
| `tests/` | Pytest test suite |

---

## 2. Exact Environment

```
Python        3.10+ (tested on 3.12.10)
OS            Windows 10/11 (Linux also works; bat file is Windows-only)
llama-cpp-python  0.3.16
psutil        7.2.1
huggingface_hub   0.36.0
tiktoken      (no version pin; install latest)
py-cpuinfo    (no version pin; install latest)
pytest        9.0.2
```

`requirements.txt`:
```
psutil==7.2.1
huggingface_hub==0.36.0
llama-cpp-python==0.3.16
tiktoken
py-cpuinfo
```

---

## 3. Directory Layout

```
LLM_TEST_BED/
├── comparator_backend.py    ← backend server
├── model_comparator.html    ← SPA frontend
├── requirements.txt
├── pyproject.toml
├── Run_me.bat
├── CHANGELOG.md
├── README.md
├── HOW_TO_USE.md
├── _patch_catalog.py        ← patch helper for model catalog
└── tests/
    ├── conftest.py
    ├── test_comprehensive.py
    ├── test_bug_fixes.py
    ├── test_full_validation.py
    └── test_llm_integration.py
```

---

## 4. Backend: `comparator_backend.py`

### 4.1 Top-level Constants

```python
DEFAULT_INFERENCE_TIMEOUT = 120   # seconds per model
MAX_INFERENCE_TIMEOUT     = 1800  # 30 min (reasoning models)
MAX_PROMPT_TOKENS         = 4096
PORT                      = 8123
_DISCOVERY_TTL            = 900   # 15-minute HuggingFace cache TTL
```

### 4.2 Environment Setup (at import time, before llama_cpp import)

```python
_vk_devices = os.environ.get('GGML_VK_VISIBLE_DEVICES', '0')
os.environ['GGML_VK_VISIBLE_DEVICES'] = _vk_devices
```

### 4.3 ThreadingHTTPServer

```python
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
```

### 4.4 Hardware Detection Functions

Implement each as a standalone top-level function. They must never raise;
return safe defaults on error.

| Function | Signature | Returns |
|----------|-----------|---------|
| `get_cpu_count()` | `() -> int` | CPU core count; fallback `1` |
| `get_memory_gb()` | `() -> float` | Total RAM GB via psutil; fallback `8.0` |
| `get_cpu_info()` | `() -> dict` | `{brand, name, cores, avx2, avx512}` |
| `get_gpu_info()` | `() -> list[dict]` | `[{name, vram_gb, api}]` per GPU |
| `get_llama_cpp_info()` | `() -> dict` | `{installed: bool, version: str\|None}` |
| `recommend_llama_build()` | `(cpu, gpus) -> dict` | `{build, pip, reason, note}` |

`get_gpu_info()` detection priority:
1. Try `nvidia-ml-py3` (`pynvml`) for NVIDIA.
2. Fall back to `vulkaninfo` subprocess (AMD / Intel).
3. Return `[]` on error.

`recommend_llama_build()` logic:
- NVIDIA GPU found → recommend `CMAKE_ARGS="-DGGML_CUDA=on"`.
- AMD/Intel GPU → recommend `GGML_VULKAN=1`.
- CPU-only with AVX2 → recommend AVX2 build.
- Fallback → plain CPU build.

### 4.5 Model Scanning: `scan_models(model_dirs: list[str]) -> list[dict]`

Rules (all must be enforced):
1. Recursively walk every directory in `model_dirs`. Skip missing dirs silently.
2. Accept only files ending in `.gguf` (case-insensitive).
3. Skip files smaller than 50 MB (`size < 50 * 1024 * 1024`).
4. Skip filenames matching incompatible quantization patterns (regex):
   `r"[-_](i1|i2|i2_s|i2s|i3|i1_s|i1s)\.gguf$"` (case-insensitive).
5. Deduplicate by basename — first occurrence wins.
6. Sort result list alphabetically by `name` (case-insensitive).
7. Each dict: `{name: str, path: str, size_gb: float}`.

### 4.6 System Info: `get_system_info(model_dirs) -> dict`

Returns one JSON-serialisable dict with ALL of these keys:

```
cpu_brand, cpu_count, cpu_name, cpu_avx2, cpu_avx512,
memory_gb, gpus,
has_llama_cpp, llama_cpp_version,
recommended_build,          # {build, pip, reason, note}
model_count, models,        # list from scan_models()
timestamp                   # time.time()
```

### 4.7 Token Counting: `count_tokens(text, model_path=None) -> int`

```python
import tiktoken
_enc = None          # module-level, lazy-loaded
_enc_lock = threading.Lock()

def count_tokens(text: str, model_path=None) -> int:
    global _enc
    if not text:
        return 0
    with _enc_lock:
        if _enc is None:
            _enc = tiktoken.get_encoding("cl100k_base")
    try:
        return len(_enc.encode(text))
    except Exception:
        return len(re.findall(r'\S+', text))  # word-count fallback
```

### 4.8 Extract Judge Scores: `extract_judge_scores(raw_text: str) -> dict`

Five-level fallback cascade. Always returns a dict with at least `overall` (0–10 float).

| Level | Strategy |
|-------|----------|
| 1 | Extract JSON inside ` ```json … ``` ` or ` ``` … ``` ` fence |
| 2 | `json.loads(raw_text.strip())` |
| 3 | Find first `{` and last `}` → try `json.loads()` |
| 4 | Find JSON with a nested `"evaluation"` key |
| 5 | Regex NLP: look for `overall.*?(\d+(?:\.\d+)?)` in raw text |
| 6 | Return `{"overall": 0}` |

Score normalisation (apply after any successful parse):
- String `"8/10"` → `8.0`
- Clamp all numeric scores to `[0, 10]`
- If parsed dict has no `overall` key, compute `mean(all numeric values)`

Schema that `extract_judge_scores` must support (HOW_TO_USE §7):
```json
{
  "overall":     0-10,
  "accuracy":    0-10,
  "reasoning":   0-10,
  "instruction": 0-10,
  "safety":      0-10,
  "explanation": "string"
}
```

### 4.9 Security: `validate_download_url(url: str) -> bool`

Allowed domains (`_ALLOWED_DOWNLOAD_HOSTS`):
```python
{
  "huggingface.co",
  "cdn-lfs.huggingface.co",
  "cdn-lfs-us-1.huggingface.co",
  "github.com",
  "objects.githubusercontent.com",
  "releases.githubusercontent.com",
  "gitlab.com",
}
```

Checks (all must pass for `True`):
1. Scheme is exactly `https`.
2. `netloc` (host) is in `_ALLOWED_DOWNLOAD_HOSTS`.
3. Resolve `netloc` → IP address; reject if private/loopback/link-local/multicast
   (use `ipaddress.ip_address(...).is_private`, `.is_loopback`, etc.).
4. Return `False` on any parse error.

### 4.10 Security: `_is_safe_model_path(path: str, model_dirs: list[str]) -> bool`

1. If `path` is falsy → `False`.
2. `resolved = Path(path).resolve()`.
3. Extension must be exactly `.gguf`.
4. `resolved` must be strictly under at least one resolved model dir (use
   `resolved.is_relative_to(Path(d).resolve())`).
5. Return `False` on any exception.

### 4.11 Rate Limiter: `_RateLimiter`

Sliding-window per-IP rate limiter.

```python
class _RateLimiter:
    def __init__(self, max_requests: int = 30, window_sec: float = 60.0):
        self._max = max_requests
        self._window = window_sec
        self._counts: dict[str, list[float]] = {}  # ip -> list of timestamps
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        """Return True and record the request if within limit."""
        now = time.monotonic()
        with self._lock:
            window = self._counts.setdefault(ip, [])
            # Remove expired
            self._counts[ip] = [t for t in window if now - t < self._window]
            if len(self._counts[ip]) >= self._max:
                return False
            self._counts[ip].append(now)
            return True

    def remaining(self, ip: str) -> int:
        now = time.monotonic()
        with self._lock:
            window = [t for t in self._counts.get(ip, []) if now - t < self._window]
            return max(0, self._max - len(window))
```

Module-level singleton:
```python
_rate_limiter = _RateLimiter(max_requests=30, window_sec=60)
```

### 4.12 Model Discovery: `_discover_hf_models(query, sort, limit) -> list[dict]`

- Minimum TTL cache (`_discovery_cache` dict, `_DISCOVERY_TTL = 900` seconds).
- Calls `huggingface_hub.list_models(...)` filtered to GGUF format.
- Only includes repos from `_TRUSTED_QUANTIZERS`:
  `{"bartowski", "TheBloke", "unsloth", "lmstudio-community",
    "QuantFactory", "second-state", "MaziyarPanahi"}`.
- Returns list of dicts: `{id, name, downloads, likes, tags, url}`.
- Returns `[]` on any network error (must not raise).

### 4.13 ComparatorHandler (HTTP routing)

All endpoint paths listed below. Each MUST:
- Set CORS headers on every response.
- Apply rate limiting (check `_rate_limiter.allow(client_ip)`).

#### CORS Headers

Allowed origins (exact match, case-insensitive):
```
localhost (any port), 127.0.0.1 (any port), ::1 (any port), "null"
```

On match, respond:
```
Access-Control-Allow-Origin: <matched origin>
Vary: Origin
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, X-Requested-With
```

OPTIONS preflight → 204 No Content with above headers.

Do NOT set `Access-Control-Allow-Origin: *`.

#### Endpoint Table

| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| GET | `/__health` | `_handle_health` | `{ok: true, ts: float}` |
| GET | `/__system-info` | `_handle_system_info` | Full hardware + model dict |
| GET | `/__config` | `_handle_config` | Timeout/rate constants |
| GET | `/__discover-models` | `_handle_discover_models` | HF model search |
| GET | `/__download-status` | `_handle_download_status` | Job state |
| GET | `/__install-status` | `_handle_install_status` | Job state |
| POST | `/__comparison/mixed` | `_handle_comparison` | Blocking comparison |
| POST | `/__comparison/stream` | `_handle_stream_comparison` | SSE streaming |
| POST | `/__chat` | `_handle_chat` | Single-turn chat |
| POST | `/__download-model` | `_handle_download_model` | Enqueue download |
| POST | `/__install-llama` | `_handle_install_llama` | Enqueue pip install |
| GET | `/` | serve HTML | Return `model_comparator.html` |
| * | anything else | 404 | `{error: "Not found"}` |

#### `/__config` Response Shape

```json
{
  "default_inference_timeout": 120,
  "max_inference_timeout": 1800,
  "max_prompt_tokens": 4096,
  "rate_limit": {"max_requests": 30, "window_sec": 60},
  "vk_devices": "0"
}
```

#### `/__comparison/mixed` Request / Response

Request:
```json
{
  "prompt":        "string",
  "local_models":  ["path/a.gguf", "path/b.gguf"],
  "online_models": [],
  "judge_model":   "path/judge.gguf or local:best",
  "judge_system_prompt": "custom or empty string",
  "n_ctx":         4096,
  "max_tokens":    512,
  "temperature":   0.7,
  "timeout":       120
}
```

Response:
```json
{
  "prompt":    "string",
  "timestamp": 1234567890.0,
  "responses": [
    {
      "model":     "basename.gguf",
      "text":      "model output",
      "time_ms":   1234,
      "ttft_ms":   456,
      "tokens_out": 64,
      "tps":        7.4,
      "ram_delta_mb": 2048,
      "error":      null
    }
  ],
  "judge": {
    "model":  "judge_basename.gguf",
    "scores": [
      {
        "model":       "basename.gguf",
        "overall":     8.0,
        "accuracy":    7.0,
        "reasoning":   9.0,
        "instruction": 8.0,
        "safety":      9.0,
        "explanation": "string"
      }
    ],
    "error": null
  }
}
```

Safety rules for `_handle_comparison`:
1. Validate prompt token count ≤ `MAX_PROMPT_TOKENS`. If exceeded → 400.
2. Filter `local_models` through `_is_safe_model_path` → `safe_models`.
3. If `judge_model` specified, resolve via `_resolve_judge_path(judge_model, safe_models)`.
4. Also run `_is_safe_model_path(judge_path, self.model_dirs)` on the resolved path.
5. If judge path fails safety check, skip judge (do not raise).

#### `/__comparison/stream` (SSE)

Same request body as `/mixed`.

Response: `Content-Type: text/event-stream`.
Emit newline-delimited SSE events:

| Event | Data |
|-------|------|
| `model_start` | `{"model": "name.gguf"}` |
| `token` | `{"model": "name.gguf", "text": "chunk"}` |
| `model_done` | `{"model": "name.gguf", "time_ms":…, "ttft_ms":…, "tokens_out":…, "tps":…, "ram_delta_mb":…}` |
| `judge_start` | `{"model": "judge.gguf"}` |
| `judge_done` | `{"scores": […]}` |
| `done` | `{}` |

SSE format per event:
```
event: <event_name>\ndata: <json_string>\n\n
```

#### `/__chat` Request

```json
{
  "model_path": "/path/to/model.gguf",
  "messages":   [{"role": "user", "content": "hello"}],
  "n_ctx":      4096,
  "max_tokens": 512,
  "temperature": 0.7
}
```

Response: `{text: "...", tokens_out: N, time_ms: N}` or 400 on error.

Validate `model_path` with `_is_safe_model_path` before use.

#### `/__install-llama` Security

Only allow pip commands that start with exactly:
```
pip install llama-cpp-python
```
(with optional additional flags, but NOT other packages). Return 400 otherwise.

#### `_resolve_judge_path(judge_model, safe_models) -> str | None`

- `"local:best"` → `max(safe_models, key=os.path.getsize, default=None)`.
- Exact basename match in `safe_models`.
- Direct path existence check (`os.path.exists(judge_model)`).
- Otherwise `None`.

#### `_run_judge(prompt, responses, judge_path, judge_system_prompt)`

1. If `judge_system_prompt` is empty, use the fallback:
   ```
   Rate each AI response to the given prompt. Return ONLY valid JSON:
   {"overall": 0-10, "accuracy": 0-10, "reasoning": 0-10,
    "instruction": 0-10, "safety": 0-10, "explanation": "brief rationale"}
   ```
2. Build judge input: concatenate all model responses with labels.
3. Run inference **twice** with swapped response order (position-bias mitigation, NeurIPS 2023).
4. Average the two score sets before returning.
5. Retry once on failure.

### 4.14 Model Directory

```python
_DEFAULT_MODEL_DIR = Path.home() / "AI" / "Models"
MODEL_DIR = Path(os.environ.get("ZENAI_MODEL_DIR", str(_DEFAULT_MODEL_DIR)))
```

### 4.15 Entry Point

```python
def run_server(port: int = 8123):
    server = ThreadingHTTPServer(("127.0.0.1", port), ComparatorHandler)
    print(f"[ZenAI] Server running at http://127.0.0.1:{port}")
    server.serve_forever()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    run_server(port)
```

---

## 5. Frontend: `model_comparator.html`

Single self-contained HTML file. No build step. No external JS frameworks.
CSS via Tailwind CDN only.

### 5.1 Global JS Variables / Constants

| Name | Type | Description |
|------|------|-------------|
| `BACKEND_URL` | `const` | `"http://127.0.0.1:8123"` |
| `_QUESTION_BANK` | `const` | 32 test prompts in 6 categories |
| `_MODEL_CATALOG` | `const` | 50+ GGUF model descriptors |
| `_SCENARIOS` | `const` | Preset scenarios (stress test, etc.) |

### 5.2 Question Bank

32 questions across 6 categories. Each entry:
```js
{ q: "the prompt text", cat: "category_id", lang: "en" }
```

Categories:
- `ops` — Operational / logistics
- `emergency` — Emergency medicine
- `cardiology` — Cardiology
- `coding` — Code generation
- `reasoning` — Logical reasoning
- `multilingual` — Non-English prompts

### 5.3 Required UI Sections

| Section | Feature |
|---------|---------|
| Left sidebar | Model checklist (populated from `/__system-info`) |
| Prompt area | Textarea + "Question Bank" chip row |
| Judge config | Template selector (5 templates), model picker |
| Comparison results | One card per model: text + metrics |
| Metrics bar | Champion stats: fastest TTFT, best quality, fastest TPS, smallest delta |
| Judge panel | Score table for all 5 fields |
| Leaderboard | ELO table (persisted in localStorage) |
| Run history | List of past runs (localStorage) |
| Zena chat | Overlay and/or inline chat bar calling `/__chat` |
| Discovery panel | HuggingFace model search calling `/__discover-models` |
| Monkey Mode | Random question + random models button |
| Batch mode | Run N prompts in sequence |
| CSV export | Download results as CSV |
| Share report | Copy URL+JSON to clipboard |
| Dark mode | Toggle via class on `<html>` |
| Language switcher | EN / HE / AR / ES / FR / DE; RTL for HE and AR |

### 5.4 Judge Templates (5 required)

| # | Name | Focus |
|---|------|-------|
| 1 | Medical/Clinical | Patient safety, accuracy |
| 2 | Research | Methodological rigour |
| 3 | Code Review | Correctness, security, style |
| 4 | Creative Writing | Originality, coherence |
| 5 | General | Balanced 5-field scoring |

### 5.5 Streaming (SSE)

```js
const evtSource = new EventSource(...) -- use fetch + ReadableStream
// Pattern:
const resp = await fetch(BACKEND_URL + "/__comparison/stream", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
});
// Read the stream line by line, parse "event: X\ndata: Y\n\n" blocks.
```

On each event type, update the corresponding model card in real time.

### 5.6 ELO System

```js
const K = 32;
function updateElo(winner, loser, eloTable) {
    const Ew = 1 / (1 + Math.pow(10, (eloTable[loser] - eloTable[winner]) / 400));
    eloTable[winner] += K * (1 - Ew);
    eloTable[loser]  -= K * (1 - Ew);
}
// Persist as JSON in localStorage under key "zai_elo"
```

### 5.7 XSS Prevention

Every string rendered as HTML must go through `escHtml()`:
```js
function escHtml(s) {
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}
```

---

## 6. `Run_me.bat`

```bat
@echo off
echo Starting Zen LLM Compare...
python -m pip install -r requirements.txt --quiet
python comparator_backend.py
```

---

## 7. Tests

### 7.1 `tests/conftest.py`

Empty or minimal fixture definitions shared across test modules.

### 7.2 Running Tests

```
# All fast tests (no GGUF models needed):
pytest tests/ --ignore=tests/test_llm_integration.py -v

# Full suite (requires GGUF models in ~/AI/Models):
pytest tests/ -v

# New validation suite only:
pytest tests/test_full_validation.py -v
```

### 7.3 Test Coverage Required

| Group | What to test |
|-------|-------------|
| HTTP endpoints | Every documented endpoint returns correct status + schema |
| CORS | Localhost allowed, external blocked, Vary header present |
| SSRF | All private IPs, loopback, file://, HTTP blocked |
| Path traversal | `..` sequences, wrong extensions |
| Rate limiter | Exact boundary, per-IP isolation, thread safety |
| Judge scores | All 6 fallback levels, clamp, float, Unicode |
| Model scanning | Skip tiny, skip incompatible quants, dedup, sort |
| Judge security | Uses `safe_models`, not raw `local_models` |
| Prompt schema | Fallback uses 0-10, not bool/string |

---

## 8. Common Mistakes to Avoid

1. **Do NOT use `local_models` for judge path resolution** — always use
   `safe_models` (the output of `_is_safe_model_path` filter).
2. **Do NOT set `Access-Control-Allow-Origin: *`** — echo the exact origin.
3. **Do NOT skip `_is_safe_model_path` on the judge path** — even if the
   judge model came from `safe_models`, re-validate before loading.
4. **Judge fallback prompt must use 0-10 integer scale** for all 5 fields —
   never `true/false` or `"safe"/"unsafe"`.
5. **Model scan: 50 MB minimum** — the user may have partial/incomplete
   downloads; ignore them.
6. **`count_tokens` must be thread-safe** — the tiktoken encoder is lazy-loaded
   under a lock.
7. **SSE content-type header** must be `text/event-stream` with
   `Cache-Control: no-cache` before any data is written.
8. **Default port is 8123** — hard-require this in tests and bat file.
9. **Model directory** — use `Path.home() / "AI" / "Models"`, not
   `C:\AI\Models`. Document as `~/AI/Models`.
10. **`escHtml()` on all user-visible LLM output** — LLM text is untrusted.
