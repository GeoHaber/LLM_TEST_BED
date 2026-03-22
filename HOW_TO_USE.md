# Zen LLM Compare — How to Use

> **For humans and LLMs alike.** This document is the primary knowledge source for Zena (the built-in AI assistant). It is also useful as a system prompt for any external LLM that needs to understand this app.

---

## What Is Zen LLM Compare?

Zen LLM Compare (codename **Swarm**) is a self-hosted, browser-based benchmarking tool that runs multiple local GGUF language models **side-by-side** on the same prompt, scores every response with a configurable **LLM judge**, and presents ranked results with detailed performance metrics.

Key properties:
- **Zero cloud dependency.** All inference runs locally via `llama-cpp-python`.
- **Single HTML file UI.** `model_comparator.html` — no build step, no framework.
- **Python backend.** `comparator_backend.py` (stdlib + `llama_cpp`) on port **8123**.
- **Supports 1–8 models per run** in parallel threads.
- **Judge model is also a local GGUF** — you choose which model grades the others.

---

## Architecture

```
Browser (model_comparator.html)
        │  HTTP  │
        ▼        ▼
comparator_backend.py  :8123
        │
        ├── /__system-info      (GET)  — scan models, RAM, GPU
        ├── /__comparison/mixed (POST) — run models + judge
        ├── /__download-model   (POST) — fetch GGUF from URL
        ├── /__install-llama    (POST) — pip install llama_cpp
        ├── /__install-status   (GET)  — install progress
        └── /__chat             (POST) — Zena chat assistant
```

---

## Quick Start

1. **Start the backend:**  double-click `Run_me.bat`  
   (or `python comparator_backend.py` in a terminal)

2. **Open the UI:** the bat file opens `model_comparator.html` automatically,  
   or open it manually in any modern browser.

3. **First run:** The app scans `C:\AI\Models` (and sub-directories) on load.  
   Each found `.gguf` file becomes a selectable chip.

4. **Select models** in the left panel, **type a prompt**, choose a **judge model**,  
   then click the big **RUN** button.

---

## Adding Models

| Method | Steps |
|--------|-------|
| **Local file** | Drop a `.gguf` into `C:\AI\Models`, then click **Scan** in the app |
| **Download tab** | Paste a HuggingFace direct-download URL and click Download |
| **Custom path** | Edit `MODEL_DIRS` in `comparator_backend.py` to add more directories |

Model scanning is automatic on page load and after each download completes.

---

## Running a Comparison

1. **Tick 1–8 model checkboxes** in the Models panel (left side).
2. **Type or paste a prompt** in the Prompt box.
3. **Choose a Judge template** from the dropdown (see Judge Templates below).
4. **Choose the Judge model** — a separate GGUF that will score all responses.
5. Click **RUN** (or press the hotkey shown on the button).
6. Results appear in the table as each model finishes; the judge scores appear once all models are done.

### Parallel execution
All selected models run simultaneously in background threads. Faster/smaller models finish first and appear incrementally.

---

## Judge Templates

Each template focuses on different scoring criteria. All output a unified JSON schema:

```json
{
  "overall": 0–10,
  "accuracy": 0–10,
  "reasoning": 0–10,
  "instruction": 0–10,
  "safety": 0–10,
  "explanation": "free-text rationale"
}
```

| Template | Value | Best for |
|----------|-------|----------|
| **🏥 Medical / Clinical** | `medical` | Emergency care, triage, clinical decision prompts |
| **💬 General Assistant** | `general` | General-purpose Q&A, instruction following |
| **💻 Code Quality** | `coding` | Programming, debugging, code review |
| **🧠 Reasoning / Math** | `reasoning` | Logic, math, multi-step reasoning |
| **🌍 Multilingual Quality** | `multilingual` | Translation, cross-language prompts |

---

## Question Bank

The Question Bank (📚 chip row below the prompt box) contains 32 categorised test prompts:

| Category | Count | Examples |
|----------|-------|---------|
| **Ops** | 5 | Hospital occupancy, free beds, triage summary |
| **Emergency** | 6 | Chest pain triage, sepsis-3/qSOFA, anaphylaxis protocol |
| **Cardiology** | 5 | ACS workup, warfarin management, CPR algorithm |
| **Coding** | 6 | Python binary search, SQL duplicates, REST API design |
| **Reasoning** | 5 | Bat-and-ball, logic puzzles, multi-step inference |
| **Multilingual** | 5 | Prompts in Romanian, German, Hungarian, French |

Click any chip to instantly load that prompt into the text box.

---

## Results Table

After a run, the results table shows 12 columns:

| Column | Description |
|--------|-------------|
| **Rank** | Sorted by `overall` judge score (highest = 1) |
| **Model** | Short model name (`.gguf` removed) |
| **TTFT (s)** | Time to first token in seconds |
| **Tokens/s** | Generation throughput |
| **RAM ↑ (MB)** | RAM consumed during inference |
| **Quality ★** | Overall judge score rendered as stars (0–5) |
| **Accuracy** | Judge sub-score |
| **Reasoning** | Judge sub-score |
| **Instruction** | Judge sub-score |
| **Safety** | Judge sub-score |
| **Response** | Truncated preview (click ▶ to expand full text) |
| **Actions** | Copy / Expand buttons |

### Metrics Summary Bar
Above the table shows the champions for the current run:
- ⚡ **Fastest TTFT** — model with lowest time to first token
- 🚀 **Best Tok/s** — highest throughput
- 💾 **Peak RAM** — highest RAM delta recorded
- ⭐ **Top Quality** — highest overall judge score

---

## Monkey Mode 🐒

Click **RANDOM** (the monkey button) to:
1. Pick a random subset of available models.
2. Select a random prompt from the Question Bank.
3. Pick a random judge template.
4. Run the comparison automatically.

Useful for unattended regression testing or discovering model performance across diverse prompts.

---

## Language Switcher

Click the **🇺🇸 EN ▾** flag button in the nav bar to switch language.

Supported languages: English · Hebrew (עברית) · Arabic (العربية) · Spanish · French · German.

RTL layout is applied automatically for Hebrew and Arabic.

---

## Dark Mode

Toggle via the **🌙 / ☀** button in the nav bar. Preference is saved to `localStorage`.

---

## Export CSV

After a run, click **Export CSV** to download all result columns as a CSV file.  
The filename includes the prompt (truncated) and a timestamp.

---

## Zena Chat Assistant

Zena is the built-in AI assistant (this app itself).

1. Click **Ask Zena** in the footer bar.
2. Select a local model from the dropdown (auto-selects the largest available).
3. Type a question and press **Enter** (Shift+Enter = newline).
4. Zena answers using the selected model through `/__chat`.

**Session history**: last 8 conversation turns are sent for context.  
**System prompt**: the full content of this HOW_TO_USE document, so Zena always knows the app.

---

## API Reference (for LLMs)

All endpoints accept/return JSON. CORS is restricted to `localhost` origins only (127.0.0.1 and localhost, any port). External origins are blocked.

### `GET /__health`
Returns server status.

```json
{ "ok": true, "ts": 1711123456.789 }
```

### `GET /__system-info`
Returns detected models, system RAM, GPU info, and build recommendations.

```json
{
  "cpu_brand": "AMD",
  "cpu_count": 8,
  "cpu_name": "AMD Ryzen 7 8845HS",
  "cpu_avx2": true,
  "cpu_avx512": false,
  "memory_gb": 31.3,
  "gpus": [{"name": "AMD Radeon 890M", "vendor": "AMD", "vram_gb": 8.0, "backend": "ROCm/Vulkan"}],
  "has_llama_cpp": true,
  "llama_cpp_version": "0.3.16",
  "recommended_build": {"build": "ROCm / Vulkan (AMD GPU)", "flag": "rocm", "pip": "...", "reason": "...", "note": "..."},
  "model_count": 5,
  "models": [{"name": "Llama-3.2-3B-Instruct-Q4_K_M.gguf", "path": "C:\\AI\\Models\\...", "size_gb": 1.9}],
  "timestamp": 1711123456.789
}
```

### `POST /__comparison/mixed`
Runs a full benchmark comparison. Returns ranked results with optional judge scores.

