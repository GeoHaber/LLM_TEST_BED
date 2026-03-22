# Zen LLM Compare

[![GitHub Stars](https://img.shields.io/github/stars/GeoHaberC/Zen_LLM_Compare?style=social)](https://github.com/GeoHaberC/Zen_LLM_Compare/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/GeoHaberC/Zen_LLM_Compare?style=social)](https://github.com/GeoHaberC/Zen_LLM_Compare/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/GeoHaberC/Zen_LLM_Compare)](https://github.com/GeoHaberC/Zen_LLM_Compare/issues)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)

> **Run the same prompt through multiple local LLMs simultaneously — speed, quality, and RAM, ranked side-by-side.**

Zen LLM Compare is a self-hosted, zero-cloud benchmarking tool for local GGUF language models.  
Send any prompt to 1–8 models at once, score every response with a configurable **LLM judge**, and get a ranked results table with per-model metrics in seconds.

---

## ✨ Features

- **Side-by-side inference** — up to 8 local GGUF models run in parallel threads
- **LLM-as-judge scoring** — a separate local model grades every response on accuracy, reasoning, instruction-following, and safety (0–10)
- **5 judge templates** — Medical/Clinical · General Assistant · Code Quality · Reasoning/Math · Multilingual
- **32 question bank** — categorised test prompts across 6 categories (ops, emergency, cardiology, coding, reasoning, multilingual)
- **Live performance metrics** — TTFT, tokens/s, RAM delta, total time
- **Catalog + Hugging Face discovery** — 42 curated download cards plus live GGUF search with source, trust, fit, and preset hints
- **ModelScope discovery** — alternative model source alongside HuggingFace
- **Monkey Mode 🐒** — randomised model + prompt + judge for unattended regression runs
- **Zena AI assistant** — built-in chat powered by any of your local models
- **No build step** — single HTML file + one Python file, pure stdlib backend
- **Dark mode** · **RTL languages** (Hebrew, Arabic) · **CSV export**

---

## 🚀 Quick Start

### Windows — one click
Double-click **`Run_me.bat`**. It starts the backend and opens the UI automatically.

### Manual

```bash
# Install Python dependencies (once)
pip install -r requirements.txt

# Start the backend (serves UI + API on port 8123)
python comparator_backend.py

# Open in browser
# http://127.0.0.1:8123
```

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `llama-cpp-python` | Local GGUF model inference |
| `psutil` | RAM / CPU monitoring |
| `huggingface_hub` | Model downloads |

```bash
pip install -r requirements.txt
```

`llama-cpp-python` can also be installed directly from the UI (Settings → Install llama.cpp).

---

## 🗂️ Model Storage

Drop `.gguf` files into **`C:\AI\Models`** — they are auto-detected on startup and after each download.  
The backend also checks `%USERPROFILE%\\AI\\Models`, a repo-local `models/` folder, and `ZENAI_MODEL_DIR` when set.

---

## 🏗️ Architecture

```
Browser  ─────────────────────────────────────────
  model_comparator.html  (single-file SPA)
         │  HTTP  │
         ▼        ▼
comparator_backend.py  :8123
         │
         ├── GET  /                    serve the HTML app
         ├── GET  /__system-info       hardware scan + model list
         ├── GET  /__discover-models   live Hugging Face GGUF search
         ├── POST /__comparison/mixed  parallel inference + judge scoring
         ├── POST /__comparison/stream SSE streaming comparison
         ├── POST /__chat              Zena assistant chat
         ├── POST /__download-model    fetch GGUF from URL or repo path
         ├── GET  /__download-status   download progress
         ├── POST /__install-llama     pip install llama_cpp
         └── GET  /__install-status    install progress
```

- **Frontend** — vanilla JS, Tailwind CSS (CDN), no framework, no build step
- **Backend** — Python `ThreadingHTTPServer` (stdlib only + `llama_cpp`)
- **Judge** — same backend, different model instance; fires after all comparisons complete

---

## 🖥️ Usage

1. **Start** — run `Run_me.bat` or `python comparator_backend.py`
2. **Select models** — tick checkboxes in the left panel (auto-scanned from `C:\AI\Models`)
3. **Enter a prompt** — or pick one from the 📚 Question Bank
4. **Choose a judge template** and a **judge model**
5. **Click RUN** — results appear as each model finishes; judge scores follow

### Results table (12 columns)
`Rank · Model · TTFT · Tokens/s · RAM ↑ · Quality ★ · Accuracy · Reasoning · Instruction · Safety · Response · Actions`

---

## 📊 Judge Templates

| Template | Value | Best for |
|----------|-------|----------|
| Medical / Clinical | `medical` | Emergency care, triage, clinical decisions |
| General Assistant | `general` | General-purpose Q&A, instruction following |
| Code Quality | `coding` | Programming, debugging, code review |
| Reasoning / Math | `reasoning` | Logic, math, multi-step reasoning |
| Multilingual Quality | `multilingual` | Translation, cross-language prompts |

All templates output a unified JSON schema: `overall · accuracy · reasoning · instruction · safety · explanation`

---

## 📁 File Structure

| File | Role |
|---|---|
| `model_comparator.html` | Complete single-file SPA — UI, CSS, JS |
| `comparator_backend.py` | Python HTTP API — hardware scan, inference, judge, discovery, downloads |
| `_patch_catalog.py` | Utility: update MODEL_CATALOG in HTML |
| `requirements.txt` | Python dependencies |
| `Run_me.bat` | One-click Windows launcher |
| `HOW_TO_USE.md` | Full user guide (also used as Zena's system prompt) |
| `LLM_COMPARE_2026.md` | Landscape analysis / competitive research |
| `Enhance_plan.md` | Enhancement roadmap with cost/benefit analysis |
| `Rebuild_Prompt.md` | Step-by-step rebuild prompt for LLMs |
| `tests/test_discovery_install.py` | Discovery / install / hardware / model-card backend tests |
| `tests/test_zombie_and_process_audit.py` | Process hygiene, daemon threads, connection errors, port binding |
| `tests/test_llm_integration.py` | Live inference integration test (requires GGUF model) |

---

## ⚙️ Configuration

| Setting | Where to change |
|---|---|
| Backend port (default `8123`) | Top of `comparator_backend.py` · `const BACKEND` in the HTML |
| Model scan directories | `ComparatorHandler.model_dirs` defaults + `ZENAI_MODEL_DIR` |
| Judge system prompt | Judge Template dropdown in the UI |

---

## 🧪 Tests

```bash
# Run all 406 Python tests (~30 seconds)
pytest tests/ -v --tb=short

# Individual suites
pytest tests/test_bug_fixes.py -v                # 100 tests
pytest tests/test_xray_comprehensive.py -v       # 119 tests
pytest tests/test_completeness_audit.py -v       #  78 tests
pytest tests/test_discovery_install.py -v        #  85 tests
pytest tests/test_zombie_and_process_audit.py -v #  21 tests

# Live inference test (requires GGUF model on disk)
python tests/test_llm_integration.py              #   3 tests
```

Open `tests/test_comparator.html` in a browser for the JS / DOM suite, including catalog-card and discovery-card rendering checks.

---

## 📄 License

MIT — see [LICENSE](LICENSE)
