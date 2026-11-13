# Enhancement Plan — Zen LLM Compare

> **Date:** July 2026
> **Status:** Active planning
> **Total tests passing:** 406 (100 + 119 + 78 + 85 + 21 + 3 integration)

---

## 1. Current State Assessment

### What's Working (All Green)
- 406/406 automated tests pass (0 failures)
- All documented API endpoints functional
- CORS restricted to localhost ✅
- SSRF prevention on downloads ✅
- Rate limiting (30 req/min per IP) ✅
- Path traversal prevention ✅
- Judge dual-pass bias mitigation ✅
- SSE streaming endpoint ✅
- 6-layer judge score extraction ✅
- Hardware auto-detection (CPU/GPU/RAM) ✅
- Model scanning with incompatible-quant filtering ✅
- Token counting via tiktoken (not naive word split) ✅
- Dark mode, RTL, 6 languages ✅
- Batch mode, CSV export, ELO, History, Leaderboard ✅
- ConnectionAbortedError handling on all write paths ✅
- Daemon threads on all background workers ✅
- Loading spinners on Discover/Catalog/Download ✅
- 42 curated model catalog entries ✅
- 3 discovery sources: HuggingFace, Discover, ModelScope ✅

### Known Gaps (from LLM_COMPARE_2026.md spec)

| ID | Gap | Severity | Current State |
|----|-----|----------|---------------|
| G1 | NL regex extraction limited — "accuracy is: 7" not matched, only "accuracy: 7" | Low | Only affects fallback layer 4/5 |
| G2 | ~~README claims "100+ question bank" but actual count is 32~~ | Low | ✅ Fixed — README updated to 32 |
| G3 | ~~README lists wrong judge template names~~ | Low | ✅ Fixed — README & HOW_TO_USE updated |
| G4 | ~~`Rebuild_Prompt.md` says server binds `0.0.0.0`~~ | Low | ✅ Fixed — corrected to `127.0.0.1` |
| G5 | No persistent results / ELO across sessions beyond localStorage | Medium | localStorage works but is browser-only |
| G6 | ~~No per-model loading indicators during streaming~~ | Medium | ✅ Fixed — loading spinners added to Discover/Catalog/Download |
| G7 | ~~ConnectionAbortedError crashes on client disconnect~~ | High | ✅ Fixed — all write paths now catch connection errors |
| G8 | ~~GitHub tab in model browser non-functional~~ | Low | ✅ Fixed — removed, replaced by HuggingFace + Discover + ModelScope |

---

## 2. Research: Best Practices & New Approaches (2025-2026)

### 2.1 Academic — LLM-as-Judge Improvements

**Source: Zheng et al., NeurIPS 2023 + follow-up work 2024-2025**

| Technique | Description | Our Status |
|-----------|-------------|------------|
| Position randomization | Swap response order between passes | ✅ Implemented |
| Swap-and-average | Run judge twice with swapped positions, average scores | ✅ Implemented |
| Reference-guided judging | Provide a gold-standard answer for the judge to compare against | ❌ Not implemented |
| Multi-judge consensus | Use 2-3 different judge models and take majority vote | ❌ Not implemented |
| Calibration prompts | Pre-test the judge on known-quality responses to detect bias | ❌ Not implemented |

**Source: Meta MLGym (Feb 2025)**
- Frontier models can tune hyperparameters but not generate novel hypotheses
- Validates that task-specific evaluation (our approach) > abstract benchmarks

### 2.2 GitHub Trends (July 2026)

| Project | Stars | Relevance |
|---------|-------|-----------|
| Open WebUI | 127k+ | Chat-first, no comparison — not a competitor |
| llamafile | 23.8k | Zero-install philosophy — we share this DNA |
| lm-evaluation-harness | 11.7k | CLI academic benchmarks — different audience |
| vLLM | 48k | High-throughput inference server — potential backend upgrade |
| SGLang | 12k | Structured generation — could improve judge output reliability |
| koboldcpp | Popular | Run GGUF easily, KoboldAI UI — zero-install competitor |
| shimmy | New | Rust inference server, OpenAI-API compat, auto-discovery |
| cortex.cpp | Growing | Jan.ai's local API platform for GGUF/ONNX |
| node-llama-cpp | Growing | JSON schema enforcement on generation level — inspiration for E1 |
| llmfit | New | Find what runs on your hardware — model-fitting approach we already do |
| Colosseum | New | Multi-agent debate arena with cross-judging — validates our judge approach |

