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
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse

# ── zen_core_libs imports (replaces ~300 lines of duplicated code) ────────────
from zen_core_libs.common import (
    count_tokens,
    get_cpu_count,
    get_cpu_info,
    get_memory_gb,
    get_system_info as _zcl_get_system_info,
    scan_gguf_models,
)
from zen_core_libs.common.system import (
    GPUInfo,
    get_gpu_info as _zcl_get_gpu_info,
    recommend_llama_build as _zcl_recommend_llama_build,
)
from zen_core_libs.llm import get_model_cache, ModelCache

# Enable Vulkan GPU backend for llama-cpp-python (AMD Radeon / any Vulkan GPU)
# Must be set before llama_cpp is imported. Has no effect if Vulkan is absent.
# Supports multi-GPU: set GGML_VK_VISIBLE_DEVICES=0,1 for two GPUs.
_vk_devices = os.environ.get('GGML_VK_VISIBLE_DEVICES', '0')
os.environ['GGML_VK_VISIBLE_DEVICES'] = _vk_devices


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a separate thread so inference doesn't block the UI."""

    daemon_threads = True


# ─ LRU Model Cache (backed by zen_core_libs.llm.ModelCache) ─────────────────
_MODEL_CACHE_SIZE = int(os.environ.get("LLM_MODEL_CACHE_SIZE", "8"))
_model_cache: ModelCache = get_model_cache(max_models=_MODEL_CACHE_SIZE)


