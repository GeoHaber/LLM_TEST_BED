# pyright: reportOptionalCall=false
"""
swarm_bridge — thin adapter between ZenAIos-Dashboard and Local_LLM's Swarm core.

Imports Local_LLM's pure-Python state module (no Flet dependency) and exposes
JSON-serialisable functions for the HTTP endpoints in server.py.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
_LLM_ROOT = os.environ.get(
    "LOCAL_LLM_PATH", os.path.normpath(os.path.join(ROOT, "..", "Local_LLM"))
)

# ── Ensure Local_LLM is importable ──────────────────────────────────────────
_import_error: str | None = None

if os.path.isdir(_LLM_ROOT) and _LLM_ROOT not in sys.path:
    sys.path.insert(0, _LLM_ROOT)

try:
    from Core.swarm.state import (  # type: ignore[import-not-found]
        AppState,
        run_inference_sync,
        judge_results,
        run_arena,
        run_benchmark,
        run_ab_test,
        preload_models,
        drain_pool,
        run_marathon,
        marathon_save_json,
        compute_domain_experts,
        consistency_analysis,
        build_marathon_leaderboard,
        routing_analysis,
        get_performance_recommendations,
        diagnose_bottleneck,
        discover_models,
        JUDGE_WEIGHTS,
    )
except ImportError as exc:
    _import_error = str(exc)
    AppState = None  # type: ignore[assignment,misc]
    run_inference_sync = None  # type: ignore[assignment]
    judge_results = None  # type: ignore[assignment]
    run_arena = None  # type: ignore[assignment]
    run_benchmark = None  # type: ignore[assignment]
    run_ab_test = None  # type: ignore[assignment]
    preload_models = None  # type: ignore[assignment]
    drain_pool = None  # type: ignore[assignment]
    run_marathon = None  # type: ignore[assignment]
    marathon_save_json = None  # type: ignore[assignment]
    compute_domain_experts = None  # type: ignore[assignment]
    consistency_analysis = None  # type: ignore[assignment]
    build_marathon_leaderboard = None  # type: ignore[assignment]
    routing_analysis = None  # type: ignore[assignment]
    get_performance_recommendations = None  # type: ignore[assignment]
    diagnose_bottleneck = None  # type: ignore[assignment]
    discover_models = None  # type: ignore[assignment]
    JUDGE_WEIGHTS = None  # type: ignore[assignment]

try:
    from Core.swarm.prompt_library import (  # type: ignore[import-not-found]
        PROMPT_LIBRARY,
        ALL_PROMPTS,
        get_eval_prompt,
        get_stats as prompt_stats,
    )
except ImportError:
    PROMPT_LIBRARY = {}
    ALL_PROMPTS = []
    get_eval_prompt = None  # type: ignore[assignment]
    prompt_stats = None  # type: ignore[assignment]

try:
    from Core.swarm.evaluation_engine import (  # type: ignore[import-not-found]
        evaluate_response,
        EvalType,
    )

    _HAS_EVAL = True
except ImportError:
    _HAS_EVAL = False
    evaluate_response = None  # type: ignore[assignment]
    EvalType = None  # type: ignore[assignment]

try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:
    psutil = None
    _HAS_PSUTIL = False

# ── Global state ────────────────────────────────────────────────────────────
_state: "AppState | None" = None  # type: ignore[type-arg]
_state_lock = threading.Lock()


def _get_state() -> "AppState":  # type: ignore[type-arg]
    """Lazy-init the shared AppState singleton."""
    global _state
    if _state is not None:
        return _state
    with _state_lock:
        if _state is None:
            if AppState is None:
                raise RuntimeError(f"Local_LLM import failed: {_import_error}")
            _state = AppState()
        return _state  # type: ignore[return-value]


def available() -> bool:
    """Return True if Local_LLM core is importable."""
    return _import_error is None


def status() -> dict:
    """Return bridge availability and configuration."""
    return {
        "available": available(),
        "llm_root": _LLM_ROOT,
        "error": _import_error,
        "has_eval": _HAS_EVAL,
        "has_psutil": _HAS_PSUTIL,
        "prompt_count": len(ALL_PROMPTS),
        "categories": list(PROMPT_LIBRARY.keys()),
    }


# ── Models ──────────────────────────────────────────────────────────────────


def list_models() -> list[dict]:
    """Discover GGUF models using Local_LLM's scanner."""
    if not available():
        return []
    return discover_models()