**Key insights:**
- **GGUF is the standard** — 403 public repos tagged `gguf` on GitHub, Ollama uses GGUF underneath
- **JSON schema enforcement** — node-llama-cpp enforces schemas at generation level (not post-hoc)
- **Contamination detection** — capbencher adds leakage alarms to benchmarks (we should consider)
- **Epistemic reliability** — ERR-EVAL tests hallucination handling (new evaluation dimension)
- **Size vs precision tradeoff** — size-precision-slm-bench found tiny models at FP16 can beat large INT4 models

### 2.3 r/LocalLLaMA Community Priorities (2025-2026)

1. **Structured output / JSON mode** — Users want guaranteed JSON from judges (SGLang, Outlines, llama.cpp grammar)
2. **MCP integration** — Model Context Protocol gaining universal adoption; expose comparison as MCP tools
3. **Multimodal** — Image understanding in Llama 4, Phi-4-vision; evaluation needs vision prompts
4. **Longer contexts** — 10M tokens in Llama 4 Scout; evaluation needs to handle this
5. **Speculative decoding** — 2-3x faster inference with draft models (llama.cpp supports it)
6. **GGUF metadata** — Parse model architecture, context length, quant type without loading (HF Hub has a viewer)
7. **Memory estimation** — hf-mem CLI can estimate inference memory — useful for model-fit scoring

### 2.4 HuggingFace Best Practices

- **GGUF metadata parsing**: `gguf` Python package (or `@huggingface/gguf` JS) can extract model metadata (context length, architecture, quant type) without loading the model — we should use this for better model cards
- **Hub API pagination**: Our discovery uses `limit=60` which may miss results; pagination improves coverage
- **Repo file listing**: `api.list_repo_files()` lets users pick specific quant variants from a repo
- **Quantization types matter**: HuggingFace documents 20+ quant types from F64 to IQ1_S. Our INCOMPATIBLE_QUANTS filter (IQ1_S, IQ1_M, IQ2_XXS, IQ2_XS, IQ2_S) is correct — these are too lossy for meaningful comparison
- **GGUF tensor viewer**: HuggingFace Hub has a built-in GGUF metadata/tensor viewer — we could link to it from model cards
- **MXFP4/NVFP4**: New quantization formats emerging (Intel auto-round supports them) — monitor for llama.cpp support

---

## 3. Cost/Benefit Analysis

### 3.1 Enhancement Costs (Developer Time Estimates)

| Enhancement | Effort | Lines Changed | Risk |
|-------------|--------|---------------|------|
| E1: Structured JSON output for judge | 2h | ~30 backend | Low |
| E2: GGUF metadata extraction | 3h | ~60 backend | Low |
| E3: Multi-judge consensus | 4h | ~100 backend + UI | Medium |
| E4: Reference-guided judging | 3h | ~50 backend + UI | Low |
| E5: Speculative decoding support | 2h | ~20 backend | Low |
| E6: Export to HTML/PNG report | 6h | ~200 frontend | Medium |
| E7: Model parameter presets | 2h | ~40 frontend | Low |
| E8: Question bank expansion (32→100) | 4h | ~300 frontend | Low |
| E9: WebSocket streaming (replace SSE POST hack) | 8h | ~300 both | High |
| E10: Persistent SQLite results DB | 6h | ~200 backend | Medium |

### 3.2 Benefit Scoring

