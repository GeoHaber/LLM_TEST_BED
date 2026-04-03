"""Parallel model stress test — find the breaking point.

Progressively loads models and runs parallel inference to determine
how many models can run simultaneously before OOM or failure.

Usage:
    python stress_test_parallel.py
"""

import gc
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Fix Windows console encoding
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Add zen_core_libs to path
ZEN_LIBS = r"C:\Users\dvdze\Documents\GitHub\GeorgeHaber\zen_core_libs"
if ZEN_LIBS not in sys.path:
    sys.path.insert(0, ZEN_LIBS)

try:
    import psutil
except ImportError:
    psutil = None

# ── Configuration ────────────────────────────────────────────────────────────

MODEL_DIR = r"C:\Ai\Models"
TEST_PROMPT = "What is 2+2? Answer in one word."
SYSTEM_PROMPT = "You are a helpful assistant. Be concise."
MAX_TOKENS = 64
N_CTX = 2048  # smaller context to save RAM during stress test
SKIP_PATTERNS = ("mmproj", "embed", "ggml-model")  # skip non-text models

# ── Discover models (sorted smallest → largest for progressive loading) ──────

def discover_models():
    models = []
    for f in Path(MODEL_DIR).glob("*.gguf"):
        name = f.name.lower()
        if any(s in name for s in SKIP_PATTERNS):
            continue
        models.append((f.stat().st_size, str(f)))
    models.sort(key=lambda x: x[0])  # smallest first
    return models


def mem_info():
    if psutil:
        m = psutil.virtual_memory()
        return f"RAM: {m.used / 1024**3:.1f}/{m.total / 1024**3:.1f} GB ({m.percent}%)"
    return "RAM: psutil not available"