```json
{
  "prompt": "Explain the Ottawa Ankle Rules.",
  "system_prompt": "You are a helpful medical assistant.",
  "local_models": ["C:\\AI\\Models\\model-a.gguf", "C:\\AI\\Models\\model-b.gguf"],
  "online_models": [],
  "judge_model": "C:\\AI\\Models\\judge.gguf",
  "judge_system_prompt": "Rate the response 0-10...",
  "max_tokens": 512,
  "temperature": 0.7,
  "n_ctx": 4096,
  "top_p": 0.95,
  "repeat_penalty": 1.1,
  "inference_timeout": 300
}
```

Response:
```json
{
  "prompt": "...",
  "models_tested": 2,
  "responses": [
    {
      "model": "model-a",
      "model_path": "C:\\AI\\Models\\model-a.gguf",
      "response": "The Ottawa Ankle Rules...",
      "time_ms": 1234.5,
      "tokens": 87,
      "tokens_per_sec": 12.3,
      "ttft_ms": 456.7,
      "ram_delta_mb": 1200,
      "judge_score": 8.5,
      "quality_score": 8.5,
      "judge_detail": {"overall": 8.5, "accuracy": 8, "reasoning": 9, "bias_passes": 2}
    }
  ],
  "judge_model": "judge.gguf",
  "timestamp": 1711123456.789
}
```

### `POST /__comparison/stream`
SSE streaming version of comparison. Returns `text/event-stream` with events:
- `model_start` — model loading begins
- `token` — each generated token streamed individually
- `model_done` — model finished generating
- `judge_start` / `judge_done` — judge scoring phase
- `done` — all complete

### `GET /__config`
Returns server configuration constants.

```json
{
  "vk_devices": "0",
  "default_inference_timeout": 300,
  "max_inference_timeout": 1800,
  "max_prompt_tokens": 8192,
  "rate_limit": {"max_requests": 30, "window_sec": 60.0}
}
```

### `GET /__discover-models?q=llama&sort=trending&limit=30`
Searches HuggingFace for GGUF models. Cached for 15 minutes.

### `POST /__chat`
Single-turn or multi-turn chat with a local model.

```json
{
  "model_path": "C:\\AI\\Models\\mistral-7b.gguf",
  "system": "You are a helpful assistant.",
  "messages": [
    {"role": "user", "content": "How do I add a new model?"}
  ],
  "max_tokens": 512,
  "temperature": 0.4
}
```

Response:
```json
{ "response": "Drop a .gguf file into C:\\AI\\Models and click Scan..." }
```

### `POST /__download-model`
Start a background download. Returns a `job_id` immediately; poll `/__download-status?job_id=<id>` for progress.

```json
{ "model": "https://huggingface.co/TheBloke/Llama-2-7B-GGUF/resolve/main/llama-2-7b.Q4_K_M.gguf", "dest": "C:\\AI\\Models" }
```

Response:
```json
{ "ok": true, "job_id": "a1b2c3d4" }
```

### `POST /__install-llama`
Triggers `pip install llama-cpp-python` with GPU flags. Poll `/__install-status` for progress.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| No models found | Check `MODEL_DIRS` in `comparator_backend.py`; ensure `.gguf` files exist |
| Backend not responding | Make sure `Run_me.bat` is running; check port 8123 is not blocked |
| Judge returns `parse error` | Judge model too small or wrong template; try a larger judge |
| GPU not used | Install `llama-cpp-python` with CUDA: `pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121` |
| High RAM usage | Reduce `n_ctx` in `comparator_backend.py` or run fewer models in parallel |
| Zena chat slow | Use a smaller/quantised model (Q4_K_M recommended for chat) |

---

## Tips for LLMs Using This App

- To test the app: `POST /__comparison/mixed` with 2 models and a short prompt.
- Judge template names: `medical`, `general`, `coding`, `reasoning`, `multilingual`.
- All file paths must use the **server's** filesystem path (e.g., `C:\AI\Models\...`).
- The `messages` array in `/__chat` follows the OpenAI chat format (`role`: `user`/`assistant`/`system`).
- Token counts and RAM figures are approximate; `llama_cpp` reports them per-run.