| Enhancement | User Impact (0-10) | Competitive Edge (0-10) | Effort/Benefit |
|-------------|--------------------|-------------------------|----------------|
| E1: Structured JSON | 3 | 5 | High benefit/low cost |
| E2: GGUF metadata | 6 | 7 | High benefit/low cost |
| E3: Multi-judge | 5 | 9 | Good benefit/med cost |
| E4: Reference-guided | 4 | 7 | Good benefit/low cost |
| E5: Speculative decode | 7 | 6 | High benefit/low cost |
| E6: HTML/PNG report | 8 | 8 | Good benefit/med cost |
| E7: Model presets | 6 | 3 | Good benefit/low cost |
| E8: Question bank 100 | 5 | 4 | Medium benefit/low cost |
| E9: WebSocket streaming | 4 | 3 | Low benefit/high cost |
| E10: SQLite results DB | 7 | 6 | Good benefit/med cost |

### 3.3 Infrastructure Costs

| Resource | Current Cost | Notes |
|----------|-------------|-------|
| Hosting | $0 | Self-hosted, local only |
| Dependencies | $0 | All open-source, no API keys needed |
| GPU | User's own hardware | No cloud compute |
| Storage | ~500MB-50GB | Depends on model count |
| Bandwidth | Model downloads only | HuggingFace CDN (free) |

**Total operational cost: $0/month.** This is a key competitive advantage.

---

## 4. Enhancement Plan — Prioritised To-Do List

### Phase 1: Quick Wins (1 week)

- [ ] **E1: Structured JSON output for judge**
  - Add `response_format={"type": "json_object"}` to judge `create_chat_completion()` call
  - Fallback to current 5-layer extraction if model doesn't support JSON mode
  - File: [comparator_backend.py](comparator_backend.py) `_run_judge()` method

- [ ] **E2: GGUF metadata extraction**
  - `pip install gguf` — parse model metadata (architecture, context length, quant type)
  - Show in model cards: max context, parameter count, quant method
  - Update `scan_models()` to include metadata
  - File: [comparator_backend.py](comparator_backend.py) `scan_models()` function

- [ ] **E5: Speculative decoding support**
  - Add `draft_model` parameter to comparison payload
  - llama-cpp-python supports `draft_model_path` kwarg in `Llama()` constructor
  - File: [comparator_backend.py](comparator_backend.py) `_run_local_comparisons()`

- [ ] **E7: Model parameter presets**
  - Use `_MODEL_CATALOG` preset values (`params_preset`) to auto-set temperature/max_tokens per model
  - File: [model_comparator.html](model_comparator.html) `runComparison()` function

- [ ] **Fix G2: Update README question bank count**
  - Change "100+" to "32" in README.md
  - File: [README.md](README.md)

- [ ] **Fix G3: Align README judge template names with code**
  - Update README to list actual template names: medical, general, coding, reasoning, multilingual
  - File: [README.md](README.md)

### Phase 2: Core Improvements (2-3 weeks)

- [ ] **E4: Reference-guided judging**
  - Add optional `reference_answer` field to comparison payload
  - Include reference in judge prompt: "Compare the response to this reference answer: ..."
  - File: [comparator_backend.py](comparator_backend.py) `_run_judge()`
  - File: [model_comparator.html](model_comparator.html) add Reference Answer textarea

- [ ] **E6: Shareable HTML/PNG report**
  - Generate standalone HTML report from comparison results
  - Include `html2canvas` (CDN) for PNG screenshot of results table
  - Add "Share" button that copies shareable link or downloads report
  - File: [model_comparator.html](model_comparator.html) `_shareReport()` enhancement
  
- [ ] **E8: Expand question bank to 100 prompts**
  - Add prompts for: Legal, Finance, Education, Science, Creative Writing, Summarization
  - Source from MT-Bench (80 questions) + community favorites
  - File: [model_comparator.html](model_comparator.html) `_QUESTION_BANK` object

- [ ] **E10: SQLite persistent results database**
  - Replace localStorage with SQLite via backend endpoint
  - `POST /__results/save` / `GET /__results/history`
  - ELO computed server-side for accuracy
  - File: [comparator_backend.py](comparator_backend.py) new module

### Phase 3: Advanced Features (1-2 months)

- [ ] **E3: Multi-judge consensus**
  - Allow selecting 2-3 judge models
  - Run each judge independently, report consensus + disagreement
  - Aggregate via majority voting or average with outlier detection
  - Files: [comparator_backend.py](comparator_backend.py) + [model_comparator.html](model_comparator.html)

