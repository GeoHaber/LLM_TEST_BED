# Enhancement Plan — Zen LLM Compare

## Cost / Benefit Analysis + Prioritised Backlog

Legend: **Effort** = engineering days · **Impact** = user-visible value (High/Med/Low)

---

## Bugs Fixed (already done, session 2025-06)

| # | File | Fix |
|---|------|-----|
| B1 | `comparator_backend.py` | Judge path used unsanitised `local_models` → changed to `safe_models` |
| B2 | `comparator_backend.py` | Fallback judge prompt used `instruction_following(bool)` / `safety(string)` → fixed to 0-10 scale |
| B3 | `README.md` | Wrong model dir `C:\AI\Models` → corrected to `~/AI/Models` |
| B4 | `HOW_TO_USE.md` | Wrong question bank count "100+" → corrected to 32 |

---

## Enhancement Backlog

### Priority 1 — High Impact, Low Effort

#### E1 · Parallel Model Inference  
**Effort:** 2 days · **Impact:** High · **Risk:** Medium

**Problem:** `_run_local_comparisons()` iterates models sequentially. With 6
models the user waits 6× longer than necessary.  
**Spec says:** *"All selected models run simultaneously in background threads."*

**Solution:**
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _run_local_comparisons(self, paths, prompt, params) -> list:
    with ThreadPoolExecutor(max_workers=min(len(paths), 4)) as pool:
        futures = {pool.submit(self._run_one_model, p, prompt, params): p
                   for p in paths}
        return [f.result() for f in as_completed(futures)]
```

**Constraints:**
- Each model fully loads into RAM; with N models, RAM usage is N×model_size.
- `max_workers=4` is a reasonable cap before RAM exhaustion on typical hardware.
- The SSE stream handler needs to emit events in arrival order using
  `queue.Queue` shared between worker threads and the SSE writer thread.

**Benefit:** Wall-clock comparison time drops from O(N) to O(1) for N models.  
**Cost:** ~2 days engineering + regression testing.

---

#### E2 · Chat Model Session Cache  
**Effort:** 1 day · **Impact:** High · **Risk:** Low

**Problem:** `_handle_chat()` creates a new `llama_cpp.Llama()` instance for
every single message. Each load takes 1–10 s. A 10-turn conversation loads
the model 10 times.

**Solution:** An LRU cache keyed by `(model_path, n_ctx)`:
```python
from functools import lru_cache

@lru_cache(maxsize=2)
def _get_chat_model(model_path: str, n_ctx: int):
    return llama_cpp.Llama(model_path=model_path, n_ctx=n_ctx, verbose=False)
