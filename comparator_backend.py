"""
LLM Model Comparator Backend
=============================
Serves system info, scans local models, and handles comparisons.

Uses **zen_core_libs** for hardware detection, model caching, token counting,
GGUF scanning, and build recommendations — avoiding code duplication.

Usage:
    python comparator_backend.py       → runs on port 8123
    python comparator_backend.py 9000  → runs on custom port

Endpoints:
    GET  /__system-info                → {cpu_count, memory_gb, model_count, has_llama_cpp, models: [...]}
    POST /__comparison/mixed           → {local_models, online_models, prompt, ...} → results
"""

import concurrent.futures
import gc
import ipaddress
import json
import os
import re
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse

# ── zen_eval imports (multi-turn judges, feedback, prompt versioning, gateway) ─
import zen_eval

# ── Skill Lab imports (multi-agent skill optimizer) ─
import skill_optimizer

# Enable Vulkan GPU backend for llama-cpp-python (AMD Radeon / any Vulkan GPU)
# Must be set before llama_cpp is imported. Has no effect if Vulkan is absent.
# Supports multi-GPU: set GGML_VK_VISIBLE_DEVICES=0,1 for two GPUs.
_vk_devices = os.environ.get('GGML_VK_VISIBLE_DEVICES', '0')
os.environ['GGML_VK_VISIBLE_DEVICES'] = _vk_devices


# ── zen_core_libs — single source of truth for shared utilities ───────────────
# Hard dependency (listed in pyproject.toml). No try/except fallback stubs.

from zen_core_libs.llm.native_build import (
    ensure_native_build as ensure_native_llama_cpp,
    build_status as _native_build_status_fn,
    cpu_fingerprint as _cpu_fingerprint,
    load_fingerprint as _load_saved_fingerprint,
    save_fingerprint as _save_fingerprint,
    needs_rebuild as _needs_rebuild,
)
from zen_core_libs.llm.inprocess_adapter import get_inprocess_adapter
from zen_core_libs.common.system import (
    get_cpu_count,
    get_memory_gb,
    get_cpu_info,
    count_tokens,
)
from zen_core_libs.llm.gguf_utils import (
    extract_gguf_metadata,
    infer_metadata_from_filename,
    estimate_model_memory_gb,
    quantization_advisor,
    background_fill_gguf_cache,
)
from zen_core_libs.llm import ModelCache

_rebuild_status = _native_build_status_fn  # callable → dict
_zen_adapter = None  # lazily initialised on first chat call


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a separate thread so inference doesn't block the UI."""

    daemon_threads = True
    allow_reuse_address = True  # prevent "Address already in use" on restart


# ── Application-specific model scanning (enriched schema for API responses) ──

def scan_gguf_models(dirs: list[str] | None = None) -> list[dict]:
    """Scan directories for .gguf model files, enriched with GGUF metadata.

    Uses zen_core_libs primitives for metadata extraction and caching.
    Returns dicts with keys: id, path, size_mb, name, architecture,
    context_length, quantization, parameters, embedding_length.
    """
    if not dirs:
        dirs = [os.path.expanduser("~/AI/Models")]
    models = []
    all_paths = []
    for d in dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        for f in p.rglob("*.gguf"):
            try:
                size_mb = round(f.stat().st_size / (1024 * 1024))
                meta = extract_gguf_metadata(str(f))
                entry = {
                    "id": f.name, "path": str(f), "size_mb": size_mb, "name": f.stem,
                    "architecture": meta.get("architecture", ""),
                    "context_length": meta.get("context_length", 0),
                    "quantization": meta.get("quantization", ""),
                    "parameters": meta.get("parameters", ""),
                    "embedding_length": meta.get("embedding_length", 0),
                }
                models.append(entry)
                all_paths.append(str(f))
            except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
                pass
    # Kick off background thread to fill GGUF header cache for next request
    if all_paths:
        threading.Thread(target=background_fill_gguf_cache, args=(all_paths,), daemon=True).start()
    return models


_MODEL_CACHE_SIZE = int(os.environ.get("LLM_MODEL_CACHE_SIZE", "8"))
_model_cache = ModelCache(max_models=_MODEL_CACHE_SIZE)
_llama_load_lock = threading.Lock()

_PERF_PROFILES = {
    "fastest": {
        "min_threads_per_model": 3,
        "max_threads_per_model": 16,
        "ram_util": 0.90,
        "ctx_cap": True,
        "batch_boost": 1.20,
    },
    "balanced": {
        "min_threads_per_model": 6,
        "max_threads_per_model": 12,
        "ram_util": 0.82,
        "ctx_cap": True,
        "batch_boost": 1.00,
    },
    "stable": {
        "min_threads_per_model": 8,
        "max_threads_per_model": 10,
        "ram_util": 0.70,
        "ctx_cap": True,
        "batch_boost": 0.85,
    },
}


def _normalize_perf_profile(name: str | None) -> str:
    profile = str(name or "balanced").strip().lower()
    return profile if profile in _PERF_PROFILES else "balanced"


def _estimate_runtime_gb_for_path(path: str, n_ctx: int = 4096) -> float:
    """Estimate model runtime memory footprint in GB for scheduling."""
    try:
        size_mb = round(os.path.getsize(path) / (1024 * 1024))
    except OSError:  # xray: ignore[QUAL-002]
        size_mb = 0
    meta = infer_metadata_from_filename(path)
    quant = str(meta.get("quantization", ""))
    base = estimate_model_memory_gb(size_mb, quant)
    # KV/cache grows with context length; apply a conservative multiplier.
    ctx_factor = 1.0 + max(0.0, (n_ctx - 4096) / 4096.0) * 0.18
    return round(base * ctx_factor, 2)


def _choose_n_batch(model_size_mb: int, perf_profile: str = "balanced") -> int:
    """Adaptive n_batch by model size. Lower for large models to avoid stalls."""
    profile = _PERF_PROFILES[_normalize_perf_profile(perf_profile)]
    if model_size_mb >= 14000:
        base = 64
    elif model_size_mb >= 8000:
        base = 96
    elif model_size_mb >= 4000:
        base = 128
    elif model_size_mb >= 1000:
        base = 192
    else:
        base = 256
    boosted = int(base * float(profile["batch_boost"]))
    return max(48, min(384, boosted))


def _effective_n_ctx_for_path(path: str, requested_n_ctx: int, perf_profile: str = "balanced") -> int:
    """Cap requested context to model-trained context length when available."""
    profile = _PERF_PROFILES[_normalize_perf_profile(perf_profile)]
    if not bool(profile.get("ctx_cap", True)):
        return requested_n_ctx
    meta = extract_gguf_metadata(path)
    model_ctx = int(meta.get("context_length", 0) or 0)
    if model_ctx > 0:
        return max(256, min(requested_n_ctx, model_ctx))
    return requested_n_ctx