- [ ] **E11: Automated benchmark suite**
  - Pre-built benchmark sets (like MT-Bench) that run all 80 prompts automatically
  - Generate a full evaluation report comparing N models across all prompts
  - Store results in SQLite (E10 prerequisite)

- [ ] **E12: Model comparison leaderboard export**
  - Export ELO leaderboard as CSV, JSON, or shareable web page
  - Include confidence intervals based on number of comparisons

- [ ] **E13: Smart model recommendations**
  - Based on available RAM + VRAM, recommend which models to download
  - Show "fits in memory" badge per model in discovery results
  - Use GGUF metadata (E2 prerequisite) for accurate sizing

### Phase 4: Future Vision

- [ ] **Multimodal support** — image + text prompts when llama.cpp supports it
- [ ] **MCP server mode** — expose comparison as MCP tools for Claude/Copilot
- [ ] **Headless CI mode** — run benchmarks from CLI, output results as JSON/JUnit XML
- [ ] **Plugin system** — custom judge templates, custom score extractors
- [ ] **E14: Epistemic reliability scoring** — test how well models handle uncertainty, avoid hallucinations (inspired by ERR-EVAL)
- [ ] **E15: GGUF metadata viewer** — link to HuggingFace's built-in tensor viewer from model cards, show architecture/context/quant details
- [ ] **E16: Memory estimation** — integrate hf-mem-style memory prediction to show "will this model fit?" before download
- [ ] **E17: Contamination detection** — flag models that score suspiciously well on common benchmarks (inspired by capbencher)

---

## 5. NL Regex Extraction Improvement (Fix G1)

Current patterns only match `accuracy: 7` but not `accuracy is: 7`. Improved patterns:

```python
# Current (limited):
(r"accuracy[\s:]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", "accuracy"),

# Improved (handles "accuracy is 7", "accuracy is: 7", "accuracy = 7"):
(r"accuracy[\s:=]+(?:is[\s:]+)?(\d+(?:\.\d+)?)\s*(?:/\s*10|out\s+of\s+10)?", "accuracy"),
```

This is a low-priority fix as it only affects fallback layers 4-5 (most judges output JSON).

---

## 6. Process Hygiene — Audit Results (July 2026)

A comprehensive zombie process investigation found:
- **PID 18928** — Main backend server on port 8123 (legitimate, parent=cmd)
- **PID 19832** — Orphan test server on port 8124 (zombie from earlier manual debug session, NOT from app or tests)
- **7 VS Code extension LSP servers** (isort, autopep8) — all legitimate

**Root cause:** The orphan on port 8124 was manually started during a debug session (`python comparator_backend.py 8124`) and its parent terminal was closed without stopping it.

**Preventive measures verified:**
1. `ThreadingHTTPServer` uses `daemon_threads = True` — child threads die with main process ✅
2. All download/install worker threads use `daemon=True` — die when backend exits ✅
3. Cache warmup thread uses `daemon=True` — dies when backend exits ✅
4. Test servers use `daemon=True` threads — die when pytest exits ✅
5. `Run_me.bat` cleans up port 8123 on start ✅
6. 21 process hygiene tests validate all of the above ✅

**No code changes needed** — the app's process management is correct.

---

## 7. Test Commands

```bash
# Run all tests (406 tests, ~30 seconds)
python -m pytest tests/ -v --tb=short

# Run specific suites
python -m pytest tests/test_bug_fixes.py -v                # 100 tests
python -m pytest tests/test_xray_comprehensive.py -v       # 119 tests
python -m pytest tests/test_completeness_audit.py -v       #  78 tests
python -m pytest tests/test_discovery_install.py -v        #  85 tests
python -m pytest tests/test_zombie_and_process_audit.py -v #  21 tests

# Run with timing
python -m pytest tests/ -v --durations=10

# Run integration test (requires GGUF model on disk)
python tests/test_llm_integration.py

# Lint
ruff check .

# Security scan
bandit -r comparator_backend.py
```