def _get_or_load_model(path: str, n_ctx: int = 4096):
    """Return a cached Llama model or load a new one. Thread-safe LRU cache."""
    import llama_cpp

    cache_key = f"{path}::ctx{n_ctx}"

    def _loader():
        return llama_cpp.Llama(
            model_path=path,
            n_ctx=n_ctx,
            n_threads=max(1, (os.cpu_count() or 4) // 2),
            n_gpu_layers=-1,
            flash_attn=True,
            n_batch=512,
            use_mmap=True,
            use_mlock=False,
            verbose=False,
        )

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
except ImportError:
    HAS_PSUTIL = False
    psutil = None
    proc = None


# ── Compatibility wrappers for zen_core_libs ─────────────────────────────────
# zen_core_libs returns GPUInfo dataclass objects; the frontend + tests expect dicts.

_BACKEND_MAP = {"cuda": "CUDA", "rocm": "ROCm/Vulkan", "wmi": "DirectML"}


def get_gpu_info() -> list[dict]:
    """Detect GPUs via zen_core_libs, return as list of dicts for backward compat."""
    raw = _zcl_get_gpu_info()
    return [
        {
            "name": g.name,
            "vendor": g.vendor,
            "vram_gb": round(g.vram_gb, 1),
            "backend": _BACKEND_MAP.get(g.backend, g.backend),
        }
        for g in raw
    ]


def recommend_llama_build(cpu: dict | None = None, gpus: list | None = None) -> dict:
    """Recommend best llama.cpp build. Accepts dicts or GPUInfo objects."""
    # Convert dict gpus to GPUInfo for zen_core_libs
    _REVERSE_BACKEND = {"CUDA": "cuda", "ROCm/Vulkan": "rocm", "DirectML": "wmi"}
    gpu_objs: list[GPUInfo] | None = None
    if gpus is not None:
        gpu_objs = []
        for g in gpus:
            if isinstance(g, GPUInfo):
                gpu_objs.append(g)
            elif isinstance(g, dict):
                be = g.get("backend", "")
                gpu_objs.append(GPUInfo(
                    name=g.get("name", "Unknown"),
                    vendor=g.get("vendor", "Unknown"),
                    vram_gb=g.get("vram_gb", 0.0),
                    backend=_REVERSE_BACKEND.get(be, be.lower()),
                ))
    rec = _zcl_recommend_llama_build(cpu, gpu_objs)
    # Map flag: zen_core_libs uses pip args; tests expect short tag
    build = rec.get("build", "").lower()
    if "cuda" in build:
        rec["flag"] = "cuda"
    elif "rocm" in build or "vulkan" in build:
        rec["flag"] = "rocm"
    elif "avx-512" in build or "avx512" in build:
        rec["flag"] = "avx512"
    elif "avx2" in build:
        rec["flag"] = "avx2"
    else:
        rec["flag"] = "cpu"
    # Add "pip" key alias for backward compat (old tests check rec["pip"])
    if "pip_command" in rec:
        rec["pip"] = rec["pip_command"]
    return rec


def get_system_info(model_dirs: list[str] | None = None) -> dict:
    """Return comprehensive system info, augmented with llama.cpp status."""
    info = _zcl_get_system_info(model_dirs)
    info["timestamp"] = time.time()
    llama = get_llama_cpp_info()
    info["has_llama_cpp"] = llama["installed"]
    info["llama_cpp_version"] = llama["version"]
    return info



def get_llama_cpp_info() -> dict:
    """Return llama.cpp version and recommended build for this hardware."""
    installed = False
    version = None  # None (not a truthy string) so frontend can check falsiness
    try:
        import llama_cpp

        installed = True
        version = getattr(llama_cpp, "__version__", "installed") or "installed"
    except Exception:
        pass

    return {"installed": installed, "version": version}


# ─ Alias for backward compat — tests call cb.scan_models() ──────────────────
scan_models = scan_gguf_models


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
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = fence_match.group(1) if fence_match else raw

    # ── Step 2: try strict JSON parse ────────────────────────────────────────
    parsed = _try_json(json_str)

    # ── Step 3: find first { ... } in the raw text ──────────────────────────
    if parsed is None:
        brace_match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
        if brace_match:
            parsed = _try_json(brace_match.group(0))

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
    except Exception:
        pass
    return None


def _extract_scores_regex(text: str) -> dict:
    """Extract scores from natural-language judge output."""
    result: dict = {}
    lower = text.lower()

    # Match patterns: "overall score: 8", "overall: 8/10", "overall 8 out of 10"
    patterns = [
        (r"overall[\s:]+(\d+(?:\.\d+)?)\s*(?:/\s*10|out\s+of\s+10)?", "overall"),
        (r"accuracy[\s:]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", "accuracy"),
        (r"reasoning[\s:]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", "reasoning"),
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
    except Exception:
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
    except ValueError:
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
    global _sysinfo_cache
    with _sysinfo_lock:
        if _sysinfo_cache is not None:
            age = time.time() - _sysinfo_cache.get("_cache_ts", 0)
            if age < _SYSINFO_TTL:
                # Refresh model list inline (fast) while keeping cached hw data
                fresh_models = scan_gguf_models(model_dirs)
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
    model_dirs = [
        d for d in [
            os.environ.get("ZENAI_MODEL_DIR", ""),
            "C:\\AI\\Models",
            str(Path.home() / "AI" / "Models"),
            str(Path(__file__).resolve().parent / "models"),
        ] if d and Path(d).is_dir()
    ] or [str(Path.home() / "AI" / "Models")]

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
            except FileNotFoundError:
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
                except FileNotFoundError:
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
        except Exception:
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
        else:
            self._send_json(404, {"error": "Not found"})

    # ── Handlers ─────────────────────────────────────────────────────────────
    def _handle_system_info(self) -> None:
        try:
            info = get_system_info_cached(self.model_dirs)
            self._send_json(200, info)
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_install_status(self) -> None:
        """GET /__install-status?job=<id>"""
        from urllib.parse import parse_qs, urlparse

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
        from urllib.parse import parse_qs, urlparse

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
                print(f"[compare] WARN rejected {rejected} model path(s) outside model_dirs")

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
                    print("[SSE] Client disconnected, suppressing further events", flush=True)

            # Stream model inference one-by-one
            try:
                import llama_cpp
            except ImportError:
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

            # ── Phase 1: Pre-load all models into cache (sequential) ──────
            total_models = len(safe_models)

            print(f"\n[compare] Pre-loading {total_models} model(s)…", flush=True)
            for idx, path in enumerate(safe_models):
                model_name = os.path.basename(path).replace(".gguf", "")
                file_size_mb = round(os.path.getsize(path) / (1024 * 1024))
                _sse("model_loading", {
                    "model": model_name, "model_index": idx,
                    "total_models": total_models,
                    "size_mb": file_size_mb, "phase": "loading",
                })
                print(f"  Loading {model_name} ({file_size_mb} MB)…", flush=True)
                t_load = time.time()
                _get_or_load_model(path, n_ctx)
                load_ms = round((time.time() - t_load) * 1000)
                _sse("model_loaded", {
                    "model": model_name, "model_index": idx,
                    "total_models": total_models,
                    "load_time_ms": load_ms, "phase": "ready",
                })
                print(f"  {model_name} loaded in {load_ms/1000:.1f}s", flush=True)

            # ── Phase 2: Dispatch inference with stream=False in parallel ─
            # stream=False lets llama.cpp run the entire C++ compute while
            # the GIL is released, enabling TRUE parallel execution across
            # threads — exactly like the original Swarm run_arena pattern.
            print(f"[compare] Dispatching prompt to {total_models} model(s) in parallel…", flush=True)
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
                llm = _get_or_load_model(path, n_ctx)

                t0 = time.time()
                ram_before = 0
                try:
                    if HAS_PSUTIL:
                        ram_before = proc.memory_info().rss // (1024 * 1024) if proc else 0
                except Exception:
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
                    response_text = out["choices"][0]["message"]["content"] or ""
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
                    except Exception:
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
                    print(f"  [model-{model_idx}] {model_name} done — {completion_tokens} tok, {tps:.1f} t/s, eff={efficiency:.1f} t/s/GB, {elapsed_ms/1000:.1f}s", flush=True)
                    return result
                except Exception as exc:
                    elapsed_ms = (time.time() - t0) * 1000
                    result = {
                        "model": model_name, "model_path": path, "path": path,
                        "response": f"❌ Error: {exc}",
                        "error": str(exc),
                        "time_ms": round(elapsed_ms, 1),
                        "tokens": 0, "tokens_per_sec": 0,
                        "quality_score": 0, "ttft_ms": 0, "ram_delta_mb": 0,
                        "model_size_mb": model_size_mb, "efficiency": 0,
                        "response_chars": 0,
                    }
                    print(f"  [model-{model_idx}] {model_name} ERROR: {exc}", flush=True)
                    return result

            # Launch all models via ThreadPoolExecutor
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(total_models, 6)
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
                            print(f"  [model-{idx}] {model_name} THREAD ERROR: {exc}", flush=True)

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
                            print(f"  [model-{idx}] {model_name} TIMED OUT after {inference_timeout}s", flush=True)

            dispatch_wall = time.time() - _dispatch_t0
            print(f"[compare] All {total_models} models done in {dispatch_wall:.1f}s wall-clock", flush=True)

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
            except Exception:
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

        try:
            import llama_cpp
        except ImportError:
            return [
                {
                    "model": os.path.basename(p).replace(".gguf", ""),
                    "model_path": p,
                    "path": p,
                    "response": "⚠️ llama-cpp-python not installed. Click Install in the sidebar.",
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
            print(
                f"[compare] ▶ {model_name}  ctx={n_ctx}  max_tokens={max_tokens}  temp={temperature}"
            )
            t0 = time.time()
            ram_before = (
                (proc.memory_info().rss // (1024 * 1024))
                if (HAS_PSUTIL and proc is not None)
                else 0  # type: ignore[union-attr]
            )
            try:
                llm = _get_or_load_model(path, n_ctx)
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
                response_text = out["choices"][0]["message"]["content"] or ""
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

                print(
                    f"[compare] ✅ {model_name}  {elapsed_ms:.0f}ms  {completion_tokens}tok  {tps:.1f}t/s  eff={efficiency:.1f}t/s/GB  ram+{ram_delta}MB"
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
                print(f"[compare] ERROR {model_name}: {exc}")
                return {
                    "model": model_name,
                    "model_path": path,
                    "path": path,
                    "response": f"❌ Error loading/running model: {exc}",
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

        # Pre-load all models into cache first
        print(f"[compare] Pre-loading {len(model_paths)} model(s)…", flush=True)
        for path in model_paths:
            _get_or_load_model(path, n_ctx)

        # Run all models in parallel via ThreadPoolExecutor
        print(f"[compare] Dispatching to {len(model_paths)} model(s) in parallel…", flush=True)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(model_paths), 6)
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
                        print(f"[compare] THREAD ERROR {model_name}: {exc}", flush=True)
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
                        print(f"[compare] TIMEOUT {model_name} after {inference_timeout}s", flush=True)
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
            # Pick smallest model — the judge task is simple (scoring/comparing)
            # so we use the lightest model for speed.
            best = min(
                local_models,
                key=lambda p: os.path.getsize(p) if os.path.exists(p) else float("inf"),
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
        judge_path: str,
        judge_system_prompt: str,
        params: dict,
    ) -> list[dict]:
        """Score each response using the judge model; adds judge_score + judge_detail.

        Single-pass evaluation for speed on CPU-constrained systems.
        """
        try:
            import llama_cpp
        except ImportError:
            return responses

        import gc

        judge_name = os.path.basename(judge_path).replace(".gguf", "")
        print(f"[judge] Loading {judge_name}…")
        llm = None
        try:
            llm = _get_or_load_model(judge_path, min(params.get("n_ctx", 4096), 8192))

            for idx, r in enumerate(responses):
                if r.get("error"):
                    continue

                scores_collected: list[float] = []
                details_collected: list[dict] = []

                # Single-pass evaluation
                user_msg = (
                    f"Original question: {original_prompt}\n\n"
                    f"Model response:\n{r.get('response', '')}"
                )

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
                        out = llm.create_chat_completion(
                            messages=_build_messages(sys_prompt, user_msg, judge_path),
                            max_tokens=512,
                            temperature=0.1,
                            stream=False,
                        )
                        raw = out["choices"][0]["message"]["content"].strip()  # type: ignore[index]
                        jd = extract_judge_scores(raw)
                        score = float(jd.get("overall", 0))
                        scores_collected.append(score)
                        details_collected.append(jd)
                        break
                    except Exception as je:
                        print(
                            f"[judge] WARN attempt {attempt+1} "
                            f"failed for {r['model']}: {je}"
                        )

                if scores_collected:
                    score = scores_collected[0]
                    detail = details_collected[0].copy()
                    detail["overall"] = round(score, 1)
                    r["judge_score"] = round(score, 1)
                    r["quality_score"] = round(score, 1)
                    r["judge_detail"] = detail
                    print(f"[judge] OK {r['model']}  score={score:.1f}")
                else:
                    r["judge_score"] = 0
                    r["quality_score"] = 0
                    r["judge_detail"] = {
                        "overall": 0,
                        "error": "Judge failed after retries",
                    }
        finally:
            pass  # Keep judge model in cache for reuse
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
            import gc

            import llama_cpp

            llm = _get_or_load_model(model_path, 4096)
            full_messages = _build_messages(system, messages[0]["content"] if messages else "", model_path) if len(messages) <= 1 else [{"role": "system", "content": system}] + messages
            # For multi-turn chat, try with system role; fall back if it fails
            try:
                out = llm.create_chat_completion(
                    full_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                )
            except ValueError:
                # Model doesn't support system role — fold into first user msg
                if full_messages and full_messages[0].get("role") == "system":
                    sys_text = full_messages.pop(0)["content"]
                    if full_messages and full_messages[0].get("role") == "user":
                        full_messages[0]["content"] = sys_text + "\n\n" + full_messages[0]["content"]
                    else:
                        full_messages.insert(0, {"role": "user", "content": sys_text})
                out = llm.create_chat_completion(
                    full_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                )
            reply = out["choices"][0]["message"]["content"]  # type: ignore[index]
            self._send_json(200, {"response": reply})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_discover_models(self) -> None:
        """GET /__discover-models?q=&sort=trending&limit=30"""
        qs = parse_qs(urlparse(self.path).query)
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
        print(f"[{self.log_date_time_string()}] {msg}")


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
        print(f"[download] {job_id} DONE → {out_path}")
    except Exception as exc:
        _upd(state="error", progress=0, message=str(exc), error=str(exc))
        print(f"[download] {job_id} ERROR: {exc}")


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
            print(f"[install] {job_id} DONE")
        else:
            _upd(
                state="error",
                error=f"pip exited with code {process.returncode}",
                log=accumulated,
            )
            print(f"[install] {job_id} FAILED (code {process.returncode})")
    except Exception as exc:
        _upd(state="error", error=str(exc), log=f"ERROR: {exc}")
        print(f"[install] {job_id} EXCEPTION: {exc}")


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
            except Exception:
                ecosystem[cat_key] = {
                    "icon": cat_info["icon"],
                    "desc": cat_info["desc"],
                    "top_models": [],
                    "count": 0,
                    "error": "search failed",
                }
        return ecosystem
    except ImportError:
        return {"error": "huggingface_hub not installed"}
    except Exception as exc:
        return {"error": str(exc)}


def run_server(port: int = 8123) -> None:
    """Start the HTTP server."""
    # Ensure emoji/unicode in log lines don't crash on Windows cp1252 consoles
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
        except Exception:
            pass
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
        except Exception:
            pass
    server = ThreadingHTTPServer(("127.0.0.1", port), ComparatorHandler)
    print(f"[OK] Comparator backend listening on http://127.0.0.1:{port}")
    print("   System info: /__system-info")
    print("   Comparison:  /__comparison/mixed")

    # Warm up the system-info cache in a background thread so the first
    # browser request returns instantly instead of waiting for GPU/CPU detection.
    def _warm_cache():
        try:
            get_system_info_cached(ComparatorHandler.model_dirs)
            print("[cache] system-info warm-up done")
        except Exception as exc:
            print(f"[cache] warm-up error: {exc}")

    threading.Thread(target=_warm_cache, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    run_server(port)