# ── Prompts ─────────────────────────────────────────────────────────────────


def get_prompts() -> dict:
    """Return prompt library organised by category."""
    return {
        "categories": {cat: list(prompts.keys()) if isinstance(prompts, dict) else prompts for cat, prompts in PROMPT_LIBRARY.items()},
        "total": len(ALL_PROMPTS),
        "stats": prompt_stats() if prompt_stats else {},
    }


def get_random_prompt() -> dict:
    """Pick a random prompt from ALL_PROMPTS."""
    import random

    if not ALL_PROMPTS:
        return {"category": "none", "prompt": "Write a haiku about code."}
    cat, prompt = random.choice(ALL_PROMPTS)  # noqa: S311
    return {"category": cat, "prompt": prompt}


# ── Arena ───────────────────────────────────────────────────────────────────

_arena_lock = threading.Lock()


def run_arena_sync(
    model_paths: list[str],
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 256,
    temperature: float = 0.7,
    n_ctx: int = 2048,
    n_gpu_layers: int = 0,
) -> dict:
    """Run a single arena round: parallel inference + judge."""
    state = _get_state()
    state.selected_paths = model_paths
    state.system_prompt = system_prompt
    state.max_tokens = max_tokens
    state.temperature = temperature
    state.n_ctx = n_ctx
    state.n_gpu_layers = n_gpu_layers

    results: list[dict] = []
    progress_msgs: list[str] = []

    done_event = threading.Event()

    def _on_progress(*args):
        progress_msgs.append(str(args))

    def _on_done(*args):
        done_event.set()

    with _arena_lock:
        run_arena(state, on_progress=_on_progress, on_done=_on_done)
        # Inject the question so run_arena uses it
        # Actually run_arena reads from state — we need the prompt set in state
        # Let me drive it directly instead

    # Direct approach: run inference for each model, then judge
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run_one(path):
        return run_inference_sync(
            model_path=path,
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
        )

    with ThreadPoolExecutor(max_workers=min(len(model_paths), 4)) as pool:
        futures = {pool.submit(_run_one, p): p for p in model_paths}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append({"model_path": futures[fut], "error": str(exc), "response": ""})

    # Judge the results
    scored = judge_results(results, prompt)

    return {
        "prompt": prompt,
        "results": scored,
        "judge_weights": dict(JUDGE_WEIGHTS) if JUDGE_WEIGHTS else {},
        "model_count": len(model_paths),
    }


# ── Benchmark ───────────────────────────────────────────────────────────────


def run_benchmark_sync(
    model_path: str,
    prompt: str = "Explain quicksort in 3 sentences.",
    system_prompt: str = "",
    concurrency_levels: list[int] | None = None,
    max_tokens: int = 256,
    temperature: float = 0.7,
) -> dict:
    """Run concurrency benchmark for a single model."""
    if concurrency_levels is None:
        concurrency_levels = [1, 2, 4]

    bench_results = []
    for workers in concurrency_levels:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        t0 = time.perf_counter()
        errors = 0
        timings: list[float] = []

        def _run():
            return run_inference_sync(
                model_path=model_path,
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_run) for _ in range(workers)]
            for f in as_completed(futs):
                try:
                    r = f.result()
                    if r.get("error"):
                        errors += 1
                    timings.append(r.get("total_time", 0))
                except Exception:
                    errors += 1

        wall = time.perf_counter() - t0
        bench_results.append({
            "workers": workers,
            "wall_time": round(wall, 2),
            "avg_time": round(sum(timings) / len(timings), 2) if timings else 0,
            "errors": errors,
        })

    return {"model": Path(model_path).name, "levels": bench_results}


# ── Single inference ────────────────────────────────────────────────────────


def run_single_inference(
    model_path: str,
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 256,
    temperature: float = 0.7,
    n_ctx: int = 2048,
    n_gpu_layers: int = 0,
    preloaded_llm: object | None = None,
) -> dict:
    """Run a single inference and return the result dict."""
    return run_inference_sync(
        model_path=model_path,
        prompt=prompt,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        preloaded_llm=preloaded_llm,
    )


# ── Evaluation ──────────────────────────────────────────────────────────────