def run_single_inference(adapter, model_path, model_idx):
    """Run a single inference and return timing + result."""
    name = os.path.basename(model_path)
    t0 = time.perf_counter()
    try:
        reply = adapter.chat(
            model_path,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": TEST_PROMPT}],
            max_tokens=MAX_TOKENS,
            temperature=0.1,
            n_ctx=N_CTX,
        )
        elapsed = time.perf_counter() - t0
        return {
            "idx": model_idx,
            "model": name,
            "reply": reply[:100],
            "time": round(elapsed, 2),
            "ok": True,
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {
            "idx": model_idx,
            "model": name,
            "error": str(e)[:200],
            "time": round(elapsed, 2),
            "ok": False,
        }


# ── Main stress test ────────────────────────────────────────────────────────

def main():
    from zen_core_libs.llm.inprocess_adapter import InProcessAdapter

    models = discover_models()
    print(f"\n{'='*70}")
    print(f"  PARALLEL MODEL STRESS TEST")
    print(f"  Models found: {len(models)}  |  {mem_info()}")
    print(f"  Prompt: '{TEST_PROMPT}'  |  n_ctx={N_CTX}  max_tokens={MAX_TOKENS}")
    print(f"{'='*70}\n")

    # Show model lineup
    total_size = 0
    for i, (size, path) in enumerate(models):
        size_gb = size / 1024**3
        total_size += size_gb
        print(f"  [{i+1:2d}] {os.path.basename(path):55s} {size_gb:6.1f} GB  cumulative: {total_size:6.1f} GB")
    print()

    # Progressive test: load N models, then run all N in parallel
    test_counts = [2, 4, 6, 8, 10, 12, 14, 16, 20, len(models)]
    # Remove duplicates and caps
    test_counts = sorted(set(min(c, len(models)) for c in test_counts))

    results_log = []

    for batch_size in test_counts:
        batch_models = [path for _, path in models[:batch_size]]
        batch_total_gb = sum(s for s, _ in models[:batch_size]) / 1024**3

        print(f"\n{'─'*70}")
        print(f"  BATCH: {batch_size} models  |  Total size on disk: {batch_total_gb:.1f} GB")
        print(f"  {mem_info()}")
        print(f"{'─'*70}")

        # Create a fresh adapter with the batch size as max_models
        adapter = InProcessAdapter(max_models=batch_size, default_n_ctx=N_CTX)

        # Phase 1: Sequential loading (measure load times)
        print(f"\n  Phase 1: Loading {batch_size} models sequentially...")
        load_times = []
        load_ok = True
        for i, mp in enumerate(batch_models):
            name = os.path.basename(mp)
            t0 = time.perf_counter()
            try:
                adapter._get_or_load(mp, N_CTX)
                lt = time.perf_counter() - t0
                load_times.append(lt)
                print(f"    [{i+1:2d}/{batch_size}] {name:45s} loaded in {lt:.1f}s  |  {mem_info()}")
            except Exception as e:
                print(f"    [{i+1:2d}/{batch_size}] {name:45s} FAILED: {e}")
                load_ok = False
                break

        if not load_ok:
            print(f"\n  >>> BREAKING POINT: Failed to load model {i+1}/{batch_size}")
            print(f"  >>> {mem_info()}")
            results_log.append({
                "batch": batch_size,
                "loaded": i,
                "status": "LOAD_FAIL",
                "mem": mem_info(),
            })
            # Clean up
            adapter.clear()
            gc.collect()
            break

        stats = adapter.stats()
        print(f"\n  All {batch_size} loaded: {stats['models_loaded']} in cache  |  {mem_info()}")

        # Phase 2: Parallel inference (all models at once)
        print(f"\n  Phase 2: Running {batch_size} models in PARALLEL...")
        t_batch_start = time.perf_counter()
        parallel_results = []

        with ThreadPoolExecutor(max_workers=min(batch_size, 24)) as pool:
            futures = {
                pool.submit(run_single_inference, adapter, mp, i): i
                for i, mp in enumerate(batch_models)
            }
            for fut in as_completed(futures):
                r = fut.result()
                parallel_results.append(r)
                status = "OK" if r["ok"] else "FAIL"
                reply_preview = r.get("reply", r.get("error", ""))[:60]
                print(f"    [{r['idx']+1:2d}] {r['model']:45s} {r['time']:6.1f}s  {status}  {reply_preview}")

        t_batch_total = time.perf_counter() - t_batch_start
        ok_count = sum(1 for r in parallel_results if r["ok"])
        fail_count = batch_size - ok_count
        avg_time = sum(r["time"] for r in parallel_results) / len(parallel_results) if parallel_results else 0

        print(f"\n  Batch {batch_size} complete: {ok_count} OK, {fail_count} FAIL")
        print(f"  Wall time: {t_batch_total:.1f}s  |  Avg per model: {avg_time:.1f}s  |  {mem_info()}")

        results_log.append({
            "batch": batch_size,
            "loaded": batch_size,
            "ok": ok_count,
            "fail": fail_count,
            "wall_time": round(t_batch_total, 1),
            "avg_time": round(avg_time, 1),
            "status": "OK" if fail_count == 0 else "PARTIAL",
            "mem": mem_info(),
        })

        # Clean up for next round
        adapter.clear()
        gc.collect()
        time.sleep(1)  # let OS reclaim memory

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  STRESS TEST SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Batch':>6}  {'Loaded':>7}  {'OK':>4}  {'Fail':>5}  {'Wall(s)':>8}  {'Avg(s)':>7}  {'Status':>10}  Memory")
    print(f"  {'─'*6}  {'─'*7}  {'─'*4}  {'─'*5}  {'─'*8}  {'─'*7}  {'─'*10}  {'─'*30}")
    for r in results_log:
        print(f"  {r['batch']:6d}  {r['loaded']:7d}  "
              f"{r.get('ok',''):>4}  {r.get('fail',''):>5}  "
              f"{r.get('wall_time',''):>8}  {r.get('avg_time',''):>7}  "
              f"{r['status']:>10}  {r['mem']}")

    print(f"\n  Max successful parallel: {max((r['batch'] for r in results_log if r['status'] == 'OK'), default=0)} models")
    print(f"  Final: {mem_info()}")
    print()


if __name__ == "__main__":
    main()
