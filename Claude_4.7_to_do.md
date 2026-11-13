# Claude 4.7 — Audit TODO (LLM_TestBed)

## Overview
Zen LLM Compare / LLM_TestBed — self-hosted side-by-side local GGUF LLM comparator. Backend is a stdlib `ThreadingHTTPServer` (`comparator_backend.py`, ~3k lines) + single-page SPA (`model_comparator.html` + `app.js`, ~6k JS lines combined). Optional skill optimizer (`skill_optimizer.py`, `adk_optimizer.py`), ELO/judge DB (`zen_results.db`), and a test suite of ~400 pytest cases under `tests/`.
Architecture is coherent and mostly wired end-to-end; issues are concentrated in stale docs, un-declared third-party imports, dead utility modules, and a large number of `innerHTML` sinks on the SPA side.
Scanner run 2026-04-18 on 30 files / 86 rules.

## X-Ray findings summary
- Total findings: **167** (HIGH: 29, MEDIUM: 138, no CRITICAL).
- Files scanned: 30. Rules checked: 86.
- Top rules: `QUAL-011` bare-ish `except Exception: pass` (28), `JS-SEC-001` innerHTML=... (24), `TEST-003` test-style (24), `SEC-015` undeclared import (23), `JS-QUAL-004` loose equality (13), `PY-006` `global` (10).
- Top 5 highest-signal individual findings:
  1. `SEC-015` `adk_optimizer.py:20` — `from google.adk.agents import Agent` (pkg **not in `requirements.txt`/`pyproject.toml`**; also `google.adk.runners`, `google.adk.sessions`, `google.genai` at lines 21–23, and `pydantic` at line 18).
  2. `JS-SEC-001` `app.js:435` / `app.js:437` — `zenaSel.innerHTML = _zOpts` built from scanned model paths. Not attacker-controlled today but a persistent XSS sink if discovery ever returns HF-supplied names untrimmed.
  3. `PY-005` `benchmark_14_models.py:29,157` — `json.loads(resp.read())` on raw HTTP response without size/content-type guard.
  4. `PORT-002` `tests/test_full_validation.py:1255` — hard-coded `C:\\AI\\...` Windows path makes the test non-portable on CI/Linux.
  5. `LLM-011` `comparator_backend.py:2980` — `subprocess.run(["taskkill", "/F", "/PID", str(pid)])` on a PID parsed from `netstat` output without validating the owner is actually a stale backend (can kill an unrelated user process that transiently owned :8123).

## Functionality
- `comparator_backend.py:1188–1189` — POST `/__skill-lab/regenerate` maps to `_handle_skill_lab_analyze` (comment "# same handler"). README/UI treat it as a distinct action; the handler branch on `data.action` is missing, so clicking "Regenerate Config" re-runs full analyze instead of the regen path. Confirm or split.
- `comparator_backend.py:1129–1197` — POST router has no `/__elo/reset` safety/auth beyond rate limit; button in UI wipes persistent ELO with a single call. Add a confirmation token or require a body field.
- `app.js:448` — UI hard-caps selections at **6** (`if (selectedModels.length >= 6) { showStatus('Max 6 models'...)`), but README (`README.md:42`, `README.md:68`) and `docs/HOW_TO_USE.md:15,:68` advertise "1–8 models". Either raise the cap to 8 or correct the docs.
- `comparator_backend.py:124` — `_MODEL_CACHE_SIZE` default is 8, matching docs — but the effective parallel dispatch limit is `ThreadPoolExecutor(max_workers=6)` (README line 143). Cache vs worker pool mismatch is the real reason the UI caps at 6; document this.
- `app.js` handlers `_evalAddRoute`, `_evalJudgeConversation`, `slAddEval`, `slAddScenario`, `slStartOptimization`, `switchTab`, etc. are defined **inside `model_comparator.html` inline scripts** (lines 1252–1944+), not in `app.js`. Works today but violates the README's "single-file SPA + one JS file" story and makes the eval/skill-lab tabs undiscoverable from `app.js` alone — consolidate into `app.js`.
- `comparator_backend.py:1167–1171` — `/__judge/conversation` and `/__judge/toolcall` handlers exist; no UI caller for `/__judge/toolcall` found in `app.js` / `model_comparator.html`. Orphan endpoint or missing UI.
- `comparator_backend.py:2829–2903` — `_scout_hf_trending` is wired (`/__scout`), but the "Scout" tab UI (`app.js:3495`) requires two endpoints (`/__scout` + `/__tool-ecosystem`) and fails entirely if either errors; currently falls back only to a "Retry" button with no per-source error messaging.

