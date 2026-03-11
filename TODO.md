# LLM_TEST_BED — TODO

> Last updated: 2026-03-12

---

## ✅ Completed (this session)

### Swarm Server — Adaptive FIFO Buffers & Admission Control
_Ported from Local_LLM's `AdaptiveFIFOBuffer` architecture_

- [x] **Wire `_request_buffer` into request pipeline** — Added `_admit_request()` / `_release_request()` gating functions. All 4 inference entry points now go through the buffer:
  - `_handle_post_chat_stream` (SSE streaming chat)
  - `_post_chat` (non-streaming chat)
  - `_handle_v1_chat_completions` (OpenAI-compatible)
  - `_handle_v1_completions` (legacy completions)
  - When buffer is full → **503 "Server busy"** instead of unbounded queueing

- [x] **Add `_response_buffer`** — `ThreadSafeFIFOBuffer(initial=20, max=100, backpressure=False)` for streaming output coordination. `_query_with_retry()` publishes completed inference results (token count, latency, source) to this buffer. Both `/health` and `/__admin/data` report its stats.

- [x] **Add logging to buffer operations** — `logging.getLogger("zenai.server")`. Buffer grow/shrink events logged with old→new sizes. Backpressure timeouts and retry attempts logged with details.

- [x] **Add `BackpressureTimeoutError`** — New exception class. `put()` now accepts `raise_on_timeout=True` — raises `BackpressureTimeoutError` instead of returning `False`.

- [x] **Update README documentation** — Added "Adaptive FIFO Buffers & Admission Control" section to `README_with_swarm.md` with architecture diagram, adaptive sizing table, and observability JSON examples.

- [x] **Fix critical bug: `conv_key` used before assignment** — In `_handle_post_chat_stream`, `conv_key` was referenced in `_admit_request()` before being defined. Moved `conv_key = badge or ip` above the admission check.

### Earlier Session Fixes
- [x] `_LlamaServerEngine.query()` — accept `messages` parameter for multi-turn
- [x] `server_is_up()` health check — changed from `/login.html` (404) to `/__swarm/status`
- [x] `get_llm_engine(blocking=False)` — non-blocking for request handlers
- [x] Test timeouts — `post_chat()` catches `TimeoutError`/`URLError` → synthetic 504
- [x] All `list[str]` + `pop(0)` replaced with `deque` + `popleft()` (O(1))
- [x] `_ConversationStore` — TTL-based session expiration (30 min), max 128 sessions with LRU eviction
- [x] `InferenceMetrics` — tracks total requests, errors, tokens, rolling p95 latency
- [x] Chunk validation in `_LlamaServerEngine.query()` — validates SSE chunk structure

### Test & Stability Fixes (2026-03-12)
- [x] **Add CORS header to GET responses** — `_send_json_bytes` was missing `Access-Control-Allow-Origin: *`, causing browser test failures.
- [x] **Add `bridge`/`engine` fields to `/__swarm/status`** — Browser tests expected these; added to `swarm_bridge.status()`.
- [x] **Return 400 (not 500) for bad input types** — Monkey tests send `{max_tokens: "many"}` etc. `ValueError`/`TypeError`/`KeyError` now → 400.
- [x] **Fix `test-chat.js` orphaned `it()` block** — Moved stray assertion inside its `describe()` group; fixed `gr` → `_gr()`.
- [x] **Create `browser_sim.py`** — 67-test Python simulator of all browser JS test suites; runs from terminal.
- [x] **Create `test-runner.js`** — Minimal async browser test framework (`describe`, `it`, asserts, `TestRunner.runAll()`).
- [x] **Create `zen_config.py`** — Centralized config replacing `paths.py`; default port 8777.
- [x] **Update `test_runall.py` port** — Hardcoded 8787 → env-var `TEST_BASE_URL` with 8777 default.
- [x] **Add `.gitignore` entries** — Added `target/`, `Cargo.lock`, `patches/`, `node_modules/`, `desktop.ini`, `*.db`.

---

## 🔲 TODO — Swarm Server

### High Priority
- [ ] **Replace remaining `print()` calls with `logger`** — X_Ray found 10+ print statements in `_start_llama_server`, `_do_switch_model`, `get_llm_engine`, `_find_default_model`. Should use `logger.info()` / `logger.warning()`.
- [ ] **Add type annotations to public functions** — X_Ray flagged `query()`, `get_llm_engine()`, `scan_gguf_models()`, `record_hit()`, `do_GET()`, `do_POST()` as missing type hints.
- [ ] **Split `do_GET()` — cyclomatic complexity 19** (limit: 16). Use a route dispatch dict like `do_POST()` already does.
- [ ] **Split `ZenHandler` class — 525 lines** (limit: 500). Extract admin endpoints and model management into separate modules or mixins.
- [ ] **Extract magic numbers into constants** — `_handle_v1_completions` has 11, `_handle_post_chat_stream` has 14 (temperature, top_p, timeouts, etc.).

### Medium Priority
- [ ] **Add `__all__` to server modules** — X_Ray flagged `server_with_swarm.py` (22 public names), `swarm_bridge.py` (20 public names), `mobile-test.py` (3 public names) as missing `__all__`.
- [ ] **Pin HuggingFace downloads to revision hash** — Bandit B615: `hf_hub_download()` without revision pinning.
- [x] **Add `.gitignore`** — X_Ray health check: missing .gitignore (weight: 15). ✅ Done
- [ ] **Add `pyproject.toml`** — X_Ray health check: missing package manifest (weight: 10).
- [ ] **Add CHANGELOG.md** — X_Ray health check: missing changelog (weight: 5).
- [ ] **Wire `_response_buffer` into consumer pattern** — Currently the response buffer collects completed inference summaries but nothing reads/drains them. Could feed a metrics dashboard or alerting system.

### Low Priority / Nice-to-Have
- [ ] **Add docstrings** to `do_GET()`, `do_POST()`, `record_hit()`, `draw_icon()`, `ZenHandler` class.
- [ ] **Investigate duplicate functions** — X_Ray found near-identical `get_ip()` (mobile-test.py:9 ↔ server_with_swarm.py:2844) and `handle_error()` (mobile-test.py:67 ↔ server_with_swarm.py:2831). Consider extracting shared utils.
- [ ] **`drain()` is never called** — X_Ray flagged as dead function. Either wire it into a periodic cleanup or add a comment explaining it's an API for external use.

---

## 📊 X_Ray Scan Results (2026-03-11)

| Metric | Value |
|--------|-------|
| **Overall Grade** | **B (85.2/100)** |
| Code Smells | 73 (0 critical, 15 warning, 58 info) |
| Duplicates | 3 groups |
| Ruff Format | 4 files need formatting |
| Ruff Lint | 2 issues (1 critical → FIXED) |
| Bandit Security | 13 issues (0 high, 7 medium, 6 low) |
| Web Smells (JS) | 21 warnings (13 deep-nesting, 4 long-function, 3 large-file) |
| Project Health | 30/100 (3/10 checks passed) |
| Pyright Typecheck | 7 errors |
| Release Readiness | NO-GO (8 critical issues across smells/lint/typecheck) |