```

**Constraints:** Cache must be invalidated when the model file changes (mtime check).  
**Benefit:** Chat feels instant after first load.  
**Cost:** ~1 day + thread-safety review.

---

#### E3 · Question Bank Expansion to 100+ Prompts  
**Effort:** 0.5 days · **Impact:** Medium · **Risk:** Low

**Problem:** HOW_TO_USE originally (incorrectly) promised 100+ prompts — only
32 exist. The spec accuracy issue has been fixed in the docs, but the actual
coverage is limited.

**Where to add:** `model_comparator.html` → `_QUESTION_BANK` array.

**Proposed additions per category (from 32 → 107):**
- `emergency` 5 → 20: add trauma, toxicology, paediatric emergencies
- `cardiology` 6 → 20: add STEMI differentials, heart failure staging
- `coding` 6 → 20: add Rust, SQL, debugging, security review
- `reasoning` 5 → 20: add counterfactuals, maths proofs, logic puzzles
- `multilingual` 5 → 12: add Mandarin, Japanese, Portuguese prompts
- `ops` 5 → 15: add RCA templates, incident response, SRE scenarios

**Benefit:** Covers phrasing the model catalog has been verified on.  
**Cost:** 0.5 day. No backend changes needed.

---

### Priority 2 — Medium Impact, Medium Effort

#### E4 · WebSocket Upgrade for Streaming  
**Effort:** 4 days · **Impact:** Medium · **Risk:** Medium

**Problem:** Current SSE streaming is one-directional and stateless. Each
comparison opens a new HTTP connection; the server cannot push updates if the
initial POST is not pending.

**Solution:** Replace `/__comparison/stream` with a WebSocket endpoint using
the standard library `websockets` package (or upgrade stdlib only via `wsproto`).

**Benefit:** Enables progress cancellation, connection resumption, and
multi-session monitoring.  
**Cost:** 4 days; frontend and backend changes; new dependency.

---

#### E5 · MCP Server Integration  
**Effort:** 3 days · **Impact:** High (strategic) · **Risk:** Low

**Problem:** No way for external agents or VS Code Copilot to invoke comparisons
programmatically.

**Solution:** Expose an MCP server layer on port 8124 with two tools:
- `compare_models(prompt, models, judge)` → returns scored results
- `list_local_models()` → returns current model inventory

**Benefit:** VS Code Copilot and other agents can run model comparisons without
launching a browser.  
**Cost:** 3 days; requires `mcp` Python SDK.

---

#### E6 · Docker / Container Deployment  
**Effort:** 2 days · **Impact:** Medium · **Risk:** Low

**Problem:** Setup requires Python + llama-cpp-python compilation which fails
frequently on Windows without the right build tools.

**Solution:**
```dockerfile
FROM python:3.12-slim
RUN apt-get install -y cmake g++ && pip install llama-cpp-python psutil huggingface-hub tiktoken
COPY comparator_backend.py model_comparator.html ./
EXPOSE 8123
CMD ["python", "comparator_backend.py"]
```

**Benefit:** One-command startup on any platform.  
**Cost:** 2 days. Adds Docker dependency for users who want it.

---

#### E7 · Persistent Model Ratings (SQLite)  
**Effort:** 2 days · **Impact:** Medium · **Risk:** Low

**Problem:** ELO scores and run history live in `localStorage`—cleared if the
user opens the app in a different browser or clears browser data.

**Solution:** Add a `/__elo` GET/POST endpoint backed by a SQLite file
(`zenai_ratings.db`) in the model directory. Frontend syncs on load and after
each run.

**Benefit:** Ratings survive browser resets; can be exported for analysis.  
**Cost:** 2 days. New backend endpoint; no new dependencies (sqlite3 is stdlib).

---

### Priority 3 — Nice to Have

#### E8 · Batch Mode Auto-Retry on Timeout  
**Effort:** 0.5 days · **Impact:** Low · **Risk:** Low

When a model times out in batch mode, the current UI marks it as error and
continues. Add: automatic retry with halved `max_tokens` before marking error.

#### E9 · System Prompt Editor  
**Effort:** 1 day · **Impact:** Medium · **Risk:** Low

Let the user set a custom system prompt per run (e.g., "You are a terse doctor.")
and store it as a named preset in localStorage.

#### E10 · Per-Run Resource Graph  
**Effort:** 2 days · **Impact:** Low · **Risk:** Low

Show a simple SVG RAM / tokens-per-second sparkline per model card, rendered
from the `ram_delta_mb` and `tps` series captured during streaming.

#### E11 · Quantization Advisor  
**Effort:** 1 day · **Impact:** Medium · **Risk:** Low

Based on `get_memory_gb()` and GPU VRAM, recommend the largest quantization
(Q4_K_M, Q6_K, Q8_0) that will fit. Surface as a badge on each model row.

#### E12 · Gemma / Non-System-Role Model Compat.  
**Effort:** 1 day · **Impact:** Medium · **Risk:** Low

`gemma-2-9b-it` and similar models reject the `system` role. Detect this
(check GGUF metadata or catch `ValueError`) and transparently prepend the
system prompt to the first `user` message.

---

## Implementation Sequence (recommended)

```
Week 1: E2 (chat cache) + E12 (Gemma compat)  — quick wins
Week 2: E1 (parallel inference)                — biggest user impact
Week 3: E3 (question bank) + E8 (batch retry)  — low risk, high coverage
Week 4: E5 (MCP server)                        — strategic
Month 2: E6 (Docker) + E7 (SQLite ELO)
Month 3: E4 (WebSocket), E9, E10, E11
```

---

## Metric Targets After E1 + E2

| Metric | Current | Target |
|--------|---------|--------|
| Wall-clock 6-model comparison | ~6× slowest model | ≤ 1.5× slowest model |
| Chat first-response (2nd message) | 1–10 s model load | < 50 ms (cache hit) |
| Test suite (fast runs) | 437 collected | 450+ with E12 regression |
| Question bank count | 32 | 107 |

---

## Risk Register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Parallel models exhaust RAM | Medium | Cap `max_workers = 4`; check free RAM before spawn |
| llama-cpp thread unsafety | Low | Each worker gets its own `Llama()` instance |
| Chat cache returns stale model | Low | Invalidate on file mtime change |
| MCP port conflict | Low | Use `ZENAI_MCP_PORT` env var |
| Gemma compat breaks other models | Low | Only apply if `ChatFormatNotFoundError` raised |