## Bugs & dead code
- `adk_optimizer.py:18–23` — imports `pydantic`, `google.adk.*`, `google.genai`; **none of these are in `requirements.txt` or `pyproject.toml`**. Importing this module on a clean install will `ImportError` immediately. Either add the deps or move the file under `archive/`.
- `adk_optimizer.py:18` — `from pydantic import BaseModel, Field` are imported but never used (dead import).
- `zen_eval.py` — unused imports: `Any` from `typing`, `JudgeResult`, `ToolCallExpectation` from `zen_core_libs.eval.judge`, and the `from __future__ import annotations` line has no accompanying use of string-forward-refs.
- `comparator_backend.py:53–57` — imports `_cpu_fingerprint`, `_save_fingerprint`, `_needs_rebuild` from `zen_core_libs.llm.native_build` but **none are referenced** after import. Only `ensure_native_build`, `build_status`, `load_fingerprint` are used.
- `comparator_backend.py:116,350,370,448,527,707,1474,1654,2984,2995,3000` — ≥11 `except Exception: pass` swallows, several around RAM sampling (`ram_before`/`ram_after` at 1474, 1507) where a failing `proc.memory_info()` silently reports `ram_delta=0` and degrades metrics without logging.
- `comparator_backend.py:2950–2986` — `_kill_zombies_on_port` kills any PID listening on the target port regardless of process name. On a user machine where a different dev tool temporarily binds :8123, this will terminate it without prompt.
- `comparator_backend.py:2714` — `urllib.request.urlretrieve(model, out_path, ...)` after `validate_download_url(model)`, but `validate_download_url` (line 792) is not shown to check redirect targets; open-redirect-to-internal-host SSRF risk remains.
- `models_scan_test/`, `models_scan_test_2/`, `test_download_temp/` — stale scratch directories with fake `.gguf` files at repo root; `test_download_temp/` is empty. Should live under `tests/tmp/` or be gitignored.
- `archive/ui/index.html:174,184` — innerHTML sinks in archived HTML still flagged by X-Ray; confirm `archive/` is excluded from release bundle or add `.xrayignore`.
- `_patch_catalog.py:393,401` — `sys.exit(1)` inside helper; if this file is ever imported (it is only meant to be CLI-run) it will terminate the importer. Gate with `if __name__ == '__main__':`.
- `comparator_backend.py:1189` — `elif self.path == "/__skill-lab/regenerate": self._handle_skill_lab_analyze(data)` — dead alias if UI doesn't use it; confirm or remove.
- `benchmark_14_models.py:288` — `open("benchmark_results.json", "w")` used without `with`, relies on GC for close. `test_comprehensive.py:809` same pattern with `open(txt, "w")`.

## UI / UX
- `app.js:448` says "Max 6 models" but `docs/HOW_TO_USE.md` and README both advertise 8. Either number is fine; **ship a single consistent story**.
- `app.js:2663` — hard-coded example prompt contains multi-line markdown/JSX; ensure the prompt textarea handles Shift-Enter vs Enter consistently (submit shortcut at `app.js:3279` could fire on Enter inside a code block).
- `app.js:174,474,…` (13× `JS-QUAL-004`) — mixed use of `== null` and `=== undefined`. Not a bug but inconsistent and trips the lint rule noisily.
- `model_comparator.html:1938` innerHTML sink writes a table built from judge-model output (`app.js:3797` etc.); if a local judge model echoes `<script>` from a user prompt, it renders. Wrap in `textContent` or sanitize.
- No visible `aria-label` on the RUN button or model checkboxes in the inline markup (check `model_comparator.html` line 1200–1500 markup); keyboard/screen-reader users get only position context.
- Download-status card shows both "Cancel" and "Delete" states (`app.js:2508–2509`) but the delete action is only offered on `isDone` — cancelled jobs stack forever.
- The "Zena" built-in assistant pulls its persona from `docs/HOW_TO_USE.md` (per README line 231); if that file is renamed/moved Zena silently loses her system prompt with no error surfaced.