def _compute_parallel_plan(
    model_paths: list[str],
    n_ctx: int = 4096,
    perf_profile: str = "balanced",
) -> tuple[int, int]:
    """Return (max_workers, threads_per_model), tuned by CPU + available RAM."""
    profile = _PERF_PROFILES[_normalize_perf_profile(perf_profile)]
    total = max(1, len(model_paths))
    cpu_count = max(2, (os.cpu_count() or 4))

    # CPU-side cap: keep enough threads per model to avoid oversubscription.
    min_thr = int(os.environ.get("LLM_MIN_THREADS_PER_MODEL", str(profile["min_threads_per_model"])))
    cpu_worker_cap = max(1, cpu_count // max(2, min_thr))

    # RAM-side cap: estimate how many models can run concurrently safely.
    if HAS_PSUTIL:
        try:
            avail_ram_gb = max(1.0, psutil.virtual_memory().available / (1024 ** 3) * float(profile["ram_util"]))
        except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
            avail_ram_gb = max(1.0, get_memory_gb() * float(profile["ram_util"]))
    else:
        avail_ram_gb = max(1.0, get_memory_gb() * float(profile["ram_util"]))
    est_gb = [_estimate_runtime_gb_for_path(p, n_ctx) for p in model_paths] or [1.0]
    avg_est = max(0.5, sum(est_gb) / len(est_gb))
    ram_worker_cap = max(1, int(avail_ram_gb // (avg_est * 1.10)))

    hard_cap = int(os.environ.get("LLM_MAX_WORKERS", str(total)))
    max_workers = max(1, min(total, cpu_worker_cap, ram_worker_cap, hard_cap))

    max_threads_cap = max(2, int(os.environ.get("LLM_MAX_THREADS_PER_MODEL", str(profile["max_threads_per_model"]))))
    min_threads_cap = max(2, int(os.environ.get("LLM_MIN_THREADS_PER_MODEL", str(profile["min_threads_per_model"]))))
    threads_per_model = max(min_threads_cap, min(max_threads_cap, cpu_count // max_workers))
    return max_workers, threads_per_model


def _get_or_load_model(
    path: str,
    n_ctx: int = 4096,
    draft_model: str = "",
    n_threads_override: int | None = None,
    n_batch_override: int | None = None,
):
    """Return a cached Llama model or load a new one. Thread-safe LRU cache.
    
    If *draft_model* is set, enables speculative decoding (E5).
    """
    import llama_cpp

    thread_key = n_threads_override if n_threads_override is not None else "auto"
    batch_key = n_batch_override if n_batch_override is not None else "auto"
    cache_key = (
        f"{path}::ctx{n_ctx}::thr={thread_key}::batch={batch_key}"
        + (f"::draft={draft_model}" if draft_model else "")
    )

    def _loader():
        n_threads = n_threads_override or int(
            os.environ.get("LLM_THREADS", str(max(2, (os.cpu_count() or 4) // 2)))
        )
        try:
            model_size_mb = round(os.path.getsize(path) / (1024 * 1024))
        except OSError:  # xray: ignore[QUAL-002]
            model_size_mb = 0
        n_batch = n_batch_override or int(os.environ.get("LLM_N_BATCH", str(_choose_n_batch(model_size_mb))))
        n_gpu_layers = int(os.environ.get("LLM_N_GPU_LAYERS", "0"))
        kwargs: dict = dict(
            model_path=path,
            n_ctx=n_ctx,
            n_threads=max(1, n_threads),
            n_gpu_layers=n_gpu_layers,
            flash_attn=n_gpu_layers != 0,
            n_batch=n_batch,
            use_mmap=True,
            use_mlock=False,
            verbose=False,
        )
        # Speculative decoding — llama_cpp ≥0.2.56 supports draft_model
        if draft_model and os.path.isfile(draft_model):
            try:
                kwargs["draft_model"] = llama_cpp.LlamaDraftModel(
                    model_path=draft_model, num_pred_tokens=8,
                )
                print(f"[spec] Using draft model: {os.path.basename(draft_model)}")  # xray: ignore[PY-004]
            except Exception as e:
                print(f"[spec] Draft model failed, falling back: {e}")  # xray: ignore[PY-004]
        # llama.cpp context construction can be unstable when many models
        # are initialized concurrently in one process on some platforms.
        with _llama_load_lock:
            return llama_cpp.Llama(**kwargs)

    return _model_cache.get_or_load(cache_key, _loader)


def _evict_model_cache():
    """Clear entire model cache (call before judge or when memory is needed)."""
    _model_cache.clear()
    gc.collect()


def _build_messages(
    system_prompt: str, user_content: str, model_path: str = ""
) -> list[dict]:
    """Build chat messages, folding system prompt into user message for models
    that don't support the system role (e.g. Gemma, Olmo)."""
    name_lower = os.path.basename(model_path).lower()
    no_system = any(t in name_lower for t in ("gemma", "olmo", "codelama"))
    if no_system or not system_prompt.strip():
        combined = f"{system_prompt.strip()}\n\n{user_content}" if system_prompt.strip() else user_content
        return [{"role": "user", "content": combined}]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


# ─ psutil for RAM delta tracking ────────────────────────────────────────────
try:
    import psutil

    HAS_PSUTIL = True
    proc = psutil.Process()  # current process — used for RAM delta tracking
except ImportError:  # xray: ignore[QUAL-002]
    HAS_PSUTIL = False
    psutil = None
    proc = None


# ── Hardware detection & build recommendations ──────────────────────────────


def get_gpu_info() -> list[dict]:
    """Detect GPUs. Returns list of dicts with name/vendor/vram_gb/backend."""
    gpus: list[dict] = []
    # Try NVIDIA via nvidia-smi
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            timeout=5, stderr=subprocess.DEVNULL, text=True,
        )
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                gpus.append({
                    "name": parts[0],
                    "vendor": "NVIDIA",
                    "vram_gb": round(float(parts[1]) / 1024, 1),
                    "backend": "CUDA",
                })
    except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
        pass
    # Try WMI on Windows (catches AMD / Intel iGPU)
    if not gpus and sys.platform == "win32":
        try:
            import subprocess
            out = subprocess.check_output(
                ["wmic", "path", "win32_videocontroller", "get", "Name,AdapterRAM", "/format:csv"],
                timeout=5, stderr=subprocess.DEVNULL, text=True,
            )
            for line in out.strip().splitlines()[1:]:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    vram = int(parts[1]) / (1024**3) if parts[1].isdigit() else 0
                    name = parts[2]
                    vendor = "AMD" if "AMD" in name.upper() or "RADEON" in name.upper() else \
                             "NVIDIA" if "NVIDIA" in name.upper() else \
                             "Intel" if "INTEL" in name.upper() else "Unknown"
                    backend = "CUDA" if vendor == "NVIDIA" else "ROCm/Vulkan" if vendor == "AMD" else "DirectML"
                    gpus.append({"name": name, "vendor": vendor, "vram_gb": round(vram, 1), "backend": backend})
        except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
            pass
    return gpus


def recommend_llama_build(cpu: dict | None = None, gpus: list | None = None) -> dict:
    """Recommend best llama.cpp build based on detected hardware."""
    rec: dict[str, Any] = {"build": "CPU (OpenBLAS)", "flag": "cpu", "pip": "llama-cpp-python"}
    if gpus:
        for g in gpus if isinstance(gpus, list) else []:
            gd = g if isinstance(g, dict) else {}
            backend = gd.get("backend", "")
            vendor = gd.get("vendor", "").upper()
            if "CUDA" in backend or "NVIDIA" in vendor:
                rec = {
                    "build": "CUDA (GPU)",
                    "flag": "cuda",
                    "pip": "llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124",
                }
                break
            if "ROCm" in backend or "Vulkan" in backend or "AMD" in vendor:
                rec = {
                    "build": "Vulkan (GPU)",
                    "flag": "rocm",
                    "pip": "llama-cpp-python (build with CMAKE_ARGS=-DGGML_VULKAN=on)",
                }
                break
    elif cpu:
        if cpu.get("avx512"):
            rec = {"build": "CPU (AVX-512)", "flag": "avx512", "pip": "llama-cpp-python"}
        elif cpu.get("avx2"):
            rec = {"build": "CPU (AVX2)", "flag": "avx2", "pip": "llama-cpp-python"}
    return rec


def get_system_info(model_dirs: list[str] | None = None) -> dict:
    """Return comprehensive system info dict for the frontend."""
    cpu = get_cpu_info()
    gpus = get_gpu_info()
    models = scan_gguf_models(model_dirs)
    llama = get_llama_cpp_info()
    build_rec = recommend_llama_build(cpu, gpus)
    mem_gb = round(get_memory_gb(), 1)
    vram_gb = sum(g.get("vram_gb", 0) for g in gpus)
    quant_advice = quantization_advisor(mem_gb, vram_gb)
    # Add per-model fitness info
    for m in models:
        est_mem = estimate_model_memory_gb(m.get("size_mb", 0), m.get("quantization", ""))
        m["estimated_memory_gb"] = est_mem
        m["fits_ram"] = est_mem <= mem_gb
        m["fits_vram"] = est_mem <= vram_gb if vram_gb > 0 else False
    return {
        "cpu_count": cpu.get("cores", get_cpu_count()),
        "cpu_name": cpu.get("name", ""),
        "cpu_brand": cpu.get("brand", "Unknown"),
        "memory_gb": mem_gb,
        "gpus": gpus,
        "vram_gb": round(vram_gb, 1),
        "models": models,
        "model_count": len(models),
        "recommended_build": build_rec,
        "quant_advice": quant_advice,
        "has_llama_cpp": llama["installed"],
        "llama_cpp_version": llama["version"],
        "timestamp": time.time(),
    }



def get_llama_cpp_info() -> dict:
    """Return llama.cpp version and recommended build for this hardware."""
    installed = False
    version = None  # None (not a truthy string) so frontend can check falsiness
    try:
        import llama_cpp

        installed = True
        version = getattr(llama_cpp, "__version__", "installed") or "installed"
    except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
        pass

    saved = _load_saved_fingerprint() if callable(_load_saved_fingerprint) else None
    status = _native_build_status_fn() if callable(_native_build_status_fn) else {}
    return {
        "installed": installed,
        "version": version,
        "native_build": saved is not None,
        "cpu_fingerprint": saved.get("cpu", "") if saved else "",
        "build_status": status.get("state", "idle") if isinstance(status, dict) else "idle",
    }


# ─ Alias for backward compat — tests call cb.scan_models() ──────────────────
scan_models = scan_gguf_models


# ─ SQLite persistent results & ELO database ──────────────────────────────────
_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zen_results.db")
_db_lock = threading.Lock()


def _db_init():
    """Create results + ELO tables if they don't exist."""
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        con.executescript("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt TEXT NOT NULL,
                judge_model TEXT,
                timestamp REAL NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS elo (
                model TEXT PRIMARY KEY,
                rating REAL NOT NULL DEFAULT 1500,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                draws INTEGER NOT NULL DEFAULT 0,
                matches INTEGER NOT NULL DEFAULT 0,
                last_updated REAL
            );
            CREATE INDEX IF NOT EXISTS idx_results_ts ON results(timestamp);
        """)
        con.close()


def db_save_result(prompt: str, judge_model: str | None, responses: list[dict], ts: float) -> int:
    """Persist a comparison result. Returns row id."""
    payload = json.dumps({"responses": responses}, default=str)
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        cur = con.execute(
            "INSERT INTO results (prompt, judge_model, timestamp, payload) VALUES (?,?,?,?)",
            (prompt, judge_model or "", ts, payload),
        )
        rid = cur.lastrowid
        con.commit()
        con.close()
    return rid or 0


def db_get_results(limit: int = 50, offset: int = 0) -> list[dict]:
    """Retrieve recent results."""
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM results ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        con.close()
    out = []
    for r in rows:
        entry = dict(r)
        try:
            entry["payload"] = json.loads(entry["payload"])
        except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
            pass
        out.append(entry)
    return out


def db_get_elo() -> list[dict]:
    """Return all ELO rankings sorted by rating descending."""
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM elo ORDER BY rating DESC").fetchall()
        con.close()
    return [dict(r) for r in rows]


def db_update_elo(responses: list[dict]):
    """Update ELO ratings from comparison results. Best score wins."""
    scored = [r for r in responses if not r.get("error") and r.get("judge_score", 0) > 0]
    if len(scored) < 2:
        return
    scored.sort(key=lambda r: r.get("judge_score", 0), reverse=True)
    K = 32
    now = time.time()
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        # Ensure all models exist
        for r in scored:
            con.execute(
                "INSERT OR IGNORE INTO elo (model, rating, wins, losses, draws, matches, last_updated) VALUES (?,1500,0,0,0,0,?)",
                (r["model"], now),
            )
        # Pairwise ELO update: each model vs every other
        ratings = {}
        for r in scored:
            row = con.execute("SELECT rating FROM elo WHERE model=?", (r["model"],)).fetchone()
            ratings[r["model"]] = row[0] if row else 1500.0

        for i, a in enumerate(scored):
            for b in scored[i + 1:]:
                ra, rb = ratings[a["model"]], ratings[b["model"]]
                ea = 1 / (1 + 10 ** ((rb - ra) / 400))
                eb = 1 - ea
                sa_score = a.get("judge_score", 0)
                sb_score = b.get("judge_score", 0)
                if sa_score > sb_score:
                    sa, sb = 1.0, 0.0
                    con.execute("UPDATE elo SET wins=wins+1, matches=matches+1, last_updated=? WHERE model=?", (now, a["model"]))
                    con.execute("UPDATE elo SET losses=losses+1, matches=matches+1, last_updated=? WHERE model=?", (now, b["model"]))
                elif sb_score > sa_score:
                    sa, sb = 0.0, 1.0
                    con.execute("UPDATE elo SET losses=losses+1, matches=matches+1, last_updated=? WHERE model=?", (now, a["model"]))
                    con.execute("UPDATE elo SET wins=wins+1, matches=matches+1, last_updated=? WHERE model=?", (now, b["model"]))
                else:
                    sa, sb = 0.5, 0.5
                    con.execute("UPDATE elo SET draws=draws+1, matches=matches+1, last_updated=? WHERE model=?", (now, a["model"]))
                    con.execute("UPDATE elo SET draws=draws+1, matches=matches+1, last_updated=? WHERE model=?", (now, b["model"]))
                ratings[a["model"]] = ra + K * (sa - ea)
                ratings[b["model"]] = rb + K * (sb - eb)

        for model, rating in ratings.items():
            con.execute("UPDATE elo SET rating=? WHERE model=?", (round(rating, 1), model))
        con.commit()
        con.close()


def db_clear_elo():
    """Reset all ELO ratings."""
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        con.execute("DELETE FROM elo")
        con.commit()
        con.close()


# Initialize DB on import
_db_init()


# ─ Judge score extraction (robust, multi-fallback) ────────────────────────────

def extract_judge_scores(raw_text: str) -> dict:
    """Extract evaluation scores from judge LLM output.

    Handles:
      1. Clean JSON
      2. JSON in markdown fences
      3. Nested JSON ({"evaluation": {...}})
      4. Partial / malformed JSON
      5. Natural-language scores ("overall: 8/10")
      6. Total garbage → {"overall": 0}

    Returns a dict always containing at least the key ``overall`` (0-10).
    """
    if not raw_text or not raw_text.strip():
        return {"overall": 0}

    raw = raw_text.strip()

    # ── Step 1: try markdown fences first ────────────────────────────────────
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if fence_match:
        # Extract balanced JSON from inside fences
        inner = fence_match.group(1).strip()
        balanced_inner = _extract_balanced_json(inner)
        json_str = balanced_inner if balanced_inner else inner
    else:
        json_str = raw

    # ── Step 2: try strict JSON parse ────────────────────────────────────────
    parsed = _try_json(json_str)

    # ── Step 3: find first balanced { ... } in the raw text ─────────────────
    if parsed is None:
        balanced = _extract_balanced_json(raw)
        if balanced:
            parsed = _try_json(balanced)

    # ── Step 4: handle nested JSON ───────────────────────────────────────────
    if parsed is not None:
        if "overall" not in parsed:
            # Check for nesting: {"evaluation": {"overall": 8, ...}}
            for v in parsed.values():
                if isinstance(v, dict) and "overall" in v:
                    parsed = v
                    break

    # ── Step 5: regex fallback from natural language ─────────────────────────
    if parsed is None:
        parsed = _extract_scores_regex(raw)

    # ── Step 6: normalise and clamp ──────────────────────────────────────────
    result = _normalise_scores(parsed or {})
    return result


def _extract_balanced_json(text: str) -> str | None:
    """Find the first balanced {...} block in *text*, handling nested braces and strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\" and in_str:
            escape = True
            continue
        if c == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _try_json(text: str) -> dict | None:
    """Try to json.loads *text*, return dict or None."""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    # Try fixing common issues: unquoted keys
    try:
        fixed = re.sub(r'(?<={|,)\s*(\w+)\s*:', r' "\1":', text)
        obj = json.loads(fixed)
        if isinstance(obj, dict):
            return obj
    except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
        pass
    return None


def _extract_scores_regex(text: str) -> dict:
    """Extract scores from natural-language judge output."""
    result: dict = {}
    lower = text.lower()

    # Match patterns: "overall score: 8", "overall: 8/10", "overall 8 out of 10", "overall is: 8", "overall = 7"
    patterns = [
        (r"overall[\s:=]+(?:is[\s:]+)?(\d+(?:\.\d+)?)\s*(?:/\s*10|out\s+of\s+10)?", "overall"),
        (r"accuracy[\s:=]+(?:is[\s:]+)?(\d+(?:\.\d+)?)\s*(?:/\s*10|out\s+of\s+10)?", "accuracy"),
        (r"reasoning[\s:=]+(?:is[\s:]+)?(\d+(?:\.\d+)?)\s*(?:/\s*10|out\s+of\s+10)?", "reasoning"),
        (r"instruction.?following[\s:=]+(?:is[\s:]+)?(true|false|\d+(?:\.\d+)?)", "instruction_following"),
        (r"safety[\s:=]+(?:is[\s:]+)?[\"']?(safe|unsafe|refused)[\"']?", "safety"),
        (r"conciseness[\s:=]+(?:is[\s:]+)?(\d+(?:\.\d+)?)\s*(?:/\s*10)?", "conciseness"),
        (r"multilingual[\s:=]+(?:is[\s:]+)?(\d+(?:\.\d+)?)\s*(?:/\s*10)?", "multilingual"),
    ]
    for pattern, key in patterns:
        m = re.search(pattern, lower)
        if m:
            result[key] = float(m.group(1))

    # If no overall but we found other scores, average them
    if "overall" not in result and result:
        nums = [v for v in result.values() if isinstance(v, (int, float))]
        if nums:
            result["overall"] = round(sum(nums) / len(nums), 1)

    # Absolute fallback: any standalone number 0-10 near "score"
    if "overall" not in result:
        m = re.search(r"score[:\s]*(\d+(?:\.\d+)?)", lower)
        if m:
            result["overall"] = float(m.group(1))

    if "overall" not in result:
        result["overall"] = 0

    return result


def _normalise_scores(d: dict) -> dict:
    """Ensure 'overall' exists, parse string scores, clamp to 0-10."""
    result: dict = {}
    for k, v in d.items():
        if isinstance(v, str):
            # Handle "8/10" format
            m = re.match(r"(\d+(?:\.\d+)?)\s*/\s*\d+", v)
            if m:
                v = float(m.group(1))
            else:
                try:
                    v = float(v)
                except (ValueError, TypeError):
                    result[k] = v
                    continue
        if isinstance(v, (int, float)):
            v = max(0.0, min(10.0, float(v)))
        result[k] = v

    if "overall" not in result:
        # Try to compute from other numeric scores
        nums = [v for v in result.values() if isinstance(v, (int, float))]
        result["overall"] = round(sum(nums) / len(nums), 1) if nums else 0

    return result


# ─ URL validation (SSRF prevention) ──────────────────────────────────────────

# Allowed HTTPS hosts for model downloads
_ALLOWED_DOWNLOAD_HOSTS = {
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cdn-lfs-us-1.huggingface.co",
    "github.com",
    "objects.githubusercontent.com",
    "releases.githubusercontent.com",
    "gitlab.com",
    "ollama.com",
}


def validate_download_url(url: str) -> bool:
    """Validate a download URL for safety (prevent SSRF).

    Returns True only if the URL:
      - Uses HTTPS scheme
      - Targets an allowed host
      - Does not resolve to a private/loopback IP
    """
    try:
        parsed = urlparse(url)
    except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
        return False

    # Only HTTPS allowed
    if parsed.scheme not in ("https",):
        return False

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False

    # Block loopback and private IPs
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_reserved:
            return False
    except ValueError:  # xray: ignore[QUAL-002]
        # Not an IP literal — that's fine, check hostname
        pass

    # Block localhost names
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"):
        return False

    # Check against allowed hosts
    if hostname not in _ALLOWED_DOWNLOAD_HOSTS:
        return False

    return True


# ─ System info cache (hardware detection is slow on first call) ───────────────
_sysinfo_cache: dict | None = None
_sysinfo_lock = threading.Lock()
_SYSINFO_TTL = 60  # seconds — rescan models each minute, hw rarely changes


def get_system_info_cached(model_dirs: list[str]) -> dict:
    """Return system info, recomputing only after TTL expires."""
    global _sysinfo_cache  # xray: ignore[PY-006]
    with _sysinfo_lock:
        if _sysinfo_cache is not None:
            age = time.time() - _sysinfo_cache.get("_cache_ts", 0)
            if age < _SYSINFO_TTL:
                # Refresh model list inline (fast) while keeping cached hw data
                fresh_models = scan_gguf_models(model_dirs)
                mem_gb = _sysinfo_cache.get("memory_gb", 8)
                vram_gb = _sysinfo_cache.get("vram_gb", 0)
                for m in fresh_models:
                    est = estimate_model_memory_gb(m.get("size_mb", 0), m.get("quantization", ""))
                    m["estimated_memory_gb"] = est
                    m["fits_ram"] = est <= mem_gb
                    m["fits_vram"] = est <= vram_gb if vram_gb > 0 else False
                result = dict(_sysinfo_cache)
                result["models"] = fresh_models
                result["model_count"] = len(fresh_models)
                return result
        info = get_system_info(model_dirs)  # wrapper already adds llama_cpp + timestamp
        info["_cache_ts"] = time.time()
        _sysinfo_cache = info
        return info


# ─ HF Model Discovery cache ──────────────────────────────────────────────────
_discovery_cache: dict[str, dict] = {}  # cache_key → {ts, data}
_discovery_lock = threading.Lock()
_DISCOVERY_TTL = 900  # 15 minutes

_TRUSTED_QUANTIZERS = {
    "bartowski", "mradermacher", "unsloth", "TheBloke",
    "QuantFactory", "MaziyarPanahi", "lmstudio-community",
}


def _discover_hf_models(query: str = "", sort: str = "trending",
                        limit: int = 30) -> list[dict]:
    """Search HuggingFace for GGUF models. Uses huggingface_hub API."""
    cache_key = f"{query}|{sort}|{limit}"
    with _discovery_lock:
        cached = _discovery_cache.get(cache_key)
        if cached and time.time() - cached["ts"] < _DISCOVERY_TTL:
            return cached["data"]

    try:
        from huggingface_hub import HfApi
        api = HfApi()

        kwargs: dict[str, Any] = {
            "limit": min(limit, 60),
            "filter": "gguf",
        }
        if sort == "trending":
            kwargs["sort"] = "trendingScore"
        elif sort == "downloads":
            kwargs["sort"] = "downloads"
        elif sort == "newest":
            kwargs["sort"] = "lastModified"
        elif sort == "likes":
            kwargs["sort"] = "likes"

        if query.strip():
            kwargs["search"] = query.strip()

        raw = list(api.list_models(**kwargs))
        results = []
        for m in raw:
            author = (m.id or "").split("/")[0] if "/" in (m.id or "") else ""
            results.append({
                "id": m.id,
                "author": author,
                "trusted": author in _TRUSTED_QUANTIZERS,
                "downloads": getattr(m, "downloads", 0) or 0,
                "likes": getattr(m, "likes", 0) or 0,
                "lastModified": str(getattr(m, "last_modified", "") or ""),
                "tags": list(getattr(m, "tags", []) or []),
                "pipeline": getattr(m, "pipeline_tag", "") or "",
            })

        with _discovery_lock:
            _discovery_cache[cache_key] = {"ts": time.time(), "data": results}
        return results
    except Exception as exc:
        return [{"error": str(exc)}]


# ─ Download job tracking ─────────────────────────────────────────────────────
_download_jobs: dict[str, dict] = {}  # job_id → {state, progress, path, error}
_download_lock = threading.Lock()
# ─ Install job tracking ─────────────────────────────────────────────
_install_jobs: dict[str, dict] = {}  # job_id → {state, log, error, status_text}
_install_lock = threading.Lock()


# ─ Input limits & defaults ────────────────────────────────────────────────────
MAX_PROMPT_TOKENS = 8192  # reject comparison prompts larger than this
DEFAULT_INFERENCE_TIMEOUT = 300  # seconds; overridable per-request
MAX_INFERENCE_TIMEOUT = 1800     # hard ceiling (30 min for reasoning models)


# ─ Rate limiting ──────────────────────────────────────────────────────────────

class _RateLimiter:
    """Simple per-IP sliding-window rate limiter.  Thread-safe."""

    def __init__(self, max_requests: int = 30, window_sec: float = 60.0):
        self._max = max_requests
        self._window = window_sec
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}  # ip → [timestamps]

    def allow(self, ip: str) -> bool:
        now = time.time()
        cutoff = now - self._window
        with self._lock:
            stamps = self._hits.get(ip, [])
            stamps = [t for t in stamps if t > cutoff]
            if len(stamps) >= self._max:
                self._hits[ip] = stamps
                return False
            stamps.append(now)
            self._hits[ip] = stamps
            return True

    def remaining(self, ip: str) -> int:
        now = time.time()
        cutoff = now - self._window
        with self._lock:
            stamps = [t for t in self._hits.get(ip, []) if t > cutoff]
            return max(0, self._max - len(stamps))


# Global rate limiter: 30 requests/min for heavy endpoints
_rate_limiter = _RateLimiter(max_requests=30, window_sec=60.0)


def _is_safe_model_path(path: str, model_dirs: list[str]) -> bool:
    """Check that *path* is a .gguf file inside one of *model_dirs*.

    Prevents path-traversal attacks (e.g. loading /etc/passwd via the API).
    """
    if not path or not path.lower().endswith(".gguf"):
        return False
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    for d in model_dirs:
        try:
            if real.startswith(os.path.realpath(d) + os.sep):
                return True
        except (OSError, ValueError):
            continue
    return False


# ─ Model Comparator Handler ─────────────────────────────────────────────────
class ComparatorHandler(BaseHTTPRequestHandler):
    """HTTP request handler for model comparator endpoints."""

    # Model directories: env var > common locations > home-based > project-local
    # Deduplicate via resolved (canonical) paths to avoid scanning the same dir twice.
    model_dirs = list({
        str(Path(d).resolve()): d for d in [
            os.environ.get("ZENAI_MODEL_DIR", ""),
            os.environ.get("SWARM_MODELS_DIR", ""),
            "C:\\Ai\\Models",
            "C:\\AI\\Models",
            str(Path.home() / "AI" / "Models"),
            str(Path.home() / "Ai" / "Models"),
            str(Path(__file__).resolve().parent / "models"),
        ] if d and Path(d).is_dir()
    }.values()) or [str(Path.home() / "AI" / "Models")]

    # ── CORS preflight ────────────────────────────────────────────────────────
    def do_OPTIONS(self) -> None:
        try:
            self.send_response(204)
            self._cors_headers()
            self.end_headers()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass

    def do_GET(self) -> None:
        if self.path == "/__system-info":
            self._handle_system_info()
        elif self.path == "/__health":
            self._send_json(200, {"ok": True, "ts": time.time()})
        elif self.path == "/__build-status":
            self._send_json(200, _native_build_status_fn() if callable(_native_build_status_fn) else dict(_native_build_status_fn))
        elif self.path == "/__config":
            self._send_json(200, {
                "vk_devices": os.environ.get("GGML_VK_VISIBLE_DEVICES", "0"),
                "default_inference_timeout": DEFAULT_INFERENCE_TIMEOUT,
                "max_inference_timeout": MAX_INFERENCE_TIMEOUT,
                "max_prompt_tokens": MAX_PROMPT_TOKENS,
                "rate_limit": {"max_requests": _rate_limiter._max, "window_sec": _rate_limiter._window},
            })
        elif self.path.startswith("/__discover-models"):
            self._handle_discover_models()
        elif self.path.startswith("/__scout"):
            self._handle_scout()
        elif self.path.startswith("/__tool-ecosystem"):
            self._handle_tool_ecosystem()
        elif self.path.startswith("/__download-status"):
            self._handle_download_status()
        elif self.path.startswith("/__install-status"):
            self._handle_install_status()
        # ── zen_eval GET endpoints ────────────────────────────────────────
        elif self.path.startswith("/__prompts"):
            self._handle_prompts_get()
        elif self.path.startswith("/__feedback"):
            self._handle_feedback_get()
        elif self.path.startswith("/__gateway/stats"):
            self._handle_gateway_stats()
        elif self.path.startswith("/__gateway/routes"):
            self._handle_gateway_routes_get()
        elif self.path.startswith("/__results"):
            self._handle_results_get()
        elif self.path.startswith("/__elo"):
            self._handle_elo_get()
        # ── Skill Lab GET endpoints ───────────────────────────────────────
        elif self.path == "/__skill-lab/examples":
            self._handle_skill_lab_examples()
        elif self.path.startswith("/__skill-lab/stream/"):
            self._handle_skill_lab_stream()
        elif self.path.startswith("/__skill-lab/download/"):
            self._handle_skill_lab_download()
        elif self.path.startswith("/__skill-lab/status/"):
            self._handle_skill_lab_status()
        elif self.path in ("/", "/model_comparator.html", "/index.html"):
            # Serve the main HTML app directly from the backend
            html_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "model_comparator.html"
            )
            try:
                with open(html_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:  # xray: ignore[QUAL-002]
                self._send_json(404, {"error": "model_comparator.html not found"})
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                pass
        else:
            # Serve static assets (images, icons) from the same directory
            _STATIC_TYPES = {
                ".js": "application/javascript",
                ".css": "text/css",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".ico": "image/x-icon",
                ".svg": "image/svg+xml",
                ".webp": "image/webp",
            }
            _ext = os.path.splitext(self.path.split("?")[0])[1].lower()
            if _ext in _STATIC_TYPES:
                _static_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    os.path.basename(self.path.split("?")[0]),
                )
                try:
                    with open(_static_path, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", _STATIC_TYPES[_ext])
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(body)
                except FileNotFoundError:  # xray: ignore[QUAL-002]
                    self._send_json(404, {"error": "Static asset not found"})
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                    pass
            else:
                self._send_json(404, {"error": "Not found"})

    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else "unknown"

    def do_POST(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body) if body else {}
        except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
            self._send_json(400, {"error": "Invalid JSON"})
            return

        # Rate-limit heavy POST endpoints
        if self.path in ("/__comparison/mixed", "/__comparison/stream", "/__chat"):
            if not _rate_limiter.allow(self._client_ip()):
                remaining = _rate_limiter.remaining(self._client_ip())
                self._send_json(429, {
                    "error": "Too many requests. Please wait a moment.",
                    "retry_after": 60,
                    "remaining": remaining,
                })
                return

        if self.path == "/__comparison/mixed":
            self._handle_comparison(data)
        elif self.path == "/__comparison/stream":
            self._handle_stream_comparison(data)
        elif self.path == "/__download-model":
            self._handle_download(data)
        elif self.path == "/__install-llama":
            self._handle_install_llama(data)
        elif self.path == "/__chat":
            self._handle_chat(data)
        # ── zen_eval POST endpoints ───────────────────────────────────────
        elif self.path == "/__prompts":
            self._handle_prompts_post(data)
        elif self.path == "/__prompts/alias":
            self._handle_prompt_alias(data)
        elif self.path == "/__feedback":
            self._handle_feedback_post(data)
        elif self.path == "/__feedback/human":
            self._handle_feedback_human(data)
        elif self.path == "/__judge/conversation":
            self._handle_judge_conversation(data)
        elif self.path == "/__judge/toolcall":
            self._handle_judge_toolcall(data)
        elif self.path == "/__gateway/routes":
            self._handle_gateway_routes_post(data)
        elif self.path == "/__gateway/resolve":
            self._handle_gateway_resolve(data)
        elif self.path == "/__results/save":
            self._handle_results_save(data)
        elif self.path == "/__elo/reset":
            db_clear_elo()
            self._send_json(200, {"ok": True})
        # ── Skill Lab POST endpoints ──────────────────────────────────────
        elif self.path == "/__skill-lab/upload":
            self._handle_skill_lab_upload(data)
        elif self.path == "/__skill-lab/analyze":
            self._handle_skill_lab_analyze(data)
        elif self.path == "/__skill-lab/update-config":
            self._handle_skill_lab_update_config(data)
        elif self.path == "/__skill-lab/regenerate":
            self._handle_skill_lab_analyze(data)  # same handler
        elif self.path.startswith("/__skill-lab/start/"):
            self._handle_skill_lab_start(data)
        elif self.path.startswith("/__skill-lab/stop/"):
            self._handle_skill_lab_stop(data)
        elif self.path.startswith("/__skill-lab/examples/") and self.path.endswith("/load"):
            self._handle_skill_lab_load_example(data)
        else:
            self._send_json(404, {"error": "Not found"})

    # ── Handlers ─────────────────────────────────────────────────────────────
    def _handle_system_info(self) -> None:
        try:
            info = get_system_info_cached(self.model_dirs)
            self._send_json(200, info)
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_results_get(self) -> None:
        """GET /__results?limit=50&offset=0"""
        qs = parse_qs(urlparse(self.path).query)
        limit = min(int(qs.get("limit", ["50"])[0]), 500)  # xray: ignore[QUAL-003]
        offset = int(qs.get("offset", ["0"])[0])  # xray: ignore[QUAL-003]
        self._send_json(200, db_get_results(limit, offset))

    def _handle_results_save(self, data: dict) -> None:
        """POST /__results/save — persist a comparison result."""
        prompt = data.get("prompt", "")
        judge = data.get("judge_model", "")
        responses = data.get("responses", [])
        ts = data.get("timestamp", time.time())
        rid = db_save_result(prompt, judge, responses, ts)
        # Also update ELO
        db_update_elo(responses)
        self._send_json(201, {"ok": True, "id": rid})

    def _handle_elo_get(self) -> None:
        """GET /__elo — return persistent ELO rankings."""
        self._send_json(200, db_get_elo())

    def _handle_install_status(self) -> None:
        """GET /__install-status?job=<id>"""
        qs = parse_qs(urlparse(self.path).query)
        job_id = qs.get("job", [""])[0]
        with _install_lock:
            job = dict(_install_jobs.get(job_id) or {"state": "unknown"})
        self._send_json(200, job)

    def _handle_install_llama(self, data: dict) -> None:
        """POST /__install-llama — run pip install in background, stream log."""
        import uuid

        pip_cmd = data.get("pip", "pip install llama-cpp-python").strip()
        # Security: only allow llama-cpp-python installation
        if not pip_cmd.startswith("pip install llama-cpp-python"):
            self._send_json(
                400,
                {"ok": False, "error": "Only llama-cpp-python installation allowed"},
            )
            return
        job_id = str(uuid.uuid4())[:8]
        with _install_lock:
            _install_jobs[job_id] = {
                "state": "starting",
                "log": "",
                "error": "",
                "status_text": "Starting…",
            }
        t = threading.Thread(target=_run_install, args=(job_id, pip_cmd), daemon=True)
        t.start()
        self._send_json(200, {"ok": True, "job_id": job_id})

    def _handle_download_status(self) -> None:
        """GET /__download-status?job=<id>"""
        qs = parse_qs(urlparse(self.path).query)
        job_id = qs.get("job", [""])[0]
        with _download_lock:
            job = _download_jobs.get(job_id) or {"state": "unknown"}
        self._send_json(200, job)

    def _handle_download(self, data: dict) -> None:
        """POST /__download-model — fire a background download, return job_id immediately."""
        import uuid

        model = data.get("model", "").strip()
        dest = data.get("dest", str(Path.home() / "AI" / "Models"))

        if not model:
            self._send_json(400, {"ok": False, "error": "model is required"})
            return

        job_id = str(uuid.uuid4())[:8]
        with _download_lock:
            _download_jobs[job_id] = {
                "state": "starting",
                "progress": 0,
                "path": "",
                "error": "",
            }

        # Start the real work in a background thread so this request returns instantly
        t = threading.Thread(target=_run_download, args=(job_id, model, dest), daemon=True)
        t.start()

        self._send_json(200, {"ok": True, "job_id": job_id})

    def _handle_comparison(self, data: dict) -> None:
        try:
            prompt = data.get("prompt", "")
            local_models = data.get("local_models", [])
            online_models = data.get("online_models", [])
            judge_model = data.get("judge_model")
            judge_system_prompt = data.get("judge_system_prompt", "")
            system_prompt = data.get("system_prompt", "You are a helpful assistant.")

            # ── Input validation ──────────────────────────────────────────
            # Reject oversized prompts (DoS prevention)
            if count_tokens(prompt) > MAX_PROMPT_TOKENS:
                self._send_json(400, {
                    "error": f"Prompt too large (>{MAX_PROMPT_TOKENS} tokens). Please shorten it."
                })
                return

            # Validate all model paths are inside configured model_dirs
            safe_models = [
                p for p in local_models
                if _is_safe_model_path(p, self.model_dirs)
            ]
            if len(safe_models) != len(local_models):
                rejected = len(local_models) - len(safe_models)
                print(f"[compare] WARN rejected {rejected} model path(s) outside model_dirs")  # xray: ignore[PY-004]

            # Clamp inference timeout to safe range
            req_timeout = min(
                max(10, int(data.get("inference_timeout", DEFAULT_INFERENCE_TIMEOUT))),
                MAX_INFERENCE_TIMEOUT,
            )
            params = {
                "n_ctx": int(data.get("n_ctx", 4096)),
                "max_tokens": int(data.get("max_tokens", 512)),
                "temperature": float(data.get("temperature", 0.7)),
                "top_p": float(data.get("top_p", 0.95)),
                "repeat_penalty": float(data.get("repeat_penalty", 1.1)),
                "inference_timeout": req_timeout,
                "performance_profile": _normalize_perf_profile(data.get("performance_profile", "balanced")),
            }
            responses = self._run_local_comparisons(prompt, system_prompt, safe_models, params)

            # ── Apply judge scoring if a judge model was selected ──────────
            if judge_model and local_models:
                judge_path = self._resolve_judge_path(judge_model, local_models)
                if judge_path:
                    # Fall back to a minimal scoring prompt if none provided
                    if not judge_system_prompt:
                        judge_system_prompt = (
                            "You are an expert evaluator. Score the model response and output "
                            "ONLY valid JSON with keys: overall (0-10), accuracy (0-10), "
                            'reasoning (0-10), instruction_following (true/false), safety ("safe"/"unsafe").'
                        )
                    responses = self._run_judge(
                        responses, prompt, judge_path, judge_system_prompt, params
                    )

            results = {
                "prompt": prompt,
                "models_tested": len(local_models) + len(online_models),
                "responses": responses,
                "judge_model": judge_model,
                "timestamp": time.time(),
            }
            self._send_json(200, results)
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_stream_comparison(self, data: dict) -> None:
        """SSE endpoint: streams per-model tokens and results as they generate."""
        try:
            prompt = data.get("prompt", "")
            local_models = data.get("local_models", [])
            judge_model = data.get("judge_model")
            judge_system_prompt = data.get("judge_system_prompt", "")
            system_prompt = data.get("system_prompt", "You are a helpful assistant.")

            if count_tokens(prompt) > MAX_PROMPT_TOKENS:
                self._send_json(400, {
                    "error": f"Prompt too large (>{MAX_PROMPT_TOKENS} tokens)."
                })
                return

            safe_models = [
                p for p in local_models
                if _is_safe_model_path(p, self.model_dirs)
            ]

            req_timeout = min(
                max(10, int(data.get("inference_timeout", DEFAULT_INFERENCE_TIMEOUT))),
                MAX_INFERENCE_TIMEOUT,
            )
            params = {
                "n_ctx": int(data.get("n_ctx", 4096)),
                "max_tokens": int(data.get("max_tokens", 512)),
                "temperature": float(data.get("temperature", 0.7)),
                "top_p": float(data.get("top_p", 0.95)),
                "repeat_penalty": float(data.get("repeat_penalty", 1.1)),
                "inference_timeout": req_timeout,
                "performance_profile": _normalize_perf_profile(data.get("performance_profile", "balanced")),
            }

            # Send SSE headers
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self._cors_headers()
            self.end_headers()

            _client_disconnected = False

            def _sse(event: str, payload: dict) -> None:
                nonlocal _client_disconnected
                if _client_disconnected:
                    return
                try:
                    line = f"event: {event}\ndata: {json.dumps(payload)}\n\n"
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                    _client_disconnected = True
                    print("[SSE] Client disconnected, suppressing further events", flush=True)  # xray: ignore[PY-004]

            # Stream model inference one-by-one
            try:
                import llama_cpp
            except ImportError:  # xray: ignore[QUAL-002]
                _sse("error", {"error": "llama-cpp-python not installed"})
                _sse("done", {})
                return

            responses: list[dict] = []
            n_ctx = params["n_ctx"]
            max_tokens = params["max_tokens"]
            temperature = params["temperature"]
            top_p = params["top_p"]
            repeat_penalty = params["repeat_penalty"]
            inference_timeout = params["inference_timeout"]
            perf_profile = params.get("performance_profile", "balanced")
            total_models = len(safe_models)
            max_workers, threads_per_model = _compute_parallel_plan(safe_models, n_ctx, perf_profile)

            # Dispatch inference (load+infer inside worker thread) in parallel
            # stream=False lets llama.cpp run the entire C++ compute while
            # the GIL is released, enabling TRUE parallel execution across
            # threads — exactly like the original Swarm run_arena pattern.
            print(  # xray: ignore[PY-004]
                f"[compare] Dispatching prompt to {total_models} model(s) in parallel "
                f"(profile={perf_profile}, workers={max_workers}, threads/model={threads_per_model})...",
                flush=True,
            )
            _dispatch_t0 = time.time()

            # Tell the frontend all models are now generating
            for idx, path in enumerate(safe_models):
                model_name = os.path.basename(path).replace(".gguf", "")
                _sse("model_start", {
                    "model": model_name, "model_index": idx,
                    "total_models": total_models,
                })

            def _thread_infer(model_idx: int, path: str) -> dict:
                """Run inference on a pre-loaded model with stream=False."""
                model_name = os.path.basename(path).replace(".gguf", "")
                model_size_mb = round(os.path.getsize(path) / (1024 * 1024)) if os.path.exists(path) else 0
                model_n_ctx = _effective_n_ctx_for_path(path, n_ctx, perf_profile)
                n_batch = _choose_n_batch(model_size_mb, perf_profile)
                llm = _get_or_load_model(
                    path,
                    model_n_ctx,
                    n_threads_override=threads_per_model,
                    n_batch_override=n_batch,
                )

                t0 = time.time()
                ram_before = 0
                try:
                    if HAS_PSUTIL:
                        ram_before = proc.memory_info().rss // (1024 * 1024) if proc else 0
                except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
                    pass

                try:
                    # stream=False → entire C++ compute runs with GIL released
                    out = llm.create_chat_completion(
                        messages=_build_messages(system_prompt, prompt, path),
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                        repeat_penalty=repeat_penalty,
                        stream=False,
                    )

                    elapsed_ms = (time.time() - t0) * 1000
                    msg = out["choices"][0]["message"]
                    response_text = (msg.get("content") or "").strip()
                    # Reasoning models (DeepSeek-R1, Qwen3.5, GLM) put chain-of-thought
                    # in reasoning_content and leave content empty — extract from there.
                    if not response_text and msg.get("reasoning_content"):
                        response_text = msg["reasoning_content"].strip()
                    completion_tokens = out.get("usage", {}).get("completion_tokens", 0)
                    if not completion_tokens:
                        completion_tokens = max(1, count_tokens(response_text))
                    prompt_tokens = out.get("usage", {}).get("prompt_tokens", 0)
                    tps = completion_tokens / (elapsed_ms / 1000) if elapsed_ms > 0 else 0
                    # TTFT not available with stream=False; use total time / tokens as proxy
                    ttft_ms = elapsed_ms / max(completion_tokens, 1)

                    ram_after = 0
                    try:
                        if HAS_PSUTIL:
                            ram_after = proc.memory_info().rss // (1024 * 1024) if proc else 0
                    except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
                        pass
                    ram_delta = max(0, ram_after - ram_before)

                    model_size_gb = model_size_mb / 1024 if model_size_mb else 0
                    efficiency = round(tps / model_size_gb, 2) if model_size_gb > 0 else 0

                    result = {
                        "model": model_name, "model_path": path, "path": path,
                        "response": response_text,
                        "time_ms": round(elapsed_ms, 1),
                        "tokens": completion_tokens,
                        "tokens_per_sec": round(tps, 1),
                        "quality_score": 0,
                        "ttft_ms": round(ttft_ms, 1),
                        "ram_delta_mb": ram_delta,
                        "prompt_tokens": prompt_tokens,
                        "model_size_mb": model_size_mb,
                        "efficiency": efficiency,
                        "response_chars": len(response_text),
                    }
                    print(f"  [model-{model_idx}] {model_name} done - {completion_tokens} tok, {tps:.1f} t/s, eff={efficiency:.1f} t/s/GB, {elapsed_ms/1000:.1f}s", flush=True)  # xray: ignore[PY-004]
                    return result
                except Exception as exc:
                    elapsed_ms = (time.time() - t0) * 1000
                    result = {
                        "model": model_name, "model_path": path, "path": path,
                        "response": f"ERROR: {exc}",
                        "error": str(exc),
                        "time_ms": round(elapsed_ms, 1),
                        "tokens": 0, "tokens_per_sec": 0,
                        "quality_score": 0, "ttft_ms": 0, "ram_delta_mb": 0,
                        "model_size_mb": model_size_mb, "efficiency": 0,
                        "response_chars": 0,
                    }
                    print(f"  [model-{model_idx}] {model_name} ERROR: {exc}", flush=True)  # xray: ignore[PY-004]
                    return result

            # Launch all models via ThreadPoolExecutor
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            ) as pool:
                futures = {
                    pool.submit(_thread_infer, idx, path): idx
                    for idx, path in enumerate(safe_models)
                }

                # As each model completes, send its result immediately via SSE.
                # Use inference_timeout so a hung model doesn't block forever.
                try:
                    for fut in concurrent.futures.as_completed(
                        futures, timeout=inference_timeout
                    ):
                        try:
                            result = fut.result()
                        except Exception as exc:
                            idx = futures[fut]
                            model_name = os.path.basename(
                                safe_models[idx]
                            ).replace(".gguf", "")
                            result = {
                                "model": model_name,
                                "model_path": safe_models[idx],
                                "path": safe_models[idx],
                                "response": f"\u274c Thread error: {exc}",
                                "error": str(exc),
                                "time_ms": 0, "tokens": 0,
                                "tokens_per_sec": 0, "quality_score": 0,
                                "ttft_ms": 0, "ram_delta_mb": 0,
                                "model_size_mb": 0, "efficiency": 0,
                                "response_chars": 0,
                            }
                            print(f"  [model-{idx}] {model_name} THREAD ERROR: {exc}", flush=True)  # xray: ignore[PY-004]

                        responses.append(result)
                        idx = futures[fut]
                        _sse("token", {
                            "model": result["model"],
                            "model_index": idx,
                            "token": result.get("response", ""),
                            "token_count": result.get("tokens", 0),
                            "elapsed_ms": round(result.get("time_ms", 0)),
                        })
                        _sse("model_done", {**result, "model_index": idx})
                except concurrent.futures.TimeoutError:
                    # Some models exceeded inference_timeout
                    for fut, idx in futures.items():
                        if not fut.done():
                            model_name = os.path.basename(
                                safe_models[idx]
                            ).replace(".gguf", "")
                            fut.cancel()
                            timeout_result = {
                                "model": model_name,
                                "model_path": safe_models[idx],
                                "path": safe_models[idx],
                                "response": f"\u23f1 Inference timed out after {inference_timeout}s",
                                "error": "timeout",
                                "time_ms": round(inference_timeout * 1000),
                                "tokens": 0, "tokens_per_sec": 0,
                                "quality_score": 0, "ttft_ms": 0,
                                "ram_delta_mb": 0, "model_size_mb": 0,
                                "efficiency": 0, "response_chars": 0,
                            }
                            responses.append(timeout_result)
                            _sse("model_done", {**timeout_result, "model_index": idx})
                            print(f"  [model-{idx}] {model_name} TIMED OUT after {inference_timeout}s", flush=True)  # xray: ignore[PY-004]

            dispatch_wall = time.time() - _dispatch_t0
            print(f"[compare] All {total_models} models done in {dispatch_wall:.1f}s wall-clock", flush=True)  # xray: ignore[PY-004]

            # Restore original model order
            responses.sort(
                key=lambda r: next(
                    (i for i, p in enumerate(safe_models) if p == r.get("model_path")), 99
                )
            )

            # Judge scoring
            if judge_model and local_models:
                _sse("judge_start", {"judge_model": judge_model})
                judge_path = self._resolve_judge_path(judge_model, local_models)
                if judge_path:
                    if not judge_system_prompt:
                        judge_system_prompt = (
                            "You are an expert evaluator. Score the model response and output "
                            "ONLY valid JSON with keys: overall (0-10), accuracy (0-10), "
                            'reasoning (0-10), instruction_following (true/false), safety ("safe"/"unsafe").'
                        )
                    responses = self._run_judge(
                        responses, prompt, judge_path, judge_system_prompt, params
                    )
                _sse("judge_done", {"responses": responses})

            # Final done event
            _sse("done", {
                "prompt": prompt,
                "models_tested": len(safe_models),
                "responses": responses,
                "judge_model": judge_model,
                "timestamp": time.time(),
            })
        except Exception as e:
            try:
                line = f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
                pass

    def _run_local_comparisons(
        self,
        prompt: str,
        system_prompt: str,
        model_paths: list[str],
        params: dict | None = None,
    ) -> list[dict]:
        """Run prompt through each local GGUF model via llama-cpp-python."""
        params = params or {}
        n_ctx = params.get("n_ctx", 4096)
        max_tokens = params.get("max_tokens", 512)
        temperature = params.get("temperature", 0.7)
        top_p = params.get("top_p", 0.95)
        repeat_penalty = params.get("repeat_penalty", 1.1)
        inference_timeout = params.get("inference_timeout", DEFAULT_INFERENCE_TIMEOUT)
        perf_profile = params.get("performance_profile", "balanced")
        max_workers, threads_per_model = _compute_parallel_plan(model_paths, n_ctx, perf_profile)

        try:
            import llama_cpp
        except ImportError:  # xray: ignore[QUAL-002]
            return [
                {
                    "model": os.path.basename(p).replace(".gguf", ""),
                    "model_path": p,
                    "path": p,
                    "response": "WARNING: llama-cpp-python not installed. Click Install in the sidebar.",
                    "error": "llama_cpp not installed",
                    "time_ms": 0,
                    "tokens": 0,
                    "tokens_per_sec": 0,
                    "quality_score": 0,
                    "ttft_ms": 0,
                    "ram_delta_mb": 0,
                }
                for p in model_paths
            ]

        def _run_one(path: str) -> dict:
            model_name = os.path.basename(path).replace(".gguf", "")
            model_size_mb = round(os.path.getsize(path) / (1024 * 1024)) if os.path.exists(path) else 0
            model_n_ctx = _effective_n_ctx_for_path(path, n_ctx, perf_profile)
            print(  # xray: ignore[PY-004]
                f"[compare] START {model_name}  ctx={model_n_ctx}  max_tokens={max_tokens}  temp={temperature}"
            )
            t0 = time.time()
            n_batch = _choose_n_batch(model_size_mb, perf_profile)
            ram_before = (
                (proc.memory_info().rss // (1024 * 1024))
                if (HAS_PSUTIL and proc is not None)
                else 0  # type: ignore[union-attr]
            )
            try:
                llm = _get_or_load_model(
                    path,
                    model_n_ctx,
                    n_threads_override=threads_per_model,
                    n_batch_override=n_batch,
                )
                # stream=False → entire C++ compute runs with GIL released
                out = llm.create_chat_completion(
                    messages=_build_messages(system_prompt, prompt, path),
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    repeat_penalty=repeat_penalty,
                    stream=False,
                )

                elapsed_ms = (time.time() - t0) * 1000
                msg = out["choices"][0]["message"]
                response_text = (msg.get("content") or "").strip()
                if not response_text and msg.get("reasoning_content"):
                    response_text = msg["reasoning_content"].strip()
                completion_tokens = out.get("usage", {}).get("completion_tokens", 0)
                if not completion_tokens:
                    completion_tokens = max(1, count_tokens(response_text))
                tps = completion_tokens / (elapsed_ms / 1000) if elapsed_ms > 0 else 0
                ttft_ms = elapsed_ms / max(completion_tokens, 1)

                ram_after = (
                    (proc.memory_info().rss // (1024 * 1024))
                    if (HAS_PSUTIL and proc is not None)
                    else 0  # type: ignore[union-attr]
                )
                ram_delta = max(0, ram_after - ram_before)

                model_size_gb = model_size_mb / 1024 if model_size_mb else 0
                efficiency = round(tps / model_size_gb, 2) if model_size_gb > 0 else 0

                print(  # xray: ignore[PY-004]
                    f"[compare] OK {model_name}  {elapsed_ms:.0f}ms  {completion_tokens}tok  {tps:.1f}t/s  eff={efficiency:.1f}t/s/GB  ram+{ram_delta}MB"
                )
                return {
                    "model": model_name,
                    "model_path": path,
                    "path": path,
                    "response": response_text,
                    "time_ms": round(elapsed_ms, 1),
                    "tokens": completion_tokens,
                    "tokens_per_sec": round(tps, 1),
                    "quality_score": 0,
                    "ttft_ms": round(ttft_ms, 1),
                    "ram_delta_mb": ram_delta,
                    "prompt_tokens": out.get("usage", {}).get("prompt_tokens", 0),
                    "model_size_mb": model_size_mb,
                    "efficiency": efficiency,
                    "response_chars": len(response_text),
                }
            except Exception as exc:
                elapsed_ms = (time.time() - t0) * 1000
                print(f"[compare] ERROR {model_name}: {exc}")  # xray: ignore[PY-004]
                return {
                    "model": model_name,
                    "model_path": path,
                    "path": path,
                    "response": f"ERROR loading/running model: {exc}",
                    "error": str(exc),
                    "time_ms": round(elapsed_ms, 1),
                    "tokens": 0,
                    "tokens_per_sec": 0,
                    "quality_score": 0,
                    "ttft_ms": 0,
                    "ram_delta_mb": 0,
                    "model_size_mb": model_size_mb,
                    "efficiency": 0,
                    "response_chars": 0,
                }

        # Run all models in parallel via ThreadPoolExecutor
        print(  # xray: ignore[PY-004]
            f"[compare] Dispatching {len(model_paths)} model(s) in parallel "
            f"(profile={perf_profile}, workers={max_workers}, threads/model={threads_per_model})...",
            flush=True,
        )
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers
        ) as pool:
            futures = {pool.submit(_run_one, p): p for p in model_paths}
            results = []
            try:
                for fut in concurrent.futures.as_completed(
                    futures, timeout=inference_timeout
                ):
                    try:
                        results.append(fut.result())
                    except Exception as exc:
                        p = futures[fut]
                        model_name = os.path.basename(p).replace(".gguf", "")
                        print(f"[compare] THREAD ERROR {model_name}: {exc}", flush=True)  # xray: ignore[PY-004]
                        results.append({
                            "model": model_name, "model_path": p, "path": p,
                            "response": f"\u274c Thread error: {exc}",
                            "error": str(exc),
                            "time_ms": 0, "tokens": 0, "tokens_per_sec": 0,
                            "quality_score": 0, "ttft_ms": 0, "ram_delta_mb": 0,
                            "model_size_mb": 0, "efficiency": 0, "response_chars": 0,
                        })
            except concurrent.futures.TimeoutError:
                for fut, p in futures.items():
                    if not fut.done():
                        model_name = os.path.basename(p).replace(".gguf", "")
                        fut.cancel()
                        print(f"[compare] TIMEOUT {model_name} after {inference_timeout}s", flush=True)  # xray: ignore[PY-004]
                        results.append({
                            "model": model_name, "model_path": p, "path": p,
                            "response": f"\u23f1 Inference timed out after {inference_timeout}s",
                            "error": "timeout",
                            "time_ms": round(inference_timeout * 1000),
                            "tokens": 0, "tokens_per_sec": 0, "quality_score": 0,
                            "ttft_ms": 0, "ram_delta_mb": 0,
                            "model_size_mb": 0, "efficiency": 0, "response_chars": 0,
                        })

        # Restore original model order
        path_order = {p: i for i, p in enumerate(model_paths)}
        results.sort(key=lambda r: path_order.get(r.get("model_path", ""), 99))
        return results

    def _resolve_judge_path(self, judge_model: str, local_models: list[str]) -> str | None:
        """Return the filesystem path to use as judge model."""
        if judge_model == "local:best":
            # Pick the LARGEST model as judge — quality matters more than speed
            # for scoring. A 135M model can't judge a 9B model's output.
            # Exclude non-text models (mmproj, embedding) by name.
            candidates = [
                p for p in local_models
                if os.path.exists(p)
                and not any(skip in os.path.basename(p).lower() for skip in ("mmproj", "embed", "ggml-model"))
            ]
            if not candidates:
                candidates = local_models
            best = max(
                candidates,
                key=lambda p: os.path.getsize(p) if os.path.exists(p) else 0,
                default=None,
            )
            return best
        if judge_model and not judge_model.startswith("online:"):
            # Could be an explicit path passed through
            if os.path.exists(judge_model):
                return judge_model
            # Try to match by basename against local_models
            for p in local_models:
                if os.path.basename(p).lower().startswith(judge_model.lower()):
                    return p
        return None

    def _run_judge(
        self,
        responses: list[dict],
        original_prompt: str,
        judge_path: str | list[str],
        judge_system_prompt: str,
        params: dict,
        reference_answer: str = "",
    ) -> list[dict]:
        """Score each response using one or more judge models.

        Supports:
          - E1: Structured JSON output via response_format
          - E3: Multi-judge consensus (pass list of paths)
          - E4: Reference-guided judging (optional reference_answer)
        """
        try:
            import llama_cpp
        except ImportError:  # xray: ignore[QUAL-002]
            return responses

        # Normalise judge_path to list for multi-judge support (E3)
        judge_paths = judge_path if isinstance(judge_path, list) else [judge_path]

        for idx, r in enumerate(responses):
            if r.get("error"):
                continue

            all_judge_scores: list[float] = []
            all_judge_details: list[dict] = []

            for jp in judge_paths:
                judge_name = os.path.basename(jp).replace(".gguf", "")
                try:
                    llm = _get_or_load_model(jp, min(params.get("n_ctx", 4096), 8192))
                except Exception as load_err:
                    print(f"[judge] WARN cannot load {judge_name}: {load_err}")  # xray: ignore[PY-004]
                    continue

                # Build user message — inject reference answer when provided (E4)
                user_msg = f"Original question: {original_prompt}\n\n"
                if reference_answer:
                    user_msg += f"Reference answer:\n{reference_answer}\n\n"
                user_msg += f"Model response:\n{r.get('response', '')}"

                for attempt in range(2):  # retry once on failure
                    try:
                        sys_prompt = (
                            judge_system_prompt
                            if attempt == 0
                            else (
                                "Rate the response quality 0-10. Output ONLY a JSON "
                                'object: {"overall": <number>}'
                            )
                        )
                        # E1: request structured JSON output
                        create_kwargs: dict = dict(
                            messages=_build_messages(sys_prompt, user_msg, jp),
                            max_tokens=512,
                            temperature=0.1,
                            stream=False,
                        )
                        try:
                            create_kwargs["response_format"] = {"type": "json_object"}
                            out = llm.create_chat_completion(**create_kwargs)
                        except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
                            # Fallback for models that don't support response_format
                            create_kwargs.pop("response_format", None)
                            out = llm.create_chat_completion(**create_kwargs)

                        _jmsg = out["choices"][0]["message"]  # type: ignore[index]
                        raw = (_jmsg.get("content") or _jmsg.get("reasoning_content") or "").strip()
                        if not raw:
                            print(f"[judge] WARN empty response from {judge_name} for {r['model']}", flush=True)  # xray: ignore[PY-004]
                        else:
                            print(f"[judge] raw ({judge_name}, len={len(raw)}): {raw[:200]}", flush=True)  # xray: ignore[PY-004]
                        jd = extract_judge_scores(raw)
                        score = float(jd.get("overall", 0))
                        jd["judge_model"] = judge_name
                        all_judge_scores.append(score)
                        all_judge_details.append(jd)
                        break
                    except Exception as je:
                        print(  # xray: ignore[PY-004]
                            f"[judge] WARN attempt {attempt+1} "
                            f"failed for {r['model']} ({judge_name}): {je}"
                        )

            if all_judge_scores:
                # Consensus: average across judges (E3)
                avg_score = round(sum(all_judge_scores) / len(all_judge_scores), 1)
                # Merge detail from first judge, add consensus info
                detail = all_judge_details[0].copy()
                detail["overall"] = avg_score
                if len(all_judge_details) > 1:
                    detail["consensus"] = {
                        "num_judges": len(all_judge_details),
                        "scores": [round(s, 1) for s in all_judge_scores],
                        "judges": [d.get("judge_model", "?") for d in all_judge_details],
                        "spread": round(max(all_judge_scores) - min(all_judge_scores), 1),
                    }
                r["judge_score"] = avg_score
                r["quality_score"] = avg_score
                r["judge_detail"] = detail
                print(f"[judge] OK {r['model']}  score={avg_score:.1f}"  # xray: ignore[PY-004]
                      + (f" ({len(all_judge_scores)} judges)" if len(all_judge_scores) > 1 else ""))
            else:
                r["judge_score"] = 0
                r["quality_score"] = 0
                r["judge_detail"] = {
                    "overall": 0,
                    "error": "Judge failed after retries",
                }
        return responses

    def _handle_chat(self, data: dict) -> None:
        model_path = data.get("model_path", "").strip()
        if not model_path or not os.path.isfile(model_path):
            self._send_json(400, {"error": "Model file not found"})
            return
        # Validate path is inside configured model directories
        if not _is_safe_model_path(model_path, self.model_dirs):
            self._send_json(403, {"error": "Model path not allowed"})
            return
        system = data.get("system", "You are a helpful assistant.")
        messages = data.get("messages", [])
        max_tokens = min(int(data.get("max_tokens", 512)), 2048)
        temperature = float(data.get("temperature", 0.4))
        try:
            # Use zen_core_libs InProcessAdapter (single source of truth)
            global _zen_adapter  # xray: ignore[PY-006]
            if get_inprocess_adapter is not None:
                if _zen_adapter is None:
                    _zen_adapter = get_inprocess_adapter(max_models=8, default_n_ctx=4096)
                reply = _zen_adapter.chat(
                    model_path,
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            else:
                # Fallback: direct llama_cpp if zen_core_libs unavailable
                import llama_cpp
                llm = _get_or_load_model(model_path, 4096)
                full_messages = _build_messages(system, messages[0]["content"] if messages else "", model_path) if len(messages) <= 1 else [{"role": "system", "content": system}] + messages
                try:
                    out = llm.create_chat_completion(
                        full_messages, max_tokens=max_tokens, temperature=temperature, stream=False,
                    )
                except ValueError:  # xray: ignore[QUAL-002]
                    if full_messages and full_messages[0].get("role") == "system":
                        sys_text = full_messages.pop(0)["content"]
                        if full_messages and full_messages[0].get("role") == "user":
                            full_messages[0]["content"] = sys_text + "\n\n" + full_messages[0]["content"]
                        else:
                            full_messages.insert(0, {"role": "user", "content": sys_text})
                    out = llm.create_chat_completion(
                        full_messages, max_tokens=max_tokens, temperature=temperature, stream=False,
                    )
                _cmsg = out["choices"][0]["message"]
                reply = (_cmsg.get("content") or _cmsg.get("reasoning_content") or "").strip()
            self._send_json(200, {"response": reply})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_discover_models(self) -> None:
        """GET /__discover-models?q=&sort=trending&limit=30"""
        qs = parse_qs(urlparse(self.path).query)

    # ── zen_eval handler methods ──────────────────────────────────────────

    def _handle_prompts_get(self) -> None:
        """GET /__prompts?name=X&version=N&alias=A"""
        qs = parse_qs(urlparse(self.path).query)
        name = qs.get("name", [None])[0]
        version = qs.get("version", [None])[0]
        alias = qs.get("alias", [None])[0]
        if name and (version or alias):
            p = zen_eval.load_prompt(
                name, version=int(version) if version else None, alias=alias
            )
            if p:
                from dataclasses import asdict
                self._send_json(200, asdict(p))
            else:
                self._send_json(404, {"error": "Prompt not found"})
        else:
            self._send_json(200, zen_eval.list_prompts(name))

    def _handle_prompts_post(self, data: dict) -> None:
        """POST /__prompts — register a new prompt version."""
        name = data.get("name", "").strip()
        template = data.get("template", "").strip()
        if not name or not template:
            self._send_json(400, {"error": "name and template required"})
            return
        p = zen_eval.register_prompt(
            name=name,
            template=template,
            system_prompt=data.get("system_prompt", ""),
            temperature=float(data.get("temperature", 0.7)),
            max_tokens=int(data.get("max_tokens", 512)),
            commit_msg=data.get("commit_msg", ""),
        )
        from dataclasses import asdict
        self._send_json(201, asdict(p))

    def _handle_prompt_alias(self, data: dict) -> None:
        """POST /__prompts/alias — set or delete an alias."""
        name = data.get("name", "").strip()
        alias = data.get("alias", "").strip()
        if not name or not alias:
            self._send_json(400, {"error": "name and alias required"})
            return
        if data.get("delete"):
            ok = zen_eval.delete_alias(name, alias)
            self._send_json(200 if ok else 404, {"ok": ok})
        else:
            version = data.get("version")
            if version is None:
                self._send_json(400, {"error": "version required"})
                return
            ok = zen_eval.set_alias(name, alias, int(version))
            self._send_json(200 if ok else 404, {"ok": ok})

    def _handle_feedback_get(self) -> None:
        """GET /__feedback?judge=X&limit=N or /__feedback/stats?judge=X"""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/__feedback/stats":
            judge = qs.get("judge", [""])[0]
            if not judge:
                self._send_json(400, {"error": "judge parameter required"})
                return
            self._send_json(200, zen_eval.get_alignment_stats(judge))
        else:
            judge = qs.get("judge", [None])[0]
            limit = int(qs.get("limit", [50])[0])  # xray: ignore[QUAL-003]
            self._send_json(200, zen_eval.get_feedback_history(judge, limit))

    def _handle_feedback_post(self, data: dict) -> None:
        """POST /__feedback — record judge feedback."""
        required = ["judge_name", "prompt", "response", "auto_score"]
        if not all(data.get(k) is not None for k in required):
            self._send_json(400, {"error": f"Required: {', '.join(required)}"})
            return
        fid = zen_eval.record_feedback(
            judge_name=data["judge_name"],
            prompt=data["prompt"],
            response=data["response"],
            auto_score=float(data["auto_score"]),
            human_score=float(data["human_score"]) if data.get("human_score") is not None else None,
            feedback=data.get("feedback", ""),
        )
        self._send_json(201, {"id": fid})

    def _handle_feedback_human(self, data: dict) -> None:
        """POST /__feedback/human — update human score for existing feedback."""
        fid = data.get("id")
        human_score = data.get("human_score")
        if fid is None or human_score is None:
            self._send_json(400, {"error": "id and human_score required"})
            return
        ok = zen_eval.update_human_score(
            int(fid), float(human_score), data.get("feedback", "")
        )
        self._send_json(200 if ok else 404, {"ok": ok})

    def _handle_judge_conversation(self, data: dict) -> None:
        """POST /__judge/conversation — run multi-turn judges on a conversation."""
        turns_raw = data.get("turns", [])
        if len(turns_raw) < 2:
            self._send_json(400, {"error": "At least 2 turns required"})
            return

        conv_id = data.get("conversation_id", f"conv_{int(time.time()*1000)}")
        model_name = data.get("model_name", "unknown")
        judges = data.get("judges", ["UserFrustration", "KnowledgeRetention"])

        turns = [
            zen_eval.TurnData(
                role=t.get("role", "user"),
                content=t.get("content", ""),
                turn_num=i,
                metadata=t.get("metadata", {}),
            )
            for i, t in enumerate(turns_raw)
        ]
        ctx = zen_eval.ConversationContext(
            conversation_id=conv_id,
            model_name=model_name,
            turns=turns,
            metadata=data.get("metadata", {}),
        )

        results = {}
        from dataclasses import asdict
        if "UserFrustration" in judges:
            results["UserFrustration"] = asdict(zen_eval.judge_user_frustration(ctx))
        if "KnowledgeRetention" in judges:
            results["KnowledgeRetention"] = asdict(zen_eval.judge_knowledge_retention(ctx))

        # Optionally persist the conversation
        if data.get("save", False):
            zen_eval.save_conversation(ctx)

        self._send_json(200, {"conversation_id": conv_id, "results": results})

    def _handle_judge_toolcall(self, data: dict) -> None:
        """POST /__judge/toolcall — evaluate tool call correctness & efficiency."""
        actual_raw = data.get("actual_calls", [])
        expected_raw = data.get("expected_calls", [])

        actual = [
            zen_eval.ToolCall(
                name=c.get("name", ""),
                arguments=c.get("arguments", {}),
                result=c.get("result"),
            )
            for c in actual_raw
        ]
        expected = [
            zen_eval.ToolCallExpectation(
                name=e.get("name", ""),
                arguments=e.get("arguments"),
                required=e.get("required", True),
                order=e.get("order"),
            )
            for e in expected_raw
        ]

        from dataclasses import asdict
        results = {}

        judges = data.get("judges", ["ToolCallCorrectness", "ToolCallEfficiency"])
        if "ToolCallCorrectness" in judges:
            results["ToolCallCorrectness"] = asdict(
                zen_eval.judge_tool_call_correctness(actual, expected)
            )
        if "ToolCallEfficiency" in judges:
            results["ToolCallEfficiency"] = asdict(
                zen_eval.judge_tool_call_efficiency(
                    actual,
                    min_expected=data.get("min_calls", 1),
                    max_expected=data.get("max_calls"),
                )
            )

        self._send_json(200, {"results": results})

    def _handle_gateway_routes_get(self) -> None:
        """GET /__gateway/routes — list all routes."""
        gw = zen_eval.get_gateway()
        self._send_json(200, gw.list_routes())

    def _handle_gateway_routes_post(self, data: dict) -> None:
        """POST /__gateway/routes — add/update a route."""
        name = data.get("name", "").strip()
        strategy = data.get("strategy", "").strip()
        models = data.get("models", [])
        if not name or not strategy or not models:
            self._send_json(400, {"error": "name, strategy, and models required"})
            return
        if strategy not in ("round_robin", "weighted", "fallback", "ab_test"):
            self._send_json(400, {"error": "strategy must be: round_robin, weighted, fallback, ab_test"})
            return
        gw = zen_eval.get_gateway()
        route = zen_eval.GatewayRoute(
            name=name, strategy=strategy, models=models,
            config=data.get("config", {}),
            enabled=data.get("enabled", True),
        )
        gw.add_route(route)
        self._send_json(201, {"ok": True, "route": name})

    def _handle_gateway_resolve(self, data: dict) -> None:
        """POST /__gateway/resolve — resolve a route to a model."""
        route_name = data.get("route", "").strip()
        if not route_name:
            self._send_json(400, {"error": "route name required"})
            return
        gw = zen_eval.get_gateway()
        model = gw.resolve(route_name)
        if model:
            self._send_json(200, {"model": model, "route": route_name})
        else:
            self._send_json(404, {"error": f"Route '{route_name}' not found"})

    def _handle_gateway_stats(self) -> None:
        """GET /__gateway/stats?route=X"""
        qs = parse_qs(urlparse(self.path).query)
        route = qs.get("route", [""])[0]
        if not route:
            self._send_json(400, {"error": "route parameter required"})
            return
        gw = zen_eval.get_gateway()
        self._send_json(200, gw.get_route_stats(route))
        query = qs.get("q", [""])[0][:200]  # cap query length
        sort = qs.get("sort", ["trending"])[0]
        if sort not in ("trending", "downloads", "newest", "likes"):
            sort = "trending"
        try:
            limit = min(int(qs.get("limit", ["30"])[0]), 60)
        except (ValueError, TypeError):
            limit = 30
        results = _discover_hf_models(query, sort, limit)
        self._send_json(200, {"models": results, "cached": bool(_discovery_cache)})

    def _handle_scout(self) -> None:
        """GET /__scout?category=all&limit=20 — Internet Scout for new models."""
        qs = parse_qs(urlparse(self.path).query)
        category = qs.get("category", ["all"])[0][:50]
        try:
            limit = min(int(qs.get("limit", ["20"])[0]), 60)
        except (ValueError, TypeError):
            limit = 20
        results = _scout_hf_trending(category, limit)
        self._send_json(200, {
            "models": results,
            "category": category,
            "categories": {k: {"icon": v["icon"], "desc": v["desc"]}
                          for k, v in _TOOL_CATEGORIES.items()},
        })

    def _handle_tool_ecosystem(self) -> None:
        """GET /__tool-ecosystem — Discover AI tool categories with top models."""
        ecosystem = _scout_tool_ecosystem()
        self._send_json(200, ecosystem)

    # ── Skill Lab Handlers ───────────────────────────────────────────────────

    def _handle_skill_lab_examples(self) -> None:
        """GET /__skill-lab/examples — List bundled example skills."""
        examples = skill_optimizer.list_example_skills()
        self._send_json(200, {"examples": examples})

    def _handle_skill_lab_load_example(self, data: dict) -> None:
        """POST /__skill-lab/examples/{name}/load — Load an example skill."""
        # Extract example name from path: /__skill-lab/examples/{name}/load
        parts = self.path.split("/")
        # ['', '__skill-lab', 'examples', '{name}', 'load']
        if len(parts) < 5:
            self._send_json(400, {"error": "Invalid path"})
            return
        example_name = parts[3]
        try:
            skill_files, file_list = skill_optimizer.load_example_skill(example_name)
            result = skill_optimizer.create_skill_session(skill_files, file_list)
            if "error" in result:
                self._send_json(400, result)
            else:
                self._send_json(200, result)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})

    def _handle_skill_lab_upload(self, data: dict) -> None:
        """POST /__skill-lab/upload — Upload skill as base64-encoded zip or raw files."""
        import base64
        zip_b64 = data.get("zip_base64", "")
        raw_files = data.get("files", {})

        if zip_b64:
            try:
                raw_bytes = base64.b64decode(zip_b64)
                skill_files, file_list = skill_optimizer.extract_upload(raw_bytes)
            except (ValueError, Exception) as e:
                self._send_json(400, {"error": str(e)})
                return
        elif raw_files and isinstance(raw_files, dict):
            # Direct file upload: {"filename": "content", ...}
            skill_files = {}
            file_list = []
            for name, content in raw_files.items():
                if not skill_optimizer._is_safe_path(name):
                    continue
                if not skill_optimizer._is_allowed_file(name):
                    continue
                if len(content) > skill_optimizer.MAX_FILE_SIZE:
                    continue
                if len(file_list) >= skill_optimizer.MAX_FILE_COUNT:
                    break
                skill_files[name] = content
                file_list.append(name)
        else:
            self._send_json(400, {"error": "Provide zip_base64 or files dict"})
            return

        if not skill_files:
            self._send_json(400, {"error": "No valid files found in upload"})
            return

        result = skill_optimizer.create_skill_session(skill_files, file_list)
        if "error" in result:
            self._send_json(400, result)
        else:
            self._send_json(200, result)

    def _handle_skill_lab_analyze(self, data: dict) -> None:
        """POST /__skill-lab/analyze — Generate scenarios + evals for a skill."""
        session_id = data.get("session_id", "")
        model_path = data.get("model_path", "")
        gemini_api_key = data.get("gemini_api_key", "")

        session = skill_optimizer.get_skill_session(session_id)
        if not session:
            self._send_json(404, {"error": "Session not found"})
            return

        if not model_path and not gemini_api_key:
            self._send_json(400, {"error": "Provide model_path or gemini_api_key"})
            return

        # Validate model path if using local model
        if model_path and not _is_safe_model_path(model_path, self.model_dirs):
            self._send_json(400, {"error": "Invalid model path"})
            return

        try:
            optimizer = skill_optimizer.create_optimizer(
                model_path=model_path or "",
                gemini_api_key=gemini_api_key,
            )
            analysis = optimizer.analyze_skill(session["skill_files"])
            session["scenarios"] = analysis.get("scenarios", [])
            session["evals"] = analysis.get("evals", [])
            session["status"] = "analyzed"
            self._send_json(200, {
                "scenarios": session["scenarios"],
                "evals": session["evals"],
            })
        except Exception as e:
            print(f"[skill-lab] Analysis error: {e}", flush=True)  # xray: ignore[PY-004]
            self._send_json(500, {"error": "Analysis failed. Check model availability."})

    def _handle_skill_lab_update_config(self, data: dict) -> None:
        """POST /__skill-lab/update-config — Save user-edited scenarios/evals."""
        session_id = data.get("session_id", "")
        session = skill_optimizer.get_skill_session(session_id)
        if not session:
            self._send_json(404, {"error": "Session not found"})
            return
        session["scenarios"] = data.get("scenarios", session.get("scenarios", []))
        session["evals"] = data.get("evals", session.get("evals", []))
        session["status"] = "configured"
        self._send_json(200, {"status": "ok"})

    def _handle_skill_lab_start(self, data: dict) -> None:
        """POST /__skill-lab/start/{session_id} — Start optimization in background."""
        parts = self.path.split("/")
        if len(parts) < 4:
            self._send_json(400, {"error": "Invalid path"})
            return
        session_id = parts[3]
        session = skill_optimizer.get_skill_session(session_id)
        if not session:
            self._send_json(404, {"error": "Session not found"})
            return
        if not session.get("scenarios") or not session.get("evals"):
            self._send_json(400, {"error": "Configure scenarios and evals first"})
            return
        if session.get("status") == "running":
            self._send_json(400, {"error": "Optimization already running"})
            return

        model_path = data.get("model_path", "")
        gemini_api_key = data.get("gemini_api_key", "")
        max_rounds = min(max(1, int(data.get("max_rounds", 10))), skill_optimizer.MAX_OPTIMIZATION_ROUNDS)
        patience = max(0, int(data.get("patience", 0)))

        if model_path and not _is_safe_model_path(model_path, self.model_dirs):
            self._send_json(400, {"error": "Invalid model path"})
            return

        if not model_path and not gemini_api_key:
            self._send_json(400, {"error": "Provide model_path or gemini_api_key"})
            return

        session["status"] = "running"
        session["stop_requested"] = False
        session["experiments"] = []
        session["sse_events"] = []  # buffer for SSE streaming

        def _run():
            try:
                optimizer = skill_optimizer.create_optimizer(
                    model_path=model_path or "",
                    gemini_api_key=gemini_api_key,
                )

                def callback(event):
                    session["sse_events"].append(event)
                    # Track experiments for status polling
                    if event["type"] == "baseline":
                        session["experiments"].append({
                            "experiment_id": 0,
                            "pass_rate": event["data"].get("score", 0),
                            "status": "baseline",
                            "per_eval": event["data"].get("per_eval", []),
                        })
                    elif event["type"] == "experiment_result":
                        session["experiments"].append({
                            "experiment_id": event["data"].get("round", 0),
                            "pass_rate": event["data"].get("score_after", 0),
                            "status": event["data"].get("status", ""),
                            "per_eval": event["data"].get("per_eval", []),
                            "description": event["data"].get("description", ""),
                            "strategy": event["data"].get("strategy_type", ""),
                        })
                    elif event["type"] == "complete":
                        d = event["data"]
                        ml = d.get("mutation_log", [])
                        session["final_result"] = {
                            "baseline_score": d.get("baseline_score", 0),
                            "final_score": d.get("final_score", 0),
                            "improved_skill_content": d.get("improved_skill_content", ""),
                            "original_skill_content": session.get("original_skill_content", ""),
                            "score_history": d.get("score_history", []),
                            "experiments_run": len(ml),
                            "kept": sum(1 for m in ml if m.get("kept")),
                            "discarded": sum(1 for m in ml if not m.get("kept")),
                            "changelog": [
                                {
                                    "description": m.get("description", ""),
                                    "reasoning": m.get("diagnosis", ""),
                                    "status": "keep" if m.get("kept") else "discard",
                                    "score_before": m.get("score_before", 0),
                                    "score_after": m.get("score_after", 0),
                                    "strategy": m.get("strategy_type", ""),
                                }
                                for m in ml
                            ],
                            "mutation_log": ml,
                            "strategy_stats": d.get("strategy_stats", {}),
                            "dpo_pairs": d.get("dpo_pairs", []),
                            "sft_examples": d.get("sft_examples", []),
                        }
                        session["current_skill_content"] = d.get("improved_skill_content", "")

                result = optimizer.optimize(
                    skill_files=session["skill_files"],
                    scenarios=session["scenarios"],
                    evals=session["evals"],
                    max_rounds=max_rounds,
                    patience=patience,
                    callback=callback,
                )
                session["status"] = "complete"
                session["current_skill_content"] = result.get("improved_skill_content", "")
            except Exception as e:
                print(f"[skill-lab] Optimization error: {e}", flush=True)  # xray: ignore[PY-004]
                session["status"] = "error"
                session["error"] = str(e)
                session["sse_events"].append({"type": "error", "data": {"message": str(e)}})

        # Run in background thread
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self._send_json(200, {"status": "started", "session_id": session_id})

    def _handle_skill_lab_stop(self, data: dict) -> None:
        """POST /__skill-lab/stop/{session_id} — Stop optimization."""
        parts = self.path.split("/")
        if len(parts) < 4:
            self._send_json(400, {"error": "Invalid path"})
            return
        session_id = parts[3]
        session = skill_optimizer.get_skill_session(session_id)
        if not session:
            self._send_json(404, {"error": "Session not found"})
            return
        session["stop_requested"] = True
        session["status"] = "stopped"
        self._send_json(200, {"status": "stopped"})

    def _handle_skill_lab_stream(self) -> None:
        """GET /__skill-lab/stream/{session_id} — SSE stream of optimization progress."""
        parts = self.path.split("/")
        if len(parts) < 4:
            self._send_json(400, {"error": "Invalid path"})
            return
        session_id = parts[3]
        session = skill_optimizer.get_skill_session(session_id)
        if not session:
            self._send_json(404, {"error": "Session not found"})
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self._cors_headers()
            self.end_headers()

            sent_index = 0
            _disconnected = False

            while not _disconnected:
                events = session.get("sse_events", [])
                while sent_index < len(events):
                    event = events[sent_index]
                    sent_index += 1
                    try:
                        line = f"data: {json.dumps(event)}\n\n"
                        self.wfile.write(line.encode("utf-8"))
                        self.wfile.flush()
                    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                        _disconnected = True
                        break

                    if event.get("type") in ("complete", "error"):
                        _disconnected = True
                        break

                if session.get("status") in ("complete", "error", "stopped") and sent_index >= len(events):
                    break

                if not _disconnected:
                    time.sleep(0.3)  # poll interval

        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _handle_skill_lab_download(self) -> None:
        """GET /__skill-lab/download/{session_id} — Download improved skill as zip."""
        import base64
        parts = self.path.split("/")
        if len(parts) < 4:
            self._send_json(400, {"error": "Invalid path"})
            return
        session_id = parts[3]
        session = skill_optimizer.get_skill_session(session_id)
        if not session:
            self._send_json(404, {"error": "Session not found"})
            return
        if not session.get("current_skill_content"):
            self._send_json(400, {"error": "No improved skill available"})
            return

        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for filename, content in session["skill_files"].items():
                if filename == session.get("skill_key", "SKILL.md"):
                    zf.writestr(filename, session["current_skill_content"])
                else:
                    zf.writestr(filename, content)
            # Add changelog
            if session.get("final_result"):
                zf.writestr("CHANGELOG.json", json.dumps(
                    session["final_result"].get("changelog", []), indent=2
                ))
        buf.seek(0)
        body = buf.read()

        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", "attachment; filename=improved_skill.zip")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _handle_skill_lab_status(self) -> None:
        """GET /__skill-lab/status/{session_id} — Poll-based status."""
        parts = self.path.split("/")
        if len(parts) < 4:
            self._send_json(400, {"error": "Invalid path"})
            return
        session_id = parts[3]
        session = skill_optimizer.get_skill_session(session_id)
        if not session:
            self._send_json(404, {"error": "Session not found"})
            return
        self._send_json(200, {
            "status": session.get("status", "unknown"),
            "experiments": session.get("experiments", []),
            "error": session.get("error"),
            "final_result": session.get("final_result"),
        })

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        # Allow localhost origins (any port), file:// (null), and empty (same-origin)
        if origin in ("", "null") or re.match(
            r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", origin
        ):
            allowed = origin if origin and origin != "null" else "http://127.0.0.1:8123"
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Vary", "Origin")
        # External origins: omit ACAO header → browser blocks the request
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, data: Any) -> None:
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass  # Client disconnected before response was sent

    def log_message(self, format, *args):
        msg = format % args
        if any(k in msg for k in ("ConnectionAbortedError", "BrokenPipeError", "ConnectionResetError")):
            return  # Silently ignore client-disconnect noise
        print(f"[{self.log_date_time_string()}] {msg}")  # xray: ignore[PY-004]


def _run_download(job_id: str, model: str, dest: str) -> None:
    """Background download worker — updates _download_jobs[job_id]."""

    def _upd(**kw):
        with _download_lock:
            _download_jobs[job_id].update(kw)

    _upd(state="downloading", progress=5, message="Starting…")
    try:
        os.makedirs(dest, exist_ok=True)
        from huggingface_hub import hf_hub_download, snapshot_download

        # Determine repo_id and filename
        if model.lower().startswith("http"):  # nosec B310 — URL validated below
            # Validate URL for SSRF prevention
            if not validate_download_url(model):
                _upd(state="error", progress=0,
                     message="Download URL not allowed (must be HTTPS from trusted hosts)",
                     error="URL validation failed")
                return
            # Direct URL — stream download with progress
            import urllib.request as _ur

            filename = model.rstrip("/").split("/")[-1]
            out_path = os.path.join(dest, filename)
            _upd(state="downloading", progress=10, message=f"Connecting to {filename}…")

            def _reporthook(block_num, block_size, total_size):
                if total_size > 0:
                    pct = min(99, int(block_num * block_size * 100 / total_size))
                    _upd(progress=pct, message=f"Downloading {filename}… {pct}%")

            _ur.urlretrieve(model, out_path, reporthook=_reporthook)  # nosec B310

        elif model.count("/") >= 2:
            # "owner/repo/file.gguf"
            parts = model.split("/", 2)
            repo_id = "/".join(parts[:2])
            filename = parts[2]
            _upd(
                state="downloading",
                progress=15,
                message=f"Fetching {filename} from {repo_id}…",
            )
            out_path = hf_hub_download(  # nosec B615
                repo_id=repo_id, filename=filename, local_dir=dest
            )

        elif model.count("/") == 1:
            # "owner/repo" — snapshot (all files in repo)
            _upd(
                state="downloading",
                progress=15,
                message=f"Fetching repo metadata for {model}…",
            )
            out_path = snapshot_download(  # nosec B615
                repo_id=model,
                local_dir=os.path.join(dest, model.split("/")[-1]),
                ignore_patterns=["*.bin", "*.pt", "*.safetensors"],  # GGUF repos only
            )
        else:
            _upd(
                state="error",
                progress=0,
                message="Use format: owner/repo/file.gguf or a direct URL",
                error="Invalid format",
            )
            return

        _upd(state="done", progress=100, message="Download complete", path=str(out_path))
        print(f"[download] {job_id} DONE → {out_path}")  # xray: ignore[PY-004]
    except Exception as exc:
        _upd(state="error", progress=0, message=str(exc), error=str(exc))
        print(f"[download] {job_id} ERROR: {exc}")  # xray: ignore[PY-004]


def _run_install(job_id: str, pip_cmd: str) -> None:
    """Background install worker — updates _install_jobs[job_id] with live log."""
    import shlex
    import subprocess
    import sys as _sys

    def _upd(**kw):
        with _install_lock:
            _install_jobs[job_id].update(kw)

    _upd(state="running", log="", error="", status_text="Starting pip…")
    try:
        parts = shlex.split(pip_cmd)
        # Always use the same Python executable as the running backend
        if parts[0] in ("pip", "pip3"):
            parts = [_sys.executable, "-m", "pip"] + parts[1:]
        process = subprocess.Popen(
            parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        accumulated = ""
        for line in iter(process.stdout.readline, ""):  # type: ignore[union-attr]
            accumulated += line
            short = line.strip()[:100] if line.strip() else "Installing…"
            _upd(log=accumulated, status_text=short)
        process.wait()
        if process.returncode == 0:
            _upd(
                state="done",
                status_text="Installation complete!",
                log=accumulated + "\n✅ Done! Restart the backend to activate.",
            )
            print(f"[install] {job_id} DONE")  # xray: ignore[PY-004]
        else:
            _upd(
                state="error",
                error=f"pip exited with code {process.returncode}",
                log=accumulated,
            )
            print(f"[install] {job_id} FAILED (code {process.returncode})")  # xray: ignore[PY-004]
    except Exception as exc:
        _upd(state="error", error=str(exc), log=f"ERROR: {exc}")
        print(f"[install] {job_id} EXCEPTION: {exc}")  # xray: ignore[PY-004]


# ─ Internet Scout ────────────────────────────────────────────────────────────
# Scours HuggingFace for new high-quality GGUF models and relevant AI tools
# that could enhance the user's workflow (voice, OCR, formatting, etc.).

_SCOUT_CACHE: dict[str, dict] = {}          # key → {ts, data}
_SCOUT_TTL = 600                             # 10-minute cache
_scout_lock = threading.Lock()

# Categories of tools the scout searches for
_TOOL_CATEGORIES = {
    "voice":     {"search": "whisper speech-to-text voice GGUF", "icon": "🎙️", "desc": "Voice/Speech-to-Text"},
    "ocr":       {"search": "OCR document extraction GGUF",      "icon": "📷", "desc": "OCR & Document Understanding"},
    "vision":    {"search": "vision multimodal image GGUF",      "icon": "👁️", "desc": "Vision & Image Understanding"},
    "embedding": {"search": "embedding sentence-similarity GGUF","icon": "🔗", "desc": "Embeddings & Retrieval"},
    "code":      {"search": "code generation programming GGUF",  "icon": "💻", "desc": "Code Generation"},
    "agent":     {"search": "function-calling agent tool-use GGUF","icon":"🤖","desc": "Agents & Tool Use"},
    "translate": {"search": "translation multilingual GGUF",     "icon": "🌍", "desc": "Translation & Multilingual"},
    "reasoning": {"search": "reasoning math logic GGUF",         "icon": "🧠", "desc": "Reasoning & Math"},
    "medical":   {"search": "medical clinical biomedical GGUF",  "icon": "🏥", "desc": "Medical & Clinical"},
    "small":     {"search": "small tiny efficient edge GGUF",    "icon": "⚡", "desc": "Small & Efficient"},
}


def _scout_hf_trending(category: str = "all", limit: int = 20) -> list[dict]:
    """Discover trending GGUF models on HuggingFace, optionally filtered."""
    cache_key = f"scout|{category}|{limit}"
    with _scout_lock:
        cached = _SCOUT_CACHE.get(cache_key)
        if cached and time.time() - cached["ts"] < _SCOUT_TTL:
            return cached["data"]

    results: list[dict] = []
    try:
        from huggingface_hub import HfApi
        api = HfApi()

        if category != "all" and category in _TOOL_CATEGORIES:
            search_q = _TOOL_CATEGORIES[category]["search"]
        else:
            search_q = "GGUF"

        raw = list(api.list_models(
            search=search_q,
            filter="gguf",
            sort="trendingScore",
            limit=min(limit, 60),
        ))

        for m in raw:
            author = (m.id or "").split("/")[0] if "/" in (m.id or "") else ""
            tags = list(getattr(m, "tags", []) or [])
            downloads = getattr(m, "downloads", 0) or 0
            likes = getattr(m, "likes", 0) or 0
            pipeline = getattr(m, "pipeline_tag", "") or ""

            # Auto-classify capabilities from tags
            caps = []
            tag_str = " ".join(tags).lower()
            if any(k in tag_str for k in ("code", "starcoder", "codellama", "deepseek-coder")):
                caps.append("code")
            if any(k in tag_str for k in ("medical", "bio", "clinical", "med")):
                caps.append("medical")
            if any(k in tag_str for k in ("vision", "multimodal", "image", "llava")):
                caps.append("vision")
            if any(k in tag_str for k in ("embedding", "sentence", "retrieval")):
                caps.append("embedding")
            if any(k in tag_str for k in ("whisper", "speech", "voice", "audio")):
                caps.append("voice")
            if any(k in tag_str for k in ("math", "reason", "logic")):
                caps.append("reasoning")
            if any(k in tag_str for k in ("translation", "multilingual", "nllb")):
                caps.append("translate")
            if any(k in tag_str for k in ("function", "tool", "agent")):
                caps.append("agent")
            if not caps:
                caps.append("chat")

            results.append({
                "id": m.id,
                "author": author,
                "trusted": author in _TRUSTED_QUANTIZERS,
                "downloads": downloads,
                "likes": likes,
                "lastModified": str(getattr(m, "last_modified", "") or ""),
                "tags": tags,
                "pipeline": pipeline,
                "capabilities": caps,
                "trending_score": getattr(m, "trending_score", 0) or 0,
            })

        with _scout_lock:
            _SCOUT_CACHE[cache_key] = {"ts": time.time(), "data": results}
        return results

    except Exception as exc:
        return [{"error": f"Scout failed: {exc}"}]


def _scout_tool_ecosystem() -> dict:
    """Return curated list of AI tool categories with HF trending models."""
    ecosystem: dict[str, dict] = {}
    try:
        from huggingface_hub import HfApi
        api = HfApi()

        for cat_key, cat_info in _TOOL_CATEGORIES.items():
            try:
                raw = list(api.list_models(
                    search=cat_info["search"],
                    filter="gguf",
                    sort="trendingScore",
                    limit=5,
                ))
                models = []
                for m in raw:
                    author = (m.id or "").split("/")[0] if "/" in (m.id or "") else ""
                    models.append({
                        "id": m.id,
                        "author": author,
                        "trusted": author in _TRUSTED_QUANTIZERS,
                        "downloads": getattr(m, "downloads", 0) or 0,
                        "likes": getattr(m, "likes", 0) or 0,
                    })
                ecosystem[cat_key] = {
                    "icon": cat_info["icon"],
                    "desc": cat_info["desc"],
                    "top_models": models,
                    "count": len(raw),
                }
            except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
                ecosystem[cat_key] = {
                    "icon": cat_info["icon"],
                    "desc": cat_info["desc"],
                    "top_models": [],
                    "count": 0,
                    "error": "search failed",
                }
        return ecosystem
    except ImportError:  # xray: ignore[QUAL-002]
        return {"error": "huggingface_hub not installed"}
    except Exception as exc:
        return {"error": str(exc)}


def _kill_zombies_on_port(port: int) -> int:
    """Kill any leftover processes listening on *port*.  Returns count killed."""
    killed = 0
    my_pid = os.getpid()
    try:
        import psutil
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
                if conn.pid and conn.pid != my_pid:
                    try:
                        proc = psutil.Process(conn.pid)
                        proc.terminate()
                        proc.wait(timeout=3)
                        killed += 1
                        print(f"[cleanup] Killed zombie PID {conn.pid} on port {port}", flush=True)  # xray: ignore[PY-004]
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                        pass
    except ImportError:  # xray: ignore[QUAL-002]
        # psutil not available — try netstat fallback on Windows
        if os.name == "nt":
            import subprocess
            try:
                out = subprocess.check_output(
                    ["netstat", "-ano"], text=True, timeout=5
                )
                for line in out.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        pid = int(parts[-1])
                        if pid != my_pid:
                            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                           capture_output=True, timeout=5)
                            killed += 1
                            print(f"[cleanup] Killed zombie PID {pid} on port {port}", flush=True)  # xray: ignore[PY-004]
            except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
                pass
    return killed


def run_server(port: int = 8123) -> None:
    """Start the HTTP server."""
    # Ensure emoji/unicode in log lines don't crash on Windows cp1252 consoles
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
        except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
            pass
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
        except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
            pass

    # Kill any zombie processes from previous runs
    zombies = _kill_zombies_on_port(port)
    if zombies:
        print(f"[cleanup] Killed {zombies} zombie process(es) on port {port}", flush=True)  # xray: ignore[PY-004]
        import time as _t; _t.sleep(0.5)  # let OS release the port

    # Auto-detect CPU and rebuild llama-cpp-python with native flags if needed
    ensure_native_llama_cpp()

    # Initialize zen_eval database
    zen_eval.init_db()
    print("[zen_eval] Database initialized")  # xray: ignore[PY-004]

    server = ThreadingHTTPServer(("127.0.0.1", port), ComparatorHandler)
    print(f"[OK] Comparator backend listening on http://127.0.0.1:{port}")  # xray: ignore[PY-004]
    print("   System info: /__system-info")  # xray: ignore[PY-004]
    print("   Comparison:  /__comparison/mixed")  # xray: ignore[PY-004]

    # Warm up the system-info cache in a background thread so the first
    # browser request returns instantly instead of waiting for GPU/CPU detection.
    def _warm_cache():
        try:
            get_system_info_cached(ComparatorHandler.model_dirs)
            print("[cache] system-info warm-up done")  # xray: ignore[PY-004]
        except Exception as exc:
            print(f"[cache] warm-up error: {exc}")  # xray: ignore[PY-004]

    threading.Thread(target=_warm_cache, daemon=True).start()

    # Graceful shutdown on Ctrl+C / SIGTERM
    import signal

    def _shutdown(signum, frame):
        print(f"\n[shutdown] Signal {signum} received, stopping server...", flush=True)  # xray: ignore[PY-004]
        server.shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        server.serve_forever()
    finally:
        server.server_close()
        print("[shutdown] Server closed cleanly", flush=True)  # xray: ignore[PY-004]


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    run_server(port)