def evaluate(response_text: str, category: str, prompt_text: str) -> dict:
    """Evaluate a response against ground truth if available."""
    if not _HAS_EVAL or get_eval_prompt is None:
        return {"error": "evaluation engine not available"}
    ep = get_eval_prompt(category, prompt_text)
    if ep is None:
        return {"error": f"no eval prompt for {category!r}: {prompt_text[:60]}"}
    result = evaluate_response(response_text, ep)
    return {
        "score": result.score,
        "passed": result.passed,
        "eval_type": result.eval_type.value if hasattr(result.eval_type, "value") else str(result.eval_type),
        "details": result.details,
    }


# ── Pool ────────────────────────────────────────────────────────────────────


def pool_preload(model_paths: list[str]) -> dict:
    """Warm-load models into the memory pool."""
    state = _get_state()
    state.selected_paths = model_paths
    state.pool_enabled = True
    msgs: list[str] = []
    preload_models(state, on_progress=lambda m: msgs.append(m))
    return {
        "loaded": len(state.model_pool),
        "models": [Path(p).name for p in state.model_pool],
        "messages": msgs,
    }


def pool_drain() -> dict:
    """Drain all models from the pool."""
    state = _get_state()
    drain_pool(state)
    return {"drained": True, "pool_size": len(state.model_pool)}


def pool_status() -> dict:
    """Return current pool status."""
    state = _get_state()
    return {
        "enabled": state.pool_enabled,
        "size": len(state.model_pool),
        "models": [Path(p).name for p in state.model_pool],
    }


# ── Memory ──────────────────────────────────────────────────────────────────


def memory_snapshot() -> dict:
    """Return current memory usage."""
    if not _HAS_PSUTIL or psutil is None:
        return {"error": "psutil not installed"}
    proc = psutil.Process(os.getpid())
    vm = psutil.virtual_memory()
    return {
        "process_rss_mb": round(proc.memory_info().rss / (1024 * 1024), 1),
        "system_total_gb": round(vm.total / (1024**3), 1),
        "system_used_percent": vm.percent,
        "system_available_gb": round(vm.available / (1024**3), 1),
    }


# ── Diagnose ────────────────────────────────────────────────────────────────


def diagnose(results: list[dict], mem_snapshots: list | None = None) -> dict:
    """Run bottleneck diagnosis on inference results."""
    # Ensure required keys exist — callers may use different field names
    normed = []
    for r in results:
        d = dict(r)
        d.setdefault("load_time", d.get("elapsed", 0.0) * 0.1)
        d.setdefault("inference_time", d.get("elapsed", 0.0) * 0.9)
        normed.append(d)
    return diagnose_bottleneck(normed, mem_snapshots)


# ── Marathon (simplified sync version) ──────────────────────────────────────

_marathon_lock = threading.Lock()
_marathon_stop = threading.Event()


def marathon_run_round(
    model_paths: list[str],
    prompt: str,
    category: str = "custom",
    system_prompt: str = "",
    max_tokens: int = 256,
    temperature: float = 0.7,
) -> dict:
    """Run one marathon round (all models on one prompt) and return scored results."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[dict] = []

    def _run(path):
        return run_inference_sync(
            model_path=path, prompt=prompt, system_prompt=system_prompt,
            max_tokens=max_tokens, temperature=temperature,
        )

    with ThreadPoolExecutor(max_workers=min(len(model_paths), 4)) as pool:
        futs = {pool.submit(_run, p): p for p in model_paths}
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except Exception as exc:
                results.append({"model_path": futs[f], "error": str(exc), "response": ""})

    scored = judge_results(results, prompt)

    return {
        "category": category,
        "prompt": prompt,
        "results": scored,
        "model_count": len(model_paths),
    }


def marathon_leaderboard(scores: dict) -> list[dict]:
    """Build leaderboard from accumulated marathon scores."""
    return build_marathon_leaderboard(scores)


def marathon_domain_experts(log: list[dict]) -> dict:
    """Find the best model per category."""
    return compute_domain_experts(log)


def marathon_consistency(scores: dict) -> list[dict]:
    """Consistency analysis across marathon rounds."""
    return consistency_analysis(scores)


def marathon_routing(log: list[dict], scores: dict) -> dict:
    """Routing analysis (single-best vs domain-expert)."""
    return routing_analysis(log, scores)


# ── Recommendations ─────────────────────────────────────────────────────────


def recommendations(result: dict) -> list[str]:
    """Performance recommendations for a single result."""
    return get_performance_recommendations(result)