## Documentation
- `README.md:272–283` — "X-Ray Scan (March 2026): Files scanned 12, Rules checked 42, HIGH 15, MEDIUM 44". **Actual fresh scan (2026-04-18)**: 30 files, 86 rules, 29 HIGH, 138 MEDIUM. Either re-run `python -m xray . --dry-run` and paste fresh numbers, or drop the section.
- `README.md:42,:68` and `docs/HOW_TO_USE.md:15,:68` — "up to 8 models in parallel" contradicts `app.js:448` (Max 6). Pick one.
- `README.md:254–263` — claims "406 Python tests" and gives per-file counts (`test_bug_fixes: 100`, `test_xray_comprehensive: 119`, `test_completeness_audit: 78`, `test_discovery_install: 85`, `test_zombie_and_process_audit: 21` → sum = **403**, not 406). Actual `def test_` counts match those per-file numbers — fix the total.
- `README.md:225–237` File Structure table is missing: `app.js`, `zen_eval.py`, `skill_optimizer.py`, `adk_optimizer.py`, `benchmark_14_models.py`, `stress_test_parallel.py`, `download_model.py`. These are non-trivial modules (~3.8k, ~0.9k, ~0.4k, ~0.4k, ~0.3k, ~0.2k, ~0.4k LOC) that should be listed or `__archived__`.
- `docs/HOW_TO_USE.md:28–33` lists only 5 endpoints; the backend exposes 30+. Either keep this intentionally minimal and say so, or link to the exhaustive list in README.
- `docs/Enhance_plan.md` + `docs/Enhancement_plan.md` — two near-identical filenames. Merge or delete one.
- `docs/imlementation_plan_2026_4_5.md` — typo in filename (`imlementation`). Rename.
- No `CONTRIBUTING.md`, no quickstart for Linux/macOS (README's Manual section implies cross-platform but `Run_me.bat` is Windows-only and `C:\AI\Models` is the advertised model dir).
- `requirements.txt:1` header "Auto-generated by x_ray_project v3.0.0" but `adk_optimizer.py`'s deps were missed — the auto-gen claim is stale.

## Enhancement backlog
- **S / High impact** — Add `pydantic`, `google-adk`, `google-genai` to `requirements.txt` (or move `adk_optimizer.py` to `archive/`). Reconciles a broken-on-fresh-install module.
- **S / High impact** — Sync README `Max models` (6 vs 8) and test total (403 vs 406) with code/reality.
- **S / Medium impact** — Refresh the "X-Ray Scan" table in `README.md:272` with the 2026-04-18 counts, or remove the section.
- **S / Medium impact** — Gate `_patch_catalog.py:393/:401 sys.exit(1)` behind `if __name__ == '__main__':`.
- **S / Low impact** — Delete `tests/_patch_catalog` references and `models_scan_test*/`, `test_download_temp/` scratch dirs; add to `.gitignore`.
- **M / High impact** — Move inline `<script>` blocks (lines 1252–2100+) out of `model_comparator.html` into `app.js` (or a new `skill_lab.js` / `eval.js`) so the "single JS file" README claim holds and the SPA is auditable by JS-only linters.
- **M / Medium impact** — Replace the ~24 `element.innerHTML = ...` sinks (`app.js:435,437,519,1073,1079,1124,2226,2230,2414,2421,2431,3305,3578,3590,3666,3797` and `model_comparator.html:1303,1418,1676,1789,1937,1938`) with a tiny `render(el, html)` helper that first `DOMPurify`-sanitises, or switch to `textContent` + `createElement` for the model/judge strings that could contain LLM-emitted HTML.
- **M / Medium impact** — `_kill_zombies_on_port` should verify the process name is `python*` and the cmdline contains `comparator_backend.py` before `taskkill /F`.
- **M / Medium impact** — Replace the ≥11 silent `except Exception: pass` in `comparator_backend.py` with a `log_and_continue(exc, context=...)` helper so RAM/metric failures leave a breadcrumb.
- **L / High impact** — Unify the skill-lab + eval tabs under a single front-end module and write an E2E test (the current Python suite barely touches these paths — only `test_skill_optimizer.py` exercises the backend logic).
- **L / Medium impact** — Cross-platform launcher: port `Run_me.bat` to a `run_me.py` that works on macOS/Linux and document `~/AI/Models` as an equally-supported default.

## Bugs X-Ray missed
Real issues the 86 regex rules did **not** flag:
- **Pattern: "undeclared import flagged, but missing deps **not** added to `requirements.txt`."** X-Ray correctly fires `SEC-015` at `adk_optimizer.py:18–23`, but has no companion rule that cross-checks `requirements.txt`/`pyproject.toml` for presence. Example: `pydantic`, `google-adk`, `google-genai` are absent from `requirements.txt:1–15`, so the whole module is a runtime import error on a clean install. Consider adding rule `PKG-001: import not declared in pyproject/requirements`.
- **Pattern: "documentation contradicts code constants."** No rule for docs drift. `README.md:42` "up to 8 models" vs `app.js:448` `if (selectedModels.length >= 6)`. Same for `README.md:254` "406 tests" vs 403 actual `def test_`. A rule that grep's numbers/limits from README and matches them to source constants would catch this.
- **Pattern: "dead code path via alias handler."** `comparator_backend.py:1189` routes `/__skill-lab/regenerate` to `_handle_skill_lab_analyze` (same handler as `/__skill-lab/analyze` at line 1185), indicating the regenerate action was never implemented. No rule flags "two routes map to the same handler with no differentiator in the handler body."
- **Pattern: "innerHTML with LLM-emitted content."** `JS-SEC-001` flags all `innerHTML =` uniformly. It misses the critical subset: assignments where the RHS is `await fetch(/__chat|/__comparison/...)`-derived text. Example: `app.js:3797` and `model_comparator.html:1937` write judge/model output directly to `innerHTML`. A taint-aware rule (source = `fetch(... __chat|__comparison ...)` → sink = `.innerHTML =`) would rank these above the benign ones at `app.js:435/437`.
- **Pattern: "resource terminated without owner check."** `comparator_backend.py:2980` calls `taskkill /F /PID <pid>` parsed from `netstat` output. `LLM-011` fires on the subprocess call but not on the missing ownership validation (process name / cmdline). Add rule `PROC-001: kill-by-pid on externally-sourced PID without owner verification`.
- **Pattern: "import used only as a side-effect comment (`# noqa: F401 — used below`) but actually unused."** `stress_test_parallel.py:24` imports `InProcessAdapter` with an F401 excuse; ripgrep for the symbol elsewhere in the file finds zero references. Flake8 would catch it but X-Ray currently doesn't.
- **Pattern: "`open()` + GC close (no `with` / `.close()`)."** `benchmark_14_models.py:288`, `tests/test_comprehensive.py:809`. `PY-008` fires on `open(...)` but as MEDIUM severity bucketed with legitimate one-liners; it doesn't distinguish "file handle retained across `.write()` + function return" from `open(...).read()`. A rule for `open(...)` whose return value is assigned to a name not later passed to a `with` or `.close()` would be more precise.
- **Pattern: "stale-autogen claim in generated file."** `requirements.txt:1` declares "Auto-generated by x_ray_project v3.0.0" yet the adk deps are missing. A meta-rule could compare `import` graph to the listed deps and flag the auto-gen claim as invalidated.
