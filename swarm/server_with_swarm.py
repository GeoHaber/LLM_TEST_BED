"""
ZenAIos — Smart Dev Server
===========================
Serves the app, tracks every client, and persists everything to SQLite.

Usage:
    python server.py          → runs on port 8787
    python server.py 9000     → runs on a custom port

Admin dashboard:
    http://localhost:8787/__admin              live activity UI
    http://localhost:8787/__admin/data         JSON for the UI
    http://localhost:8787/__admin/db-stats     quick DB summary JSON

Database:  zenai_activity.db  (SQLite, created alongside server.py)

  Table: sessions
    ip TEXT, device TEXT, browser TEXT, os TEXT,
    first_seen REAL, last_seen REAL, total_hits INTEGER

  Table: hits
    id INTEGER PK, ip TEXT, path TEXT, status INTEGER,
    timestamp REAL, time_str TEXT, ua TEXT

Query examples (sqlite3 CLI or any LLM tool):
    SELECT ip, device, browser, total_hits FROM sessions ORDER BY total_hits DESC;
    SELECT path, COUNT(*) n FROM hits GROUP BY path ORDER BY n DESC LIMIT 10;
    SELECT * FROM hits WHERE ip='192.168.1.5' ORDER BY timestamp DESC LIMIT 20;
    SELECT date(timestamp,'unixepoch') d, COUNT(*) n FROM hits GROUP BY d;
"""

import asyncio
import http.server
import logging
import socketserver
import socket
import sqlite3
import json
import os
import sys
import time
import threading
import urllib.parse
import urllib.request
import heapq
import uuid as _uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum

logger = logging.getLogger("zenai.server")

# Force UTF-8 stdout/stderr so box-drawing and emoji chars work on Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# ─── Optional psutil for memory monitoring ────────────────────────────────────
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _HAS_PSUTIL = False


# ─── Adaptive FIFO Buffer (ported from Local_LLM) ────────────────────────────


class BackpressureTimeoutError(Exception):
    """Raised when a FIFO buffer put() times out under backpressure.

    Callers that need explicit failure handling can catch this instead of
    checking the boolean return from put().

    Example::

        try:
            _request_buffer.put(request, raise_on_timeout=True)
        except BackpressureTimeoutError:
            send_503(handler, "Server busy — try again shortly")
    """


class MessagePriority(IntEnum):
    """Priority levels for inference requests."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(order=True)
class _PrioritizedItem:
    priority: int
    timestamp: float
    item: object = field(compare=False)


class ThreadSafeFIFOBuffer:
    """Thread-safe adaptive FIFO buffer with backpressure and priority support.

    Ported from Local_LLM's AdaptiveFIFOBuffer — uses threading primitives
    instead of asyncio for compatibility with http.server handlers.

    Features:
      - Adaptive sizing (grows 1.5\u00d7 at >80% full, shrinks 0.8\u00d7 under pressure)
      - Priority queues (CRITICAL > HIGH > NORMAL > LOW)
      - Backpressure with configurable timeout
      - Memory monitoring via psutil (optional)
      - O(1) append/popleft via collections.deque
      - Built-in metrics
    """

    def __init__(
        self,
        min_size: int = 5,
        initial_size: int = 50,
        max_size: int = 500,
        enable_backpressure: bool = True,
        enable_memory_monitoring: bool = True,
        buffer_name: str = "buffer",
    ):
        self.min_size = min_size
        self.initial_size = initial_size
        self.max_size = max_size
        self.current_max_size = initial_size
        self.enable_backpressure = enable_backpressure
        self.enable_memory_monitoring = enable_memory_monitoring and _HAS_PSUTIL
        self.buffer_name = buffer_name

        self._queue: deque = deque()
        self._priority_queue: list[_PrioritizedItem] = []
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._not_full = threading.Condition(self._lock)

        self._metrics = {
            "total_added": 0,
            "total_retrieved": 0,
            "times_grew": 0,
            "times_shrunk": 0,
            "peak_size": 0,
            "backpressure_events": 0,
            "memory_adjustments": 0,
        }

    def put(self, item: object, priority: MessagePriority = MessagePriority.NORMAL,
            timeout: float = 30.0, raise_on_timeout: bool = False) -> bool:
        """Add item with backpressure. Returns False on timeout.

        Args:
            raise_on_timeout: If True, raises BackpressureTimeoutError instead
                of returning False when the buffer is full.
        """
        with self._not_full:
            if self.enable_backpressure:
                deadline = time.monotonic() + timeout
                while self._size_unlocked() >= self.current_max_size:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not self._not_full.wait(remaining):
                        self._metrics["backpressure_events"] += 1
                        logger.warning(
                            "[%s] Backpressure timeout (size=%d/%d)",
                            self.buffer_name, self._size_unlocked(), self.current_max_size,
                        )
                        if raise_on_timeout:
                            raise BackpressureTimeoutError(
                                f"[{self.buffer_name}] Buffer full "
                                f"({self._size_unlocked()}/{self.current_max_size}), "
                                f"waited {timeout:.1f}s"
                            )
                        return False

            self._adapt_size_unlocked()

            if priority != MessagePriority.NORMAL:
                entry = _PrioritizedItem(-priority.value, time.time(), item)
                heapq.heappush(self._priority_queue, entry)
            else:
                self._queue.append(item)

            self._metrics["total_added"] += 1
            sz = self._size_unlocked()
            if sz > self._metrics["peak_size"]:
                self._metrics["peak_size"] = sz
            self._not_empty.notify()
            return True

    def get(self, timeout: float = 5.0) -> object | None:
        """Get next item (priority first, then FIFO). Returns None on timeout."""
        with self._not_empty:
            deadline = time.monotonic() + timeout
            while self._size_unlocked() == 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._not_empty.wait(remaining):
                    return None

            if self._priority_queue:
                item = heapq.heappop(self._priority_queue).item
            else:
                item = self._queue.popleft()

            self._metrics["total_retrieved"] += 1
            self._adapt_size_unlocked()

            if self.enable_backpressure:
                self._not_full.notify()

            return item

    def put_nowait(self, item: object) -> bool:
        """Non-blocking put. Returns False if buffer is full."""
        with self._lock:
            if self.enable_backpressure and self._size_unlocked() >= self.current_max_size:
                self._metrics["backpressure_events"] += 1
                return False
            self._queue.append(item)
            self._metrics["total_added"] += 1
            sz = self._size_unlocked()
            if sz > self._metrics["peak_size"]:
                self._metrics["peak_size"] = sz
            self._not_empty.notify()
            return True

    def get_nowait(self) -> object | None:
        """Non-blocking get. Returns None if empty."""
        with self._lock:
            if self._size_unlocked() == 0:
                return None
            if self._priority_queue:
                item = heapq.heappop(self._priority_queue).item
            else:
                item = self._queue.popleft()
            self._metrics["total_retrieved"] += 1
            if self.enable_backpressure:
                self._not_full.notify()
            return item

    def drain(self) -> list:
        """Remove and return all items (priority first, then FIFO order)."""
        with self._lock:
            items = []
            while self._priority_queue:
                items.append(heapq.heappop(self._priority_queue).item)
            items.extend(self._queue)
            self._queue.clear()
            self._metrics["total_retrieved"] += len(items)
            if self.enable_backpressure:
                self._not_full.notify_all()
            return items

    def _size_unlocked(self) -> int:
        return len(self._queue) + len(self._priority_queue)

    def size(self) -> int:
        with self._lock:
            return self._size_unlocked()

    def is_empty(self) -> bool:
        return self.size() == 0

    def is_full(self) -> bool:
        with self._lock:
            return self._size_unlocked() >= self.current_max_size

    def _adapt_size_unlocked(self) -> None:
        """Adapt buffer size based on fill level and memory pressure."""
        sz = self._size_unlocked()
        if self.current_max_size == 0:
            return
        fill_pct = (sz / self.current_max_size) * 100

        # GROW at >80% full
        if fill_pct > 80 and self.current_max_size < self.max_size:
            old = self.current_max_size
            self.current_max_size = min(int(self.current_max_size * 1.5), self.max_size)
            self._metrics["times_grew"] += 1
            logger.info(
                "[%s] Grew %d → %d (%.1f%% full)",
                self.buffer_name, old, self.current_max_size, fill_pct,
            )

        # SHRINK at <20% full + memory pressure
        if self.enable_memory_monitoring and fill_pct < 20:
            mem_pct = psutil.virtual_memory().percent
            if mem_pct > 80 and self.current_max_size > self.min_size:
                old = self.current_max_size
                self.current_max_size = max(int(self.current_max_size * 0.8), self.min_size)
                self._metrics["times_shrunk"] += 1
                self._metrics["memory_adjustments"] += 1
                logger.info(
                    "[%s] Shrunk %d → %d (memory pressure %.1f%%)",
                    self.buffer_name, old, self.current_max_size, mem_pct,
                )
        elif not self.enable_memory_monitoring and fill_pct < 10:
            if self.current_max_size > self.initial_size:
                old = self.current_max_size
                self.current_max_size = max(int(self.current_max_size * 0.8), self.initial_size)
                self._metrics["times_shrunk"] += 1
                logger.info(
                    "[%s] Shrunk %d → %d (low fill %.1f%%)",
                    self.buffer_name, old, self.current_max_size, fill_pct,
                )

    def stats(self) -> dict:
        with self._lock:
            sz = self._size_unlocked()
            return {
                "buffer_name": self.buffer_name,
                "current_size": sz,
                "max_size": self.current_max_size,
                "fill_percent": round(sz / self.current_max_size * 100, 1) if self.current_max_size else 0,
                **self._metrics,
            }


class InferenceMetrics:
    """Thread-safe inference metrics tracker for observability."""

    def __init__(self):
        self._lock = threading.Lock()
        self._total_requests = 0
        self._total_errors = 0
        self._total_tokens = 0
        self._latencies: deque[float] = deque(maxlen=100)
        self._start_time = time.time()

    def record(self, latency: float, token_count: int = 0, error: bool = False):
        with self._lock:
            self._total_requests += 1
            self._latencies.append(latency)
            self._total_tokens += token_count
            if error:
                self._total_errors += 1

    def stats(self) -> dict:
        with self._lock:
            lats = list(self._latencies)
            uptime = time.time() - self._start_time
            avg_lat = sum(lats) / len(lats) if lats else 0
            p95 = sorted(lats)[int(len(lats) * 0.95)] if len(lats) >= 2 else avg_lat
            return {
                "total_requests": self._total_requests,
                "total_errors": self._total_errors,
                "total_tokens_generated": self._total_tokens,
                "avg_latency_s": round(avg_lat, 2),
                "p95_latency_s": round(p95, 2),
                "requests_per_minute": round(
                    self._total_requests / max(uptime / 60, 0.01), 1
                ),
                "error_rate_pct": round(
                    self._total_errors / max(self._total_requests, 1) * 100, 1
                ),
            }


# Global FIFO buffer and metrics instances
_request_buffer = ThreadSafeFIFOBuffer(
    min_size=2, initial_size=10, max_size=50,
    enable_backpressure=True, buffer_name="inference_requests",
)
_response_buffer = ThreadSafeFIFOBuffer(
    min_size=2, initial_size=20, max_size=100,
    enable_backpressure=False, buffer_name="inference_responses",
)
_inference_metrics = InferenceMetrics()

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
ROOT = os.path.dirname(os.path.abspath(__file__))

# Late-import swarm bridge (Local_LLM integration)
try:
    import swarm_bridge as _swarm  # type: ignore[import-untyped]

    _SWARM_OK = _swarm.available()
except ImportError:
    _swarm = None  # type: ignore[assignment]
    _SWARM_OK = False

# ─── Local LLM (optional — gracefully disabled if not found) ──────────────────
# Default: sibling folder ../Local_LLM, or override via LOCAL_LLM_PATH env var
_LLM_ROOT = os.environ.get(
    "LOCAL_LLM_PATH", os.path.normpath(os.path.join(ROOT, "..", "Local_LLM"))
)
_llm_engine = None
_llm_lock = threading.Lock()
_llm_error = None  # str when unavailable, None when ok
_llm_ready = False

# ─── llama-server.exe HTTP-based engine (auto-fallback) ──────────────────────
_LLAMA_SRV_EXE = os.environ.get("LLAMA_SERVER_EXE", r"C:\AI\bin\llama-server.exe")
_LLAMA_SRV_PORT = int(os.environ.get("LLAMA_SERVER_PORT", "8888"))
_llama_srv_proc = None  # subprocess.Popen, kept alive while server is running


def _stop_llama_server():
    global _llama_srv_proc
    if _llama_srv_proc and _llama_srv_proc.poll() is None:
        _llama_srv_proc.terminate()
        try:
            _llama_srv_proc.wait(timeout=10)
        except Exception:
            _llama_srv_proc.kill()
    _llama_srv_proc = None


def _start_llama_server(model_path: str):
    """Start llama-server.exe with *model_path* and block until /health responds."""
    import subprocess as _sp

    global _llama_srv_proc

    if not os.path.isfile(_LLAMA_SRV_EXE):
        raise FileNotFoundError(
            f"llama-server.exe not found at {_LLAMA_SRV_EXE!r}. "
            "Download from https://github.com/ggerganov/llama.cpp/releases "
            "or set LLAMA_SERVER_EXE env var."
        )

    _stop_llama_server()

    cmd = [
        _LLAMA_SRV_EXE,
        "-m",
        model_path,
        "--host",
        "127.0.0.1",
        "--port",
        str(_LLAMA_SRV_PORT),
        "-c",
        "4096",
        "-np",
        "1",
        "--no-webui",
    ]
    print(
        f"  [LlamaServer] Starting {os.path.basename(_LLAMA_SRV_EXE)} on :{_LLAMA_SRV_PORT}…"
    )
    _llama_srv_proc = _sp.Popen(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)

    deadline = time.time() + 120
    url = f"http://127.0.0.1:{_LLAMA_SRV_PORT}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                import json as _j

                data = _j.loads(resp.read())
                if data.get("status") == "ok":
                    print(f"  [LlamaServer] ✅ Ready — {os.path.basename(model_path)}")
                    return
                # still loading — status is e.g. "loading model"
        except Exception:
            if _llama_srv_proc.poll() is not None:
                raise RuntimeError(
                    "llama-server.exe exited unexpectedly during startup"
                )
        time.sleep(1)

    _stop_llama_server()
    raise TimeoutError("llama-server.exe did not become ready within 120s")


class _LlamaServerEngine:
    """Drop-in engine adapter backed by llama-server.exe (OpenAI-compatible HTTP)."""

    _init_error = None

    def __init__(self, model_path: str):
        self.model_path = model_path
        _start_llama_server(model_path)

    def switch_model(self, new_path: str) -> bool:
        try:
            _start_llama_server(new_path)
            self.model_path = new_path
            return True
        except Exception as exc:
            print(f"  [LlamaServer] switch_model failed: {exc}")
            return False

    async def query(
        self,
        message: str,
        system_prompt: str = "",
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 512,
        repeat_penalty: float = 1.1,
        stream: bool = True,
        messages: list[dict] | None = None,
        **_kwargs,
    ):
        import json as _json
        import urllib.request as _req

        # Use explicit multi-turn messages if provided, else build from params
        if messages:
            msgs = list(messages)
        else:
            msgs = []
            if system_prompt:
                msgs.append({"role": "system", "content": system_prompt})
            msgs.append({"role": "user", "content": message})

        body = _json.dumps(
            {
                "model": "local",
                "messages": msgs,
                "temperature": temperature,
                "top_p": top_p,
                # Give extra budget for Qwen3.5 thinking tokens (model uses ~100-500
                # tokens for internal reasoning before writing the actual response).
                "max_tokens": max(max_tokens, 1024),
                "repeat_penalty": repeat_penalty,
                "stream": True,
            }
        ).encode()

        req = _req.Request(
            f"http://127.0.0.1:{_LLAMA_SRV_PORT}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def _collect():
            content_parts: list[str] = []
            thinking_parts: list[str] = []
            with _req.urlopen(req, timeout=180) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        chunk = _json.loads(line[6:])
                    except (ValueError, _json.JSONDecodeError):
                        continue
                    # Validate chunk structure (ported from Local_LLM _validate_chunk)
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta")
                    if not isinstance(delta, dict):
                        continue
                    # 'content' = final response; 'reasoning_content' = thinking
                    tok = delta.get("content")
                    if isinstance(tok, str) and tok:
                        content_parts.append(tok)
                    else:
                        rtok = delta.get("reasoning_content")
                        if isinstance(rtok, str) and rtok:
                            thinking_parts.append(rtok)
            # Return final response; if empty fall back to thinking tokens
            return "".join(content_parts) or "".join(thinking_parts)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _collect)
        yield result


# ─── Model scan / download state ─────────────────────────────────────────────
_MODELS_DIRS = [
    r"C:\AI\Models",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "lm-studio", "models"),
    os.path.join(os.path.expanduser("~"), ".ollama", "models"),
    os.path.normpath(os.path.join(ROOT, "..", "Local_LLM", "models")),
]
_download_status: dict = {}  # key = filename → {state, progress, error}
_download_lock = threading.Lock()


def scan_gguf_models():
    """Return list of {name, path, size_gb} for every .gguf ≥ 50 MB found."""
    seen = set()
    models = []
    for d in _MODELS_DIRS:
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if not fname.lower().endswith(".gguf"):
                continue
            full = os.path.join(d, fname)
            try:
                sz = os.path.getsize(full)
            except OSError:
                continue
            if sz < 50 * 1024 * 1024:  # skip tiny/partial files
                continue
            key = fname.lower()
            if key in seen:
                continue
            seen.add(key)
            models.append(
                {"name": fname, "path": full, "size_gb": round(sz / (1024**3), 2)}
            )
    models.sort(key=lambda m: m["name"].lower())
    return models


def _do_switch_model(new_path: str) -> tuple[bool, str]:
    """Thread-safe model switch — called from a POST handler thread."""
    global _llm_engine, _llm_error, _llm_ready
    if not os.path.isfile(new_path):
        return False, f"File not found: {new_path}"
    with _llm_lock:
        try:
            if _LLM_ROOT not in sys.path:
                sys.path.insert(0, _LLM_ROOT)
            engine = _llm_engine
            if engine is None:
                # Engine not yet loaded — try Python binding first, then llama-server
                try:
                    from Core.services.inference_engine import FIFOLlamaCppInference  # type: ignore[import-not-found]

                    loop = _get_llm_loop()

                    async def _make(path):
                        return FIFOLlamaCppInference(model_path=path)

                    future = asyncio.run_coroutine_threadsafe(_make(new_path), loop)
                    new_engine = future.result(timeout=120)
                    if new_engine._init_error:
                        raise RuntimeError(new_engine._init_error)
                except Exception:
                    new_engine = _LlamaServerEngine(model_path=new_path)
                _llm_engine = new_engine
                _llm_ready = True
                _llm_error = None
            else:
                # Use the engine's own switch_model method
                loop = _get_llm_loop()
                future = asyncio.run_coroutine_threadsafe(
                    asyncio.get_event_loop().run_in_executor(
                        None, engine.switch_model, new_path
                    )
                    if False
                    else _async_switch(engine, new_path),
                    loop,
                )
                ok = future.result(timeout=120)
                if not ok:
                    raise RuntimeError("switch_model returned False")
                _llm_error = None
            name = os.path.basename(new_path)
            print(f"  [LLM] ✅ Switched to {name}")
            return True, name
        except Exception as exc:
            _llm_error = str(exc)
            print(f"  [LLM] ❌ Switch failed: {exc}")
            return False, str(exc)


async def _async_switch(engine, path):
    import asyncio as _aio

    loop = _aio.get_event_loop()
    return await loop.run_in_executor(None, engine.switch_model, path)


# Single persistent event loop running in a background thread.
# The LLM engine (and its asyncio.Semaphore) are always created and used
# inside this loop, so they are never "bound to a different event loop".
_llm_loop: asyncio.AbstractEventLoop | None = None


def _get_llm_loop() -> asyncio.AbstractEventLoop:
    """Return the shared background event loop, creating it on first call."""
    global _llm_loop
    if _llm_loop is not None and _llm_loop.is_running():
        return _llm_loop
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True, name="llm-loop")
    t.start()
    _llm_loop = loop
    return loop


def _find_default_model() -> str:
    """Return the first .gguf model path (≥ 50 MB) found in known dirs, or ''."""
    model = os.environ.get("DEFAULT_MODEL", "").strip()
    if model and os.path.isfile(model):
        return model
    if model:
        print(f"  [LLM] ⚠ DEFAULT_MODEL not found: {model} — auto-detecting")
    for mdir in _MODELS_DIRS:
        if not os.path.isdir(mdir):
            continue
        for fname in sorted(os.listdir(mdir)):
            if not fname.lower().endswith(".gguf"):
                continue
            full = os.path.join(mdir, fname)
            if os.path.getsize(full) > 50 * 1024 * 1024:
                return full
    return ""


# ─── Direct memory adapter for Local_LLM Swarm test ──────────────────────────


class ZenAIEngineAdapter:
    """Wraps ZenAIos engine to duck-type as llama_cpp.Llama for Swarm test.

    Exposes ``create_chat_completion()`` and ``tokenize()`` so that
    ``run_inference_sync(preloaded_llm=adapter)`` works with zero HTTP.
    """

    def __init__(self, engine, model_path: str = ""):
        self.engine = engine
        self.model_path = model_path or getattr(engine, "model_path", "")

    # ── llama_cpp.Llama interface expected by _stream_inference ────────

    def create_chat_completion(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.7,
        stream: bool = True,
        **kwargs,
    ):
        """Yield OpenAI-style chunk dicts, matching llama_cpp streaming output."""
        system_prompt = ""
        user_prompt = ""
        for msg in messages:
            role = msg.get("role", "")
            content = str(msg.get("content", ""))
            if role == "system":
                system_prompt = content
            elif role == "user":
                user_prompt = content

        loop = _get_llm_loop()
        q: deque[str] = deque()
        done = threading.Event()
        exc_box: list[Exception] = []

        async def _pump():
            try:
                # FIFOLlamaCppInference uses 'prompt=', _LlamaServerEngine uses 'message='
                import inspect as _insp

                sig = _insp.signature(self.engine.query)
                if "prompt" in sig.parameters:
                    kw: dict = {"prompt": user_prompt}
                else:
                    kw = {"message": user_prompt}
                kw["system_prompt"] = system_prompt
                kw["temperature"] = temperature
                kw["max_tokens"] = max_tokens
                kw["stream"] = True
                if len(messages) > 1:
                    kw["messages"] = messages
                async for chunk in self.engine.query(**kw):
                    q.append(chunk)
            except Exception as e:
                exc_box.append(e)
            finally:
                done.set()

        asyncio.run_coroutine_threadsafe(_pump(), loop)

        while not done.is_set() or q:
            while q:
                tok = q.popleft()
                yield {"choices": [{"delta": {"content": tok}, "finish_reason": None}]}
            if not done.is_set():
                time.sleep(0.01)

        if exc_box:
            raise exc_box[0]

        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    def tokenize(self, text_bytes: bytes) -> list[int]:
        """Approximate tokenization (4 chars/token). Enough for profiling."""
        return list(range(max(1, len(text_bytes) // 4)))


def get_engine_adapter():
    """Return a ZenAIEngineAdapter suitable for Local_LLM's preloaded_llm param.

    Usage from Local_LLM::

        sys.path.insert(0, '<path-to-ZenAIos-Dashboard>')
        from server import get_engine_adapter
        adapter = get_engine_adapter()
        result = run_inference_sync(model_path, prompt, ..., preloaded_llm=adapter)
    """
    engine, err = get_llm_engine()
    if err or engine is None:
        raise RuntimeError(f"ZenAIos engine unavailable: {err or 'not loaded'}")
    return ZenAIEngineAdapter(engine)


def _try_python_binding(model_path: str):
    """Try initialising the FIFOLlamaCppInference engine.  Returns engine or raises."""
    if not os.path.isdir(_LLM_ROOT):
        raise FileNotFoundError(
            f"Local_LLM not found at {_LLM_ROOT}. Set LOCAL_LLM_PATH env var."
        )
    if _LLM_ROOT not in sys.path:
        sys.path.insert(0, _LLM_ROOT)
    from Core.services.inference_engine import (  # type: ignore[import-not-found]
        FIFOLlamaCppInference,
        LLAMA_CPP_AVAILABLE,
    )

    if not LLAMA_CPP_AVAILABLE:
        raise ImportError(
            "llama-cpp-python not installed — run: pip install llama-cpp-python"
        )
    loop = _get_llm_loop()

    async def _make():
        return (
            FIFOLlamaCppInference(model_path=model_path)
            if model_path
            else FIFOLlamaCppInference()
        )

    engine = asyncio.run_coroutine_threadsafe(_make(), loop).result(timeout=60)
    if engine._init_error:
        raise RuntimeError(engine._init_error)
    return engine


def _try_server_fallback(model_path: str):
    """Try starting llama-server.exe as fallback.  Returns engine or raises."""
    if not model_path:
        raise FileNotFoundError("No .gguf model found in model dirs")
    return _LlamaServerEngine(model_path=model_path)


def get_llm_engine(blocking=True):
    """Lazy-init the LLM engine singleton (thread-safe, double-checked lock).

    When *blocking* is False (used by request handlers), returns immediately
    with a "still loading" error instead of waiting behind the warmup thread.
    """
    global _llm_engine, _llm_error, _llm_ready
    if _llm_ready or _llm_error is not None:
        return _llm_engine, _llm_error
    if not blocking:
        # Don't block request handlers while the warmup thread loads the model
        return None, "AI engine is still loading\u2026"
    with _llm_lock:
        if _llm_ready or _llm_error is not None:
            return _llm_engine, _llm_error
        model = _find_default_model()
        try:
            _llm_engine = _try_python_binding(model)
            _llm_ready = True
            mname = os.path.basename(str(_llm_engine.model_path))
            try:
                mgb = os.path.getsize(str(_llm_engine.model_path)) / (1024**3)
                print(f"  [LLM] ✅ Ready — {mname}  ({mgb:.1f} GB)")
            except OSError:
                print(f"  [LLM] ✅ Ready — {mname}")
        except Exception as exc:
            bind_err = str(exc)
            print(f"  [LLM] ❌ Python binding unavailable: {bind_err}")
            try:
                _llm_engine = _try_server_fallback(model or _find_default_model())
                _llm_ready = True
                print(
                    f"  [LLM] ✅ llama-server.exe backend active — {os.path.basename(model)}"
                )
            except Exception as srv_exc:
                _llm_error = f"Python binding: {bind_err}; llama-server: {srv_exc}"
                print(f"  [LLM] ❌ All backends failed: {_llm_error}")
    return _llm_engine, _llm_error


DB_PATH = os.path.join(ROOT, "zenai_activity.db")

# ─── SQLite — one connection per thread ────────────────────────────────────────
_local = threading.local()


def get_db():
    """Return a thread-local SQLite connection, creating it if needed."""
    if not getattr(_local, "conn", None):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")  # concurrent reads + writes
        _local.conn.execute("PRAGMA synchronous=NORMAL")  # safe + faster than FULL
    return _local.conn


def init_db():
    """Create tables if they don't exist yet."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            ip          TEXT PRIMARY KEY,
            device      TEXT,
            browser     TEXT,
            os          TEXT,
            first_seen  REAL,
            last_seen   REAL,
            total_hits  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS hits (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ip        TEXT,
            path      TEXT,
            status    INTEGER,
            timestamp REAL,
            time_str  TEXT,
            ua        TEXT
        );
        -- In-app actions (view switches, dept taps, alert acks, chat sends, etc.)
        CREATE TABLE IF NOT EXISTS actions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ip        TEXT,
            badge     TEXT,
            action    TEXT,   -- e.g. 'switchView', 'tapDept', 'ackAlert', 'chatSend', 'handover'
            detail    TEXT,   -- JSON blob with context
            timestamp REAL,
            time_str  TEXT
        );
        -- Alert acknowledgements
        CREATE TABLE IF NOT EXISTS alert_acks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key  TEXT,
            badge      TEXT,
            ip         TEXT,
            timestamp  REAL,
            time_str   TEXT
        );
        -- Anomaly log
        CREATE TABLE IF NOT EXISTS anomalies (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            type      TEXT,   -- 'beds_spike', 'triage_high', 'alerts_surge'
            message   TEXT,
            value     REAL,
            threshold REAL,
            timestamp REAL,
            time_str  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_hits_ip      ON hits(ip);
        CREATE INDEX IF NOT EXISTS idx_hits_ts      ON hits(timestamp);
        CREATE INDEX IF NOT EXISTS idx_hits_path    ON hits(path);
        CREATE INDEX IF NOT EXISTS idx_actions_ip   ON actions(ip);
        CREATE INDEX IF NOT EXISTS idx_actions_act  ON actions(action);
        CREATE INDEX IF NOT EXISTS idx_acks_key     ON alert_acks(alert_key);
    """)
    conn.commit()
    conn.close()


def db_upsert_session(ip, device, browser, os_name, first_seen, last_seen):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO sessions(ip, device, browser, os, first_seen, last_seen, total_hits)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(ip) DO UPDATE SET
            device     = excluded.device,
            browser    = excluded.browser,
            os         = excluded.os,
            last_seen  = excluded.last_seen,
            total_hits = total_hits + 1
    """,
        (ip, device, browser, os_name, first_seen, last_seen),
    )
    conn.commit()


def db_insert_hit(ip, path, status, ts, time_str, ua):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO hits(ip, path, status, timestamp, time_str, ua)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (ip, path, status, ts, time_str, ua),
    )
    conn.commit()


def db_insert_action(ip, badge, action, detail, ts):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO actions(ip, badge, action, detail, timestamp, time_str)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (
            ip,
            badge,
            action,
            json.dumps(detail) if isinstance(detail, dict) else str(detail),
            ts,
            datetime.fromtimestamp(ts).strftime("%H:%M:%S"),
        ),
    )
    conn.commit()


def db_insert_ack(alert_key, badge, ip, ts):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO alert_acks(alert_key, badge, ip, timestamp, time_str)
        VALUES (?, ?, ?, ?, ?)
    """,
        (alert_key, badge, ip, ts, datetime.fromtimestamp(ts).strftime("%H:%M:%S")),
    )
    conn.commit()


def db_insert_anomaly(atype, message, value, threshold, ts):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO anomalies(type, message, value, threshold, timestamp, time_str)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (
            atype,
            message,
            value,
            threshold,
            ts,
            datetime.fromtimestamp(ts).strftime("%H:%M:%S"),
        ),
    )
    conn.commit()


# ─── Activity store (in-memory, fast — DB is the persistent copy) ──────────────
# sessions[ip] = { ip, device, browser, os, first_seen, last_seen, hits: [...] }
sessions = {}


def now_str():
    return datetime.now().strftime("%H:%M:%S")


def now_ts():
    return time.time()


UA_HINTS = [
    # (fragment, device label, browser label, os label)
    ("iPhone", "iPhone", None, "iOS"),
    ("iPad", "iPad", None, "iPadOS"),
    ("Android", "Android", None, "Android"),
    ("Windows NT", "PC", None, "Windows"),
    ("Macintosh", "Mac", None, "macOS"),
    ("Linux", "Linux", None, "Linux"),
]
BROWSER_HINTS = [
    ("EdgA", "Edge Mobile"),
    ("EdgW", "Edge Mobile"),
    ("Edg/", "Edge"),
    ("Chrome", "Chrome"),
    ("Firefox", "Firefox"),
    ("Safari", "Safari"),
    ("curl", "curl"),
    ("python", "Python"),
]


def parse_ua(ua):
    device, os_name, browser = "Unknown", "Unknown", "Unknown"
    for frag, dev, _, os_ in UA_HINTS:
        if frag in ua:
            device = dev
            os_name = os_
            break
    for frag, br in BROWSER_HINTS:
        if frag in ua:
            browser = br
            break
    return device, browser, os_name


def record_hit(ip, path, status, ua):
    device, browser, os_name = parse_ua(ua)
    ts = now_ts()
    if ip not in sessions:
        sessions[ip] = {
            "ip": ip,
            "device": device,
            "browser": browser,
            "os": os_name,
            "first_seen": ts,
            "last_seen": ts,
            "hits": [],
        }
    else:
        # Update device/browser if we got a richer UA this time
        if device != "Unknown":
            sessions[ip]["device"] = device
            sessions[ip]["browser"] = browser
            sessions[ip]["os"] = os_name
        sessions[ip]["last_seen"] = ts

    hit = {"time": now_str(), "path": path, "status": status}
    sessions[ip]["hits"].append(hit)
    # Keep last 200 hits per client in memory
    sessions[ip]["hits"] = sessions[ip]["hits"][-200:]

    # Persist to SQLite
    s = sessions[ip]
    db_upsert_session(ip, s["device"], s["browser"], s["os"], s["first_seen"], ts)
    db_insert_hit(ip, path, status, ts, hit["time"], ua)


# ─── Admin HTML page ───────────────────────────────────────────────────────────
ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZenAIos — Activity Monitor</title>
<style>
  :root { --bg:#0b0e14; --card:#151b27; --border:#1e2a3a; --blue:#3B5BDB;
          --green:#2b8a3e; --red:#e03131; --orange:#e67700;
          --text:#e2e8f0; --muted:#8b9bb4; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:system-ui,sans-serif;
         padding:20px; min-height:100vh; }
  h1 { color:var(--blue); font-size:1.4rem; margin-bottom:4px; }
  .sub { color:var(--muted); font-size:.8rem; margin-bottom:20px; }
  .grid { display:grid; gap:14px; }
  .card { background:var(--card); border:1px solid var(--border);
          border-radius:12px; padding:16px; }
  .card-head { display:flex; align-items:center; gap:10px; margin-bottom:12px; }
  .dot { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
  .dot.active  { background:var(--green); box-shadow:0 0 6px var(--green); animation:pulse 2s infinite; }
  .dot.idle    { background:var(--orange); }
  .dot.offline { background:var(--muted); }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .name { font-weight:600; font-size:.95rem; }
  .badge { background:var(--border); border-radius:6px; padding:2px 8px;
           font-size:.72rem; color:var(--muted); }
  .meta { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
  .meta span { font-size:.75rem; color:var(--muted); }
  .meta span b { color:var(--text); }
  .hits { max-height:180px; overflow-y:auto; }
  .hit { display:flex; gap:8px; align-items:baseline; padding:4px 0;
         border-bottom:1px solid var(--border); font-size:.78rem; }
  .hit:last-child { border-bottom:none; }
  .hit .t  { color:var(--muted); min-width:58px; flex-shrink:0; }
  .hit .s200 { color:var(--green); min-width:30px; }
  .hit .s304 { color:var(--blue);  min-width:30px; }
  .hit .serr { color:var(--red);   min-width:30px; }
  .hit .p  { color:var(--text); word-break:break-all; }
  .empty { color:var(--muted); font-size:.82rem; text-align:center; padding:30px; }
  .stats-row { display:flex; gap:24px; margin-bottom:16px; flex-wrap:wrap; }
  .stat { text-align:center; }
  .stat .n { font-size:1.6rem; font-weight:700; color:var(--blue); }
  .stat .l { font-size:.72rem; color:var(--muted); margin-top:2px; }
  .refresh { color:var(--muted); font-size:.72rem; margin-top:16px; text-align:right; }
  header { display:flex; align-items:center; justify-content:space-between;
           margin-bottom:20px; flex-wrap:wrap; gap:10px; }
  .server-url { background:var(--card); border:1px solid var(--border);
                border-radius:8px; padding:6px 12px; font-family:monospace;
                font-size:.82rem; color:var(--blue); }
  .section-title { font-size:.8rem; font-weight:700; letter-spacing:.08em;
                   color:var(--muted); text-transform:uppercase; margin:20px 0 8px; }
  .ev { display:flex; gap:10px; align-items:baseline; padding:5px 0;
        border-bottom:1px solid var(--border); font-size:.78rem; }
  .ev:last-child { border-bottom:none; }
  .ev .t  { color:var(--muted); min-width:58px; flex-shrink:0; }
  .ev .ip { color:var(--muted); min-width:92px; flex-shrink:0; font-family:monospace; font-size:.72rem; }
  .ev .badge-id { color:#ffd43b; min-width:90px; flex-shrink:0; font-weight:600; }
  .ev .act-ok   { color:var(--green); min-width:170px; flex-shrink:0; }
  .ev .act-fail { color:var(--red);   min-width:170px; flex-shrink:0; }
  .ev .act-warn { color:var(--orange);min-width:170px; flex-shrink:0; }
  .ev .act-info { color:var(--blue);  min-width:170px; flex-shrink:0; }
  .ev .det { color:var(--muted); word-break:break-all; }
  /* Model manager */
  .model-search { width:100%; background:var(--bg); border:1px solid var(--border);
                  border-radius:8px; padding:8px 12px; color:var(--text);
                  font-size:.85rem; margin-bottom:14px; outline:none; }
  .model-search:focus { border-color:var(--blue); }
  .mc-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:12px; }
  .mc { background:linear-gradient(135deg,#191d2d 0%,#141820 100%);
        border:1px solid var(--border); border-radius:12px; padding:14px 16px;
        transition:border-color .2s; }
  .mc:hover { border-color:#3a4a6a; }
  .mc.mc-active { border-color:var(--green); box-shadow:0 0 0 1px #69db7c22; }
  .mc-top  { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
  .mc-badge { font-size:.62rem; font-weight:700; letter-spacing:.07em; padding:2px 7px;
              border-radius:4px; text-transform:uppercase; white-space:nowrap; flex-shrink:0; }
  .mc-badge.fast        { background:#0d2a0d; color:#69db7c; }
  .mc-badge.balanced    { background:#2a2500; color:#ffd43b; }
  .mc-badge.large       { background:#1e0a2e; color:#cc5de8; }
  .mc-badge.specialized { background:#0a1e2e; color:#74c0fc; }
  .mc-size { font-size:.72rem; color:var(--muted); white-space:nowrap; }
  .mc-name { flex:1; font-size:.84rem; font-weight:600; color:var(--text);
             word-break:break-all; line-height:1.3; margin-bottom:4px; }
  .mc-name.active-model { color:#69db7c; }
  .mc-desc { font-size:.76rem; color:var(--muted); margin-bottom:8px;
             line-height:1.4; min-height:1.1em; }
  .mc-stats{ display:flex; gap:14px; flex-wrap:wrap; margin-bottom:8px;
             font-size:.71rem; color:#7a8faa; }
  .mc-caps { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:10px; }
  .mc-cap  { font-size:.67rem; background:#0f1e30; color:#74c0fc;
             padding:2px 8px; border-radius:10px; border:1px solid #1a3a5a; }
  .mc-cap.cap-coding     { color:#69db7c; background:#0d2010; border-color:#1a4020; }
  .mc-cap.cap-reasoning  { color:#ffd43b; background:#251f00; border-color:#3a3000; }
  .mc-cap.cap-math       { color:#ff922b; background:#2a1500; border-color:#3a2500; }
  .mc-cap.cap-multilingual{color:#cc5de8; background:#1a0a2e; border-color:#2a1040; }
  .btn-load { background:var(--blue); color:#fff; border:none; border-radius:6px;
              padding:5px 14px; font-size:.75rem; cursor:pointer; white-space:nowrap; flex-shrink:0; }
  .btn-load:hover { opacity:.85; }
  .btn-load:disabled { opacity:.4; cursor:not-allowed; }
  .btn-dl   { background:#1a2a1a; color:var(--green); border:1px solid var(--green);
              border-radius:6px; padding:5px 14px; font-size:.75rem; cursor:pointer; white-space:nowrap; }
  .btn-dl:hover { background:#2b3b2b; }
  .dl-bar   { height:3px; border-radius:2px; background:var(--border); margin-top:6px; width:100%; }
  .dl-fill  { height:100%; border-radius:2px; background:var(--green); transition:width .4s; }
  .dl-label { font-size:.7rem; color:var(--muted); margin-top:2px; }
  .model-status { font-size:.72rem; padding:2px 7px; border-radius:4px; }
  .model-status.running { background:#1a2a1a; color:var(--green); }
  .model-status.done    { background:#1a2a3a; color:var(--blue); }
  .model-status.error   { background:#2a1a1a; color:var(--red); }
  .quick-dl { margin-top:16px; padding-top:14px; border-top:1px solid var(--border); }
  .quick-dl-title { font-size:.75rem; color:var(--muted); margin-bottom:8px; }
  .quick-dl-grid  { display:flex; gap:8px; flex-wrap:wrap; }
  .chip { background:var(--border); border:none; border-radius:14px; padding:5px 13px;
          color:var(--text); font-size:.75rem; cursor:pointer; }
  .chip:hover { background:#2a3a4a; }
</style>
</head>
<body>
<header>
  <div>
    <h1>🏥 ZenAIos — Activity Monitor</h1>
    <div class="sub" id="serverInfo">Loading...</div>
  </div>
  <div class="server-url" id="serverUrl">Loading...</div>
</header>

<div class="stats-row" id="statsRow"></div>

<div class="section-title">🤖 AI Model Manager</div>
<div class="card" id="modelPanel">
  <div style="color:var(--muted);font-size:.82rem">Loading models...</div>
</div>

<div class="section-title">🔐 Login Events</div>
<div class="card" id="loginEvents"><div class="empty">No login attempts yet</div></div>
<div class="section-title">🌐 Connected Clients</div>
<div class="grid" id="clientGrid"><div class="empty">Waiting for connections...</div></div>
<div class="refresh" id="refreshNote"></div>

<script>
const LOCAL_IP = '__LOCAL_IP__';
const PORT     = __PORT__;

document.getElementById('serverUrl').textContent = 'http://' + LOCAL_IP + ':' + PORT;
document.getElementById('serverInfo').textContent = 'Admin panel — auto-refreshes every 3 s';

// ── Model catalog — rich metadata keyed by filename keyword ──────────────────
const MODEL_CATALOG = {
  'qwen3.5-9b':     { desc:'Qwen3.5 flagship · top reasoning, coding & multilingual', ctx:'32K',  speed:'~8 tok/s',  ram:'8 GB',  caps:['Chat','Reasoning','Coding','Multilingual','Math'], category:'balanced' },
  'qwen3.5-4b':     { desc:'Qwen3.5 compact · fast everyday assistant with great accuracy', ctx:'32K',  speed:'~14 tok/s', ram:'5 GB',  caps:['Chat','Reasoning','Multilingual'],               category:'fast'     },
  'qwen3.5-1.7b':   { desc:'Qwen3.5 nano · ultra fast, ideal for edge & mobile',      ctx:'32K',  speed:'~28 tok/s', ram:'3 GB',  caps:['Chat'],                                          category:'fast'     },
  'qwen3-8b':       { desc:'Qwen3 8B · strong instruction-following and reasoning',   ctx:'32K',  speed:'~9 tok/s',  ram:'8 GB',  caps:['Chat','Reasoning','Coding'],                     category:'balanced' },
  'qwen3-4b':       { desc:'Qwen3 4B · lightweight and capable for most tasks',       ctx:'32K',  speed:'~16 tok/s', ram:'5 GB',  caps:['Chat','Reasoning'],                              category:'fast'     },
  'qwen3-1.7b':     { desc:'Qwen3 1.7B · tiny yet surprisingly capable',              ctx:'32K',  speed:'~28 tok/s', ram:'3 GB',  caps:['Chat'],                                          category:'fast'     },
  'qwen2.5-14b':    { desc:'Qwen2.5 14B · flagship quality, multilingual, top math',  ctx:'128K', speed:'~6 tok/s',  ram:'16 GB', caps:['Chat','Coding','Reasoning','Multilingual','Math'], category:'large'    },
  'qwen2.5-coder':  { desc:'Qwen2.5 Coder · #1 open coding model, beats GPT-4 on HumanEval', ctx:'32K', speed:'~11 tok/s', ram:'6 GB', caps:['Chat','Coding','Math'],                  category:'balanced' },
  'qwen2.5':        { desc:'Qwen2.5 · efficient multilingual model',                  ctx:'32K',  speed:'~10 tok/s', ram:'6 GB',  caps:['Chat','Coding','Multilingual'],                  category:'balanced' },
  'deepseek-r1':    { desc:'DeepSeek R1 · chain-of-thought reasoning specialist',     ctx:'64K',  speed:'~5 tok/s',  ram:'16 GB', caps:['Reasoning','Math','Coding'],                     category:'large'    },
  'deepseek-coder': { desc:'DeepSeek Coder · specialist for code generation & debug', ctx:'16K',  speed:'~12 tok/s', ram:'8 GB',  caps:['Coding','Math'],                                 category:'balanced' },
  'llama-3.2-3b':   { desc:'Llama 3.2 3B · lightning fast, 128K context window',      ctx:'128K', speed:'~22 tok/s', ram:'4 GB',  caps:['Chat','Reasoning'],                              category:'fast'     },
  'llama-3.1-8b':   { desc:'Llama 3.1 8B · versatile, long context, multilingual',    ctx:'128K', speed:'~10 tok/s', ram:'8 GB',  caps:['Chat','Reasoning','Multilingual'],               category:'balanced' },
  'llama':          { desc:'Meta Llama · solid all-purpose assistant',                 ctx:'8K',   speed:'~10 tok/s', ram:'8 GB',  caps:['Chat','Reasoning'],                              category:'balanced' },
  'mistral-7b':     { desc:'Mistral 7B · excellent reasoning & analysis, great all-rounder', ctx:'8K', speed:'~10 tok/s', ram:'8 GB', caps:['Chat','Coding','Reasoning','Math'],         category:'balanced' },
  'devstral':       { desc:'Devstral · Mistral-based coding specialist for devs',      ctx:'32K',  speed:'~4 tok/s',  ram:'18 GB', caps:['Coding','Chat'],                                 category:'large'    },
  'mistral':        { desc:'Mistral · jack of all trades, solid at everything',        ctx:'32K',  speed:'~10 tok/s', ram:'8 GB',  caps:['Chat','Coding','Reasoning'],                     category:'balanced' },
  'phi-3':          { desc:"Microsoft Phi-3 · tiny powerhouse, great for STEM & math",ctx:'4K',   speed:'~25 tok/s', ram:'4 GB',  caps:['Chat','Coding','Math'],                          category:'fast'     },
  'phi':            { desc:'Microsoft Phi · efficient, excellent for quick tasks',     ctx:'4K',   speed:'~25 tok/s', ram:'4 GB',  caps:['Chat','Coding'],                                 category:'fast'     },
  'gemma-2-9b':     { desc:'Google Gemma 2 9B · powerful reasoning, excellent safety', ctx:'8K',   speed:'~8 tok/s',  ram:'10 GB', caps:['Chat','Reasoning'],                              category:'balanced' },
  'gemma':          { desc:'Google Gemma · instruction-following, safe and reliable',  ctx:'8K',   speed:'~12 tok/s', ram:'8 GB',  caps:['Chat','Reasoning'],                              category:'balanced' },
  'glm-4':          { desc:'GLM-4 · Chinese/English bilingual, strong chat & code',   ctx:'128K', speed:'~5 tok/s',  ram:'18 GB', caps:['Chat','Coding','Multilingual'],                  category:'large'    },
};
const CAP_CLASS = { Coding:'cap-coding', Reasoning:'cap-reasoning', Math:'cap-math', Multilingual:'cap-multilingual' };
const CAT_LABEL = { fast:'⚡ Fast', balanced:'⚖ Balanced', large:'🔬 Large', specialized:'🎯 Special' };

function getModelInfo(name) {
  const n = name.toLowerCase();
  const keys = Object.keys(MODEL_CATALOG).sort((a,b) => b.length - a.length);
  for (const k of keys) { if (n.includes(k)) return MODEL_CATALOG[k]; }
  return { desc:'', ctx:'—', speed:'—', ram:'—', caps:['Chat'], category:'balanced' };
}

// ── Quick-download presets ────────────────────────────────────────────────────
const QUICK_MODELS = [
  { label:'Qwen3.5-4B Q4 (2.7 GB)',  repo:'unsloth/Qwen3.5-4B-GGUF',        file:'Qwen3.5-4B-Q4_K_M.gguf' },
  { label:'Qwen3.5-9B Q4 (5.3 GB)',  repo:'unsloth/Qwen3.5-9B-GGUF',        file:'Qwen3.5-9B-Q4_K_M.gguf' },
  { label:'Qwen3-1.7B Q4 (1.0 GB)',  repo:'unsloth/Qwen3-1.7B-GGUF',        file:'Qwen3-1.7B-Q4_K_M.gguf' },
  { label:'Qwen3-4B Q4 (2.3 GB)',    repo:'Qwen/Qwen3-4B-GGUF',              file:'Qwen3-4B-Q4_K_M.gguf' },
  { label:'Qwen3-8B Q4 (4.7 GB)',    repo:'Qwen/Qwen3-8B-GGUF',              file:'Qwen3-8B-Q4_K_M.gguf' },
  { label:'Qwen2.5-14B Q4 (8.6 GB)', repo:'Qwen/Qwen2.5-14B-Instruct-GGUF', file:'Qwen2.5-14B-Instruct-Q4_K_M.gguf' },
];

let _modelFilter = '';
let _lastModels = [];
let _switching = false;

async function downloadModel(repo, filename) {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '⏳ Starting…';
  try {
    const r = await fetch('/__admin/download-model', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ repo_id: repo, filename, dest_dir: 'C:\\\\AI\\\\Models' })
    });
    const d = await r.json();
    if (d.ok) btn.textContent = '⬇ Downloading…';
    else { btn.textContent = '❌ Failed'; btn.disabled = false; }
  } catch { btn.textContent = '❌ Error'; btn.disabled = false; }
}

async function loadModel(path) {
  if (_switching) return;
  _switching = true;
  const allBtns = document.querySelectorAll('.btn-load');
  allBtns.forEach(b => { b.disabled = true; });
  const statusEl = document.getElementById('switchStatus');
  if (statusEl) statusEl.textContent = '⏳ Switching model (may take ~30s)…';
  try {
    const r = await fetch('/__admin/set-model', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ path })
    });
    const d = await r.json();
    if (statusEl) statusEl.textContent = d.ok ? '✅ Loaded: ' + d.model : '❌ ' + d.error;
    await loadModels();
  } catch(e) {
    if (statusEl) statusEl.textContent = '❌ Network error: ' + e.message;
  }
  _switching = false;
  allBtns.forEach(b => { b.disabled = false; });
}

async function loadModels() {
  let data;
  try { data = await (await fetch('/__admin/models')).json(); }
  catch { return; }
  _lastModels = data.models || [];
  renderModelPanel(data);
}

function renderModelPanel(data) {
  const panel = document.getElementById('modelPanel');
  const models = (data.models || []).filter(m =>
    !_modelFilter || m.name.toLowerCase().includes(_modelFilter.toLowerCase())
  );
  const current = data.current || '';
  const downloads = data.downloads || {};

  const cards = models.map(m => {
    const isCurrent = current && current.toLowerCase().endsWith(m.name.toLowerCase());
    const info     = getModelInfo(m.name);
    const catCls   = info.category || 'balanced';
    const catLabel = CAT_LABEL[catCls] || catCls;
    const capHtml  = (info.caps||[]).map(c =>
      `<span class="mc-cap ${CAP_CLASS[c]||''}">${c}</span>`).join('');
    const descHtml  = info.desc ? `<div class="mc-desc">${info.desc}</div>` : '';
    const statsHtml = (info.ctx && info.ctx !== '—') ?
      `<div class="mc-stats"><span>📏 ${info.ctx} ctx</span><span>⚡ ${info.speed}</span><span>💾 ${info.ram} RAM</span></div>` : '';
    const safePath  = m.path.replace(/\\/g,'\\\\');
    return `<div class="mc${isCurrent?' mc-active':''}">
      <div class="mc-top">
        <span class="mc-badge ${catCls}">${catLabel}</span>
        <span class="mc-size">${m.size_gb} GB</span>
        <button class="btn-load" style="margin-left:auto" ${isCurrent?'disabled':''} onclick="loadModel('${safePath}')">${isCurrent?'✅ Active':'Load'}</button>
      </div>
      <div class="mc-name${isCurrent?' active-model':''}">${m.name}</div>
      ${descHtml}${statsHtml}
      <div class="mc-caps">${capHtml}</div>
    </div>`;
  }).join('');

  const dlRows = Object.entries(downloads).map(([fname, st]) => {
    const cls = st.state === 'done' ? 'done' : st.state === 'error' ? 'error' : 'running';
    const label = st.state === 'done' ? '✅ Ready' : st.state === 'error' ? '❌ ' + st.error : '⬇ Downloading…';
    return `<div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--border);font-size:.8rem;">
      <span style="flex:1;color:var(--text)">${fname}</span>
      <span class="model-status ${cls}">${label}</span>
    </div>`;
  }).join('');

  const quickChips = QUICK_MODELS.map(m => {
    const alreadyHave = _lastModels.some(x => x.name.toLowerCase() === m.file.toLowerCase());
    const inDl = downloads[m.file] && downloads[m.file].state !== 'error';
    if (alreadyHave) return '';
    return `<button class="chip" ${inDl?'disabled':''} onclick="downloadModel('${m.repo}','${m.file}')">${inDl?'⬇ '+m.label:'⬇ '+m.label}</button>`;
  }).filter(Boolean).join('');

  panel.innerHTML = `
    <div style="font-size:.8rem;color:var(--muted);margin-bottom:10px">
      Active model: <b style="color:#69db7c">${current ? current.split(/[/\\\\]/).pop() : 'none'}</b>
      &nbsp;<span id="switchStatus"></span>
    </div>
    <input class="model-search" placeholder="🔍 Filter models…" value="${_modelFilter}"
      oninput="_modelFilter=this.value; renderModelPanel({models:_lastModels,current:'${current}'.replace(/\\\\/g,'\\\\\\\\'),downloads:${JSON.stringify(downloads).replace(/\\/g,'\\\\')}})" />
    <div class="mc-grid">
      ${cards || '<div class="empty">No .gguf models found in C:\\\\AI\\\\Models</div>'}
    </div>
    ${dlRows ? '<div style="margin-top:12px;font-size:.75rem;color:var(--muted);margin-bottom:6px">Downloads in progress:</div>' + dlRows : ''}
    ${quickChips ? '<div class="quick-dl"><div class="quick-dl-title">⬇ Quick download from HuggingFace (saves to C:\\\\AI\\\\Models):</div><div class="quick-dl-grid">' + quickChips + '</div></div>' : ''}
  `;
}

async function load() {
  let data;
  try { data = await (await fetch('/__admin/data')).json(); }
  catch { return; }

  // Summary stats
  const active  = data.sessions.filter(s => s.active).length;
  const total   = data.sessions.length;
  const allHits = data.sessions.reduce((n, s) => n + s.total_hits, 0);
  document.getElementById('statsRow').innerHTML = `
    <div class="stat"><div class="n">${total}</div><div class="l">Clients</div></div>
    <div class="stat"><div class="n" style="color:var(--green)">${active}</div><div class="l">Active now</div></div>
    <div class="stat"><div class="n">${allHits}</div><div class="l">Total requests</div></div>
    <div class="stat"><div class="n">${data.uptime}</div><div class="l">Uptime</div></div>`;

  // Model panel (uses data from main poll so it refreshes every 3s)
  if (!_switching) {
    _lastModels = data.models || [];
    renderModelPanel({ models: data.models, current: data.current_model_path, downloads: data.downloads });
  }

  if (!data.sessions.length) {
    document.getElementById('clientGrid').innerHTML =
      '<div class="empty">No clients yet — open the app on a device.</div>';
    document.getElementById('refreshNote').textContent =
      'Last refreshed ' + new Date().toLocaleTimeString();
    return;
  }

  // Sort: active first, then by last_seen desc
  data.sessions.sort((a, b) => {
    if (a.active !== b.active) return b.active - a.active;
    return b.last_seen_ts - a.last_seen_ts;
  });

  document.getElementById('clientGrid').innerHTML = data.sessions.map(s => {
    const dot = s.active ? 'active' : (s.idle ? 'idle' : 'offline');
    const statusClass = code => code < 300 ? 's200' : (code < 400 ? 's304' : 'serr');
    const hitsHtml = s.recent_hits.slice().reverse().map(h => `
      <div class="hit">
        <span class="t">${h.time}</span>
        <span class="${statusClass(h.status)}">${h.status}</span>
        <span class="p">${h.path}</span>
      </div>`).join('');
    return `
    <div class="card">
      <div class="card-head">
        <div class="dot ${dot}"></div>
        <div class="name">${s.ip}</div>
        <span class="badge">${s.device}</span>
        <span class="badge">${s.browser}</span>
        <span class="badge">${s.os}</span>
      </div>
      <div class="meta">
        <span>First seen: <b>${s.first_seen}</b></span>
        <span>Last active: <b>${s.last_seen}</b></span>
        <span>Total requests: <b>${s.total_hits}</b></span>
        <span>Status: <b style="color:var(--${dot === 'active' ? 'green' : dot === 'idle' ? 'orange' : 'muted'})">${dot === 'active' ? '● Active' : dot === 'idle' ? '◌ Idle' : '○ Offline'}</b></span>
      </div>
      <div class="hits">${hitsHtml || '<span style="color:var(--muted);font-size:.78rem">No requests yet</span>'}</div>
    </div>`;
  }).join('');

  // Login events panel
  const evBox = document.getElementById('loginEvents');
  const LOGIN_ACTIONS = {
    'face_login_success': ['act-ok',   '✅ Face recognised'],
    'face_login_fail':    ['act-fail',  '❌ Face not matched'],
    'face_skipped':       ['act-warn',  '⏭ Face skipped by user'],
    'face_unavailable':   ['act-info',  'ℹ️ Face unavailable'],
    'face_no_enrolled':   ['act-info',  'ℹ️ No faces enrolled'],
    'pin_login_success':  ['act-ok',   '✅ PIN login success'],
    'pin_login_fail':     ['act-fail',  '❌ PIN wrong'],
    'pin_login_locked':   ['act-fail',  '🔒 Account locked out'],
  };
  if (data.login_events && data.login_events.length) {
    evBox.innerHTML = data.login_events.map(ev => {
      const [cls, label] = LOGIN_ACTIONS[ev.action] || ['act-info', ev.action];
      let detStr = '';
      try { const d = JSON.parse(ev.detail||'{}'); detStr = Object.entries(d).map(([k,v])=>`${k}=${v}`).join(' · '); } catch{}
      return `<div class="ev">
        <span class="t">${ev.time_str}</span>
        <span class="ip">${ev.ip}</span>
        <span class="badge-id">${ev.badge||'—'}</span>
        <span class="${cls}">${label}</span>
        <span class="det">${detStr}</span>
      </div>`;
    }).join('');
  } else {
    evBox.innerHTML = '<div class="empty">No login attempts yet</div>';
  }

  document.getElementById('refreshNote').textContent =
    'Last refreshed ' + new Date().toLocaleTimeString();
}

load();
setInterval(load, 3000);
</script>
</body>
</html>
"""


# ─── Request-handler helpers (module-level to keep class method count low) ─────


def _send_json_bytes(handler, body: bytes) -> None:
    """Send pre-encoded JSON bytes with standard cache headers."""
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _handle_post_log(handler, ip, payload):
    badge = str(payload.get("badge", ""))[:64]
    action = str(payload.get("action", ""))[:64]
    detail = payload.get("detail", {})
    if not isinstance(detail, dict):
        detail = {"value": str(detail)[:256]}
    db_insert_action(ip, badge, action, detail, now_ts())
    handler._send_json(200, {"ok": True})


def _handle_post_ack(handler, ip, payload):
    badge = str(payload.get("badge", ""))[:64]
    alert_key = str(payload.get("alertKey", ""))[:64]
    db_insert_ack(alert_key, badge, ip, now_ts())
    db_insert_action(ip, badge, "ackAlert", {"alertKey": alert_key}, now_ts())
    handler._send_json(200, {"ok": True})


def _handle_post_anomaly(handler, ip, payload):
    atype = str(payload.get("type", "unknown"))[:64]
    message = str(payload.get("message", ""))[:256]
    value = float(payload.get("value", 0) or 0)
    threshold = float(payload.get("threshold", 0) or 0)
    db_insert_anomaly(atype, message, value, threshold, now_ts())
    handler._send_json(200, {"ok": True})


# ─── Conversation memory with TTL and eviction (ported from Local_LLM KVCacheManager) ─
_MAX_HISTORY = 20  # max messages per conversation (system excluded)
_MAX_SESSIONS = 128  # max concurrent conversations before LRU eviction
_SESSION_TTL = 1800.0  # 30 min idle timeout


class _ConversationStore:
    """Thread-safe conversation memory with TTL expiration and LRU eviction.

    Ported from Local_LLM's KVCacheManager eviction/TTL patterns.
    """

    def __init__(self, max_history: int = 20, max_sessions: int = 128,
                 ttl: float = 1800.0):
        self._max_history = max_history
        self._max_sessions = max(1, max_sessions)
        self._ttl = ttl
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}  # key → {messages, last_access}

    def get(self, key: str) -> list[dict]:
        with self._lock:
            self._evict_expired()
            entry = self._sessions.get(key)
            if entry is None:
                return []
            entry["last_access"] = time.monotonic()
            return list(entry["messages"])

    def append(self, key: str, role: str, content: str) -> None:
        with self._lock:
            self._evict_expired()
            if key not in self._sessions:
                if len(self._sessions) >= self._max_sessions:
                    self._evict_oldest()
                self._sessions[key] = {"messages": [], "last_access": time.monotonic()}
            entry = self._sessions[key]
            entry["messages"].append({"role": role, "content": content})
            if len(entry["messages"]) > self._max_history:
                entry["messages"] = entry["messages"][-self._max_history:]
            entry["last_access"] = time.monotonic()

    def clear(self, key: str) -> None:
        with self._lock:
            self._sessions.pop(key, None)

    def active_sessions(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _evict_expired(self) -> None:
        if self._ttl <= 0:
            return
        now = time.monotonic()
        expired = [k for k, v in self._sessions.items()
                   if now - v["last_access"] > self._ttl]
        for k in expired:
            del self._sessions[k]

    def _evict_oldest(self) -> None:
        if not self._sessions:
            return
        oldest = min(self._sessions, key=lambda k: self._sessions[k]["last_access"])
        del self._sessions[oldest]


_conversation_store = _ConversationStore(
    max_history=_MAX_HISTORY, max_sessions=_MAX_SESSIONS, ttl=_SESSION_TTL,
)


def _get_conversation(badge: str) -> list[dict]:
    return _conversation_store.get(badge)


def _append_conversation(badge: str, role: str, content: str) -> None:
    _conversation_store.append(badge, role, content)


def _clear_conversation(badge: str) -> None:
    _conversation_store.clear(badge)


# ─── Retry with exponential backoff ──────────────────────────────────────────
_INFERENCE_RETRIES = 2
_INFERENCE_BACKOFF = 1.0  # seconds, doubles each retry


def _admit_request(source: str, priority: MessagePriority = MessagePriority.NORMAL,
                   timeout: float = 10.0) -> bool:
    """Gate a request through the FIFO buffer for admission control.

    Returns True if the request was admitted. Returns False if the buffer is
    full (backpressure — caller should return 503).
    """
    admitted = _request_buffer.put(
        {"source": source, "ts": time.time()},
        priority=priority,
        timeout=timeout,
    )
    if not admitted:
        logger.warning("Request rejected (backpressure): source=%s", source)
    return admitted


def _release_request() -> None:
    """Release a slot from the request buffer after inference completes."""
    _request_buffer.get_nowait()


async def _query_with_retry(engine, **kwargs):
    """Call engine.query() with retries on failure. Yields chunks (streaming).

    Tracks latency and token count via _inference_metrics.
    Publishes completed response tokens to _response_buffer for observability.
    """
    last_exc = None
    start = time.time()
    token_count = 0
    for attempt in range(_INFERENCE_RETRIES + 1):
        try:
            async for chunk in engine.query(**kwargs):
                token_count += 1
                yield chunk
            # Track metrics + publish to response buffer
            latency = time.time() - start
            _inference_metrics.record(latency, token_count)
            _response_buffer.put_nowait({
                "tokens": token_count,
                "latency": round(latency, 2),
                "source": kwargs.get("message", "")[:80],
            })
            return  # success
        except Exception as exc:
            last_exc = exc
            if attempt < _INFERENCE_RETRIES:
                logger.warning(
                    "Inference attempt %d/%d failed: %s — retrying",
                    attempt + 1, _INFERENCE_RETRIES, exc,
                )
                await asyncio.sleep(_INFERENCE_BACKOFF * (2**attempt))
    _inference_metrics.record(time.time() - start, token_count, error=True)
    raise last_exc  # type: ignore[misc]


# ─── OpenAI-compatible helpers ────────────────────────────────────────────────


def _make_chat_id() -> str:
    return "chatcmpl-" + _uuid.uuid4().hex[:12]


def _clamp(val, lo, hi):
    return max(lo, min(hi, val))


def _handle_health(handler):
    """GET /health — readiness check with memory monitoring (Local_LLM compatible)."""
    engine, err = get_llm_engine(blocking=False)
    memory = {}
    if _HAS_PSUTIL:
        vm = psutil.virtual_memory()
        memory = {
            "ram_total_gb": round(vm.total / (1024**3), 1),
            "ram_free_gb": round(vm.available / (1024**3), 1),
            "ram_used_pct": vm.percent,
        }
    body = json.dumps(
        {
            "status": "ok" if engine and not err else "error",
            "model_loaded": engine is not None and err is None,
            "model": os.path.basename(str(engine.model_path))
            if engine and hasattr(engine, "model_path") and engine.model_path
            else None,
            "memory": memory,
            "inference": _inference_metrics.stats(),
            "request_buffer": _request_buffer.stats(),
            "response_buffer": _response_buffer.stats(),
            "active_conversations": _conversation_store.active_sessions(),
        }
    ).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _handle_v1_models(handler):
    """GET /v1/models — list available GGUF models (OpenAI-compatible)."""
    models = scan_gguf_models()
    engine = _llm_engine
    current = (
        os.path.basename(str(engine.model_path))
        if engine and hasattr(engine, "model_path") and engine.model_path
        else None
    )
    data = {
        "object": "list",
        "data": [
            {
                "id": m["name"],
                "object": "model",
                "owned_by": "local",
                "size_gb": m["size_gb"],
                "active": m["name"] == current,
            }
            for m in models
        ],
    }
    body = json.dumps(data).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _v1_send_error(handler, status, message, error_type="server_error"):
    """Send a JSON error response for /v1 endpoints."""
    body = json.dumps({"error": {"message": message, "type": error_type}}).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _v1_parse_chat_request(payload):
    """Parse and validate /v1/chat/completions payload → (params_dict, error_msg)."""
    messages = payload.get("messages", [])
    if not messages:
        return None, "messages required"

    temperature = _clamp(float(payload.get("temperature", 0.7)), 0.0, 2.0)
    top_p = _clamp(float(payload.get("top_p", 0.9)), 0.0, 1.0)
    max_tokens = _clamp(int(payload.get("max_tokens", 2048)), 1, 131072)
    repeat_penalty = float(payload.get("repeat_penalty", 1.1))

    system_prompt = ""
    user_prompt = ""
    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))
        if role == "system":
            system_prompt = content
        elif role == "user":
            user_prompt = content

    query_kwargs = {
        "message": user_prompt,
        "system_prompt": system_prompt,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "repeat_penalty": repeat_penalty,
        "stream": True,
    }
    grammar = payload.get("grammar")
    json_schema = payload.get("json_schema")
    json_mode = bool(payload.get("json_mode", False))
    if grammar:
        query_kwargs["grammar"] = grammar
    if json_schema:
        query_kwargs["json_schema"] = json_schema
    if json_mode:
        query_kwargs["json_mode"] = json_mode
    if len(messages) > 1:
        query_kwargs["messages"] = messages

    return query_kwargs, None


def _v1_stream_sse(handler, engine, query_kwargs, chat_id, model_name):
    """Write SSE streaming response for /v1/chat/completions."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    try:
        q: deque[str] = deque()
        done_event = threading.Event()
        exc_box: list[Exception] = []

        async def _pump():
            try:
                async for chunk in _query_with_retry(engine, **query_kwargs):
                    q.append(chunk)
            except Exception as e:
                exc_box.append(e)
            finally:
                done_event.set()

        asyncio.run_coroutine_threadsafe(_pump(), _get_llm_loop())

        while not done_event.is_set() or q:
            while q:
                tok = q.popleft()
                evt = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "model": model_name,
                    "choices": [
                        {"index": 0, "delta": {"content": tok}, "finish_reason": None}
                    ],
                }
                handler.wfile.write(f"data: {json.dumps(evt)}\n\n".encode())
                handler.wfile.flush()
            if not done_event.is_set():
                time.sleep(0.02)

        if exc_box:
            handler.wfile.write(
                f"data: {json.dumps({'error': str(exc_box[0])})}\n\n".encode()
            )

        final = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "model": model_name,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        handler.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()
    except (ConnectionResetError, BrokenPipeError):
        pass


def _v1_collect_response(handler, engine, query_kwargs, chat_id, model_name):
    """Collect full non-streaming response for /v1/chat/completions."""

    async def _collect():
        parts = []
        async for chunk in _query_with_retry(engine, **query_kwargs):
            parts.append(chunk)
        return "".join(parts)

    try:
        future = asyncio.run_coroutine_threadsafe(_collect(), _get_llm_loop())
        reply = future.result(timeout=120)
    except Exception as exc:
        _v1_send_error(handler, 500, str(exc))
        return

    result = {
        "id": chat_id,
        "object": "chat.completion",
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(reply.split()),
            "total_tokens": 0,
        },
    }
    body = json.dumps(result).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _handle_v1_chat_completions(handler, payload):
    """POST /v1/chat/completions — OpenAI-compatible chat (streaming SSE + non-streaming)."""
    engine, err = get_llm_engine(blocking=False)
    if err or engine is None:
        _v1_send_error(handler, 503, f"AI engine unavailable: {err or 'not loaded'}")
        return

    query_kwargs, parse_err = _v1_parse_chat_request(payload)
    if parse_err:
        _v1_send_error(handler, 400, parse_err, "invalid_request_error")
        return

    # Admission control via request buffer
    if not _admit_request("v1/chat/completions"):
        _v1_send_error(handler, 503, "Server busy — try again shortly")
        return

    chat_id = _make_chat_id()
    model_name = (
        os.path.basename(str(engine.model_path))
        if hasattr(engine, "model_path") and engine.model_path
        else "local"
    )

    if bool(payload.get("stream", False)):
        _v1_stream_sse(handler, engine, query_kwargs, chat_id, model_name)
    else:
        _v1_collect_response(handler, engine, query_kwargs, chat_id, model_name)

    _release_request()


def _handle_v1_completions(handler, payload):
    """POST /v1/completions — legacy text completion (OpenAI-compatible)."""
    engine, err = get_llm_engine(blocking=False)
    if err or engine is None:
        body = json.dumps(
            {
                "error": {
                    "message": f"AI engine unavailable: {err or 'not loaded'}",
                    "type": "server_error",
                }
            }
        ).encode()
        handler.send_response(503)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(body)
        return

    prompt = str(payload.get("prompt", ""))
    if not prompt:
        body = json.dumps(
            {"error": {"message": "prompt required", "type": "invalid_request_error"}}
        ).encode()
        handler.send_response(400)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(body)
        return

    temperature = _clamp(float(payload.get("temperature", 0.7)), 0.0, 2.0)
    top_p = _clamp(float(payload.get("top_p", 0.9)), 0.0, 1.0)
    max_tokens = _clamp(int(payload.get("max_tokens", 2048)), 1, 131072)

    # Admission control via request buffer
    if not _admit_request("v1/completions"):
        body = json.dumps(
            {"error": {"message": "Server busy \u2014 try again shortly", "type": "server_error"}}
        ).encode()
        handler.send_response(503)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(body)
        return

    async def _collect():
        parts = []
        async for chunk in _query_with_retry(
            engine,
            message=prompt,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
        ):
            parts.append(chunk)
        return "".join(parts)

    try:
        future = asyncio.run_coroutine_threadsafe(_collect(), _get_llm_loop())
        text = future.result(timeout=120)
    except Exception as exc:
        body = json.dumps(
            {"error": {"message": str(exc), "type": "server_error"}}
        ).encode()
        handler.send_response(500)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(body)
        return

    model_name = (
        os.path.basename(str(engine.model_path))
        if engine and hasattr(engine, "model_path") and engine.model_path
        else "local"
    )
    result = {
        "id": "cmpl-" + _uuid.uuid4().hex[:12],
        "object": "text_completion",
        "model": model_name,
        "choices": [{"text": text, "index": 0, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(text.split()),
            "total_tokens": 0,
        },
    }
    body = json.dumps(result).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)

    _release_request()


_CHAT_LANG_NAMES = {
    "en": "English",
    "ro": "Romanian",
    "hu": "Hungarian",
    "de": "German",
    "fr": "French",
}


def _build_chat_system_prompt(lang):
    """Build the ZenAI system prompt with requested reply language."""
    reply_lang = _CHAT_LANG_NAMES.get(lang, "English")
    return (
        "You are ZenAI, a smart assistant embedded in ZenAIos — a hospital operations dashboard. "
        "Answer the user's question directly and accurately. "
        "If the question is about hospital operations, clinical data, triage, or patient flow, "
        "give a concise (2–5 sentences) actionable answer and remind the user to verify clinical "
        "data in the official EMR system. "
        "For all other questions (general knowledge, calculations, language, images, etc.), "
        "just answer normally without forcing a medical angle. "
        f"IMPORTANT: Always reply in {reply_lang}, regardless of the language you were trained in."
    )


def _handle_post_chat_stream(handler, ip, payload):
    """SSE streaming chat with multi-turn memory and retry (module-level)."""
    message = str(payload.get("message", "")).strip()[:2000]
    badge = str(payload.get("badge", ""))[:64]
    lang = str(payload.get("lang", "en"))[:10]
    if not message:
        handler._send_json(400, {"error": "empty message"})
        return

    db_insert_action(ip, badge, "chatSend", {"message": message[:200]}, now_ts())

    engine, err = get_llm_engine(blocking=False)
    if err or engine is None:
        handler._send_json(
            503, {"error": f"AI engine unavailable: {err or 'not loaded'}"}
        )
        return

    # Admission control via request buffer
    conv_key = badge or ip
    if not _admit_request(f"chat-stream:{conv_key}"):
        handler._send_json(503, {"error": "Server busy — try again shortly"})
        return

    system = _build_chat_system_prompt(lang)
    history = _get_conversation(conv_key)
    _append_conversation(conv_key, "user", message)

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    reply_parts: list[str] = []

    try:
        q: deque[str] = deque()
        done_event = threading.Event()
        exc_box: list[Exception] = []

        async def _pump():
            try:
                async for chunk in _query_with_retry(
                    engine,
                    message=message,
                    system_prompt=system,
                    temperature=0.6,
                    top_p=0.85,
                    max_tokens=512,
                    repeat_penalty=1.15,
                    stream=True,
                    messages=[{"role": "system", "content": system}]
                    + history
                    + [{"role": "user", "content": message}],
                ):
                    q.append(chunk)
            except Exception as e:
                exc_box.append(e)
            finally:
                done_event.set()

        asyncio.run_coroutine_threadsafe(_pump(), _get_llm_loop())

        while not done_event.is_set() or q:
            while q:
                tok = q.popleft()
                reply_parts.append(tok)
                handler.wfile.write(f"data: {json.dumps({'token': tok})}\n\n".encode())
                handler.wfile.flush()
            if not done_event.is_set():
                time.sleep(0.02)

        if exc_box:
            handler.wfile.write(
                f"data: {json.dumps({'error': str(exc_box[0])})}\n\n".encode()
            )

        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()

    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        _release_request()

    reply = "".join(reply_parts)
    _append_conversation(conv_key, "assistant", reply)
    db_insert_action(ip, badge, "chatReply", {"reply": reply[:200]}, now_ts())


def _handle_post_chat_clear(handler, ip, payload):
    """Clear conversation history for a badge (module-level)."""
    badge = str(payload.get("badge", ""))[:64]
    _clear_conversation(badge or ip)
    handler._send_json(200, {"ok": True})


# ─── Swarm POST dispatcher ────────────────────────────────────────────────────


def _swarm_arena(p):
    models = p.get("models", [])
    if not models:
        return {"error": "models list is empty", "results": []}
    return _swarm.run_arena_sync(  # type: ignore[union-attr]
        model_paths=models,
        prompt=str(p.get("prompt", "")),
        system_prompt=str(p.get("system_prompt", "")),
        max_tokens=int(p.get("max_tokens", 256)),
        temperature=float(p.get("temperature", 0.7)),
        n_ctx=int(p.get("n_ctx", 2048)),
        n_gpu_layers=int(p.get("n_gpu_layers", 0)),
    )


def _swarm_benchmark(p):
    return _swarm.run_benchmark_sync(  # type: ignore[union-attr]
        model_path=str(p.get("model", "")),
        prompt=str(p.get("prompt", "Explain quicksort in 3 sentences.")),
        concurrency_levels=p.get("levels", [1, 2, 4]),
        max_tokens=int(p.get("max_tokens", 256)),
        temperature=float(p.get("temperature", 0.7)),
    )


def _swarm_inference(p):
    return _swarm.run_single_inference(  # type: ignore[union-attr]
        model_path=str(p.get("model", "")),
        prompt=str(p.get("prompt", "")),
        system_prompt=str(p.get("system_prompt", "")),
        max_tokens=int(p.get("max_tokens", 256)),
        temperature=float(p.get("temperature", 0.7)),
    )


def _swarm_evaluate(p):
    return _swarm.evaluate(  # type: ignore[union-attr]
        response_text=str(p.get("response", "")),
        category=str(p.get("category", "")),
        prompt_text=str(p.get("prompt", "")),
    )


def _swarm_marathon_round(p):
    models = p.get("models", [])
    if not models:
        return {"error": "models list is empty", "results": []}
    return _swarm.marathon_run_round(  # type: ignore[union-attr]
        model_paths=models,
        prompt=str(p.get("prompt", "")),
        category=str(p.get("category", "custom")),
        system_prompt=str(p.get("system_prompt", "")),
        max_tokens=int(p.get("max_tokens", 256)),
        temperature=float(p.get("temperature", 0.7)),
    )


_SWARM_DISPATCH = {
    "arena": _swarm_arena,
    "benchmark": _swarm_benchmark,
    "inference": _swarm_inference,
    "evaluate": _swarm_evaluate,
    "marathon-round": _swarm_marathon_round,
    "diagnose": lambda p: _swarm.diagnose(p.get("results", [])),  # type: ignore[union-attr]
    "pool-preload": lambda p: _swarm.pool_preload(p.get("models", [])),  # type: ignore[union-attr]
    "pool-drain": lambda p: _swarm.pool_drain(),  # type: ignore[union-attr]
    "recommendations": lambda p: {"tips": _swarm.recommendations(p.get("result", {}))},  # type: ignore[union-attr]
}


def _handle_swarm_post(handler, action, payload):
    """Dispatch /__swarm/* POST endpoints to swarm_bridge."""
    if not _swarm or not _SWARM_OK:
        handler._send_json(503, {"error": "Swarm bridge unavailable"})
        return

    fn = _SWARM_DISPATCH.get(action)
    if fn is None:
        handler._send_json(404, {"error": f"unknown swarm action: {action}"})
        return

    try:
        result = fn(payload)
        body = json.dumps(result, ensure_ascii=False, default=str).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(body)
    except Exception as exc:
        handler._send_json(500, {"error": str(exc)})


# ─── Request handler ───────────────────────────────────────────────────────────
class ZenHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        ip = self.client_address[0]
        ua = self.headers.get("User-Agent", "")

        # Admin routes — don't log these into the activity feed
        if path == "/__admin":
            html = ADMIN_HTML.replace("__LOCAL_IP__", LOCAL_IP).replace(
                "__PORT__", str(PORT)
            )
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/__admin/data":
            return _send_json_bytes(self, self._admin_json())

        if path == "/__admin/db-stats":
            return _send_json_bytes(self, self._db_stats_json())

        if path == "/__admin/actions":
            return _send_json_bytes(self, self._actions_json())

        if path == "/__admin/models":
            engine = _llm_engine
            current = (
                str(engine.model_path)
                if engine and hasattr(engine, "model_path") and engine.model_path
                else ""
            )
            models = scan_gguf_models()
            with _download_lock:
                dl = dict(_download_status)
            body = json.dumps(
                {"models": models, "current": current, "downloads": dl},
                ensure_ascii=False,
            ).encode()
            return _send_json_bytes(self, body)

        if path == "/__admin/download-status":
            with _download_lock:
                dl = dict(_download_status)
            return _send_json_bytes(self, json.dumps(dl).encode())

        if path == "/__model":
            engine, _ = get_llm_engine(blocking=False)
            name = (
                os.path.basename(str(engine.model_path))
                if engine and engine.model_path
                else "not loaded"
            )
            return _send_json_bytes(self, json.dumps({"model": name}).encode())

        # ── OpenAI-compatible API routes ──
        if path == "/health":
            return _handle_health(self)
        if path == "/v1/models":
            return _handle_v1_models(self)

        # ── Swarm test routes (GET) ──
        if path == "/__swarm/status":
            return _send_json_bytes(self, json.dumps(
                _swarm.status() if _swarm else {"available": False, "error": "swarm_bridge not imported"}
            ).encode())
        if path == "/__swarm/models":
            return _send_json_bytes(self, json.dumps(
                {"models": _swarm.list_models()} if _swarm else {"models": scan_gguf_models()}
            ).encode())
        if path == "/__swarm/prompts":
            return _send_json_bytes(self, json.dumps(
                _swarm.get_prompts() if _swarm else {"categories": {}, "total": 0}
            ).encode())
        if path == "/__swarm/random-prompt":
            return _send_json_bytes(self, json.dumps(
                _swarm.get_random_prompt() if _swarm else {"prompt": "Explain recursion."}
            ).encode())
        if path == "/__swarm/pool":
            return _send_json_bytes(self, json.dumps(
                _swarm.pool_status() if _swarm else {"enabled": False, "size": 0}
            ).encode())
        if path == "/__swarm/memory":
            return _send_json_bytes(self, json.dumps(
                _swarm.memory_snapshot() if _swarm else {"error": "bridge unavailable"}
            ).encode())

        # Silence favicon.ico — return empty 204 so browser stops requesting it
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # Serve the actual file and record the hit
        # Capture status by wrapping send_response
        self._status_code = 200
        super().do_GET()
        record_hit(ip, path or "/", self._status_code, ua)

    def send_response(self, code, message=None):
        self._status_code = code
        super().send_response(code, message)

    def _admin_json(self):
        now = now_ts()
        result = []
        for ip, s in sessions.items():
            age = now - s["last_seen"]
            result.append(
                {
                    "ip": ip,
                    "device": s["device"],
                    "browser": s["browser"],
                    "os": s["os"],
                    "first_seen": datetime.fromtimestamp(s["first_seen"]).strftime(
                        "%H:%M:%S"
                    ),
                    "last_seen": datetime.fromtimestamp(s["last_seen"]).strftime(
                        "%H:%M:%S"
                    ),
                    "last_seen_ts": s["last_seen"],
                    "total_hits": len(s["hits"]),
                    "active": age < 60,
                    "idle": 60 <= age < 300,
                    "recent_hits": s["hits"][-20:],
                }
            )
        uptime_s = int(now - SERVER_START)
        h, r = divmod(uptime_s, 3600)
        m, s_ = divmod(r, 60)
        uptime = f"{h}h {m}m {s_}s" if h else f"{m}m {s_}s"
        # Read LLM state WITHOUT triggering a load (avoids blocking admin panel)
        engine = _llm_engine
        llm_model = (
            os.path.basename(str(engine.model_path))
            if engine and hasattr(engine, "model_path") and engine.model_path
            else ("loading…" if not _llm_error else "unavailable")
        )

        # Recent login events from DB
        login_events = []
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT ip, badge, action, detail, time_str FROM actions "
                "WHERE action LIKE 'face_%' OR action LIKE 'pin_%' "
                "ORDER BY id DESC LIMIT 50"
            ).fetchall()
            login_events = [dict(r) for r in rows]
        except Exception:
            pass

        models_list = scan_gguf_models()
        # Read current model path without blocking on LLM load
        current_model_path = (
            str(engine.model_path)
            if engine and hasattr(engine, "model_path") and engine.model_path
            else ""
        )
        with _download_lock:
            dl_status = dict(_download_status)

        return json.dumps(
            {
                "sessions": result,
                "uptime": uptime,
                "llm_model": llm_model,
                "login_events": login_events,
                "models": models_list,
                "current_model_path": current_model_path,
                "downloads": dl_status,
                "inference_metrics": _inference_metrics.stats(),
                "request_buffer": _request_buffer.stats(),
                "response_buffer": _response_buffer.stats(),
                "active_conversations": _conversation_store.active_sessions(),
                "memory": {
                    "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
                    "ram_free_gb": round(psutil.virtual_memory().available / (1024**3), 1),
                    "ram_used_pct": psutil.virtual_memory().percent,
                } if _HAS_PSUTIL else {},
            }
        ).encode()

    def _db_stats_json(self):
        """Quick summary stats read straight from the database."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            total_sessions = conn.execute("SELECT COUNT(*) n FROM sessions").fetchone()[
                "n"
            ]
            total_hits = conn.execute("SELECT COUNT(*) n FROM hits").fetchone()["n"]
            top_pages = [
                dict(r)
                for r in conn.execute(
                    "SELECT path, COUNT(*) n FROM hits GROUP BY path ORDER BY n DESC LIMIT 10"
                ).fetchall()
            ]
            top_clients = [
                dict(r)
                for r in conn.execute(
                    "SELECT ip, device, browser, total_hits FROM sessions ORDER BY total_hits DESC LIMIT 10"
                ).fetchall()
            ]
            by_day = [
                dict(r)
                for r in conn.execute(
                    "SELECT date(timestamp,'unixepoch','localtime') d, COUNT(*) n "
                    "FROM hits GROUP BY d ORDER BY d DESC LIMIT 30"
                ).fetchall()
            ]
            conn.close()
            return json.dumps(
                {
                    "db_path": DB_PATH,
                    "total_sessions": total_sessions,
                    "total_hits": total_hits,
                    "top_pages": top_pages,
                    "top_clients": top_clients,
                    "hits_by_day": by_day,
                }
            ).encode()
        except Exception as e:
            return json.dumps({"error": str(e)}).encode()

    def _actions_json(self):
        """Recent in-app user actions from DB (last 200)."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, ip, badge, action, detail, time_str "
                    "FROM actions ORDER BY id DESC LIMIT 200"
                ).fetchall()
            ]
            conn.close()
            return json.dumps({"actions": rows}).encode()
        except Exception as exc:
            return json.dumps({"error": str(exc)}).encode()

    # ── POST handler ──────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}

        ip = self.client_address[0]
        # ── OpenAI-compatible API routes (handler, payload only) ──
        if path == "/v1/chat/completions":
            return _handle_v1_chat_completions(self, payload)
        if path == "/v1/completions":
            return _handle_v1_completions(self, payload)

        dispatch = {
            "/__log": lambda ip, p: _handle_post_log(self, ip, p),
            "/__ack": lambda ip, p: _handle_post_ack(self, ip, p),
            "/__anomaly": lambda ip, p: _handle_post_anomaly(self, ip, p),
            "/__chat": self._post_chat,
            "/__chat-stream": lambda ip, p: _handle_post_chat_stream(self, ip, p),
            "/__chat-clear": lambda ip, p: _handle_post_chat_clear(self, ip, p),
            "/__admin/set-model": self._post_set_model,
            "/__admin/download-model": self._post_download_model,
            "/__handover": self._post_handover,
            "/__swarm/arena": lambda ip, p: _handle_swarm_post(self, "arena", p),
            "/__swarm/benchmark": lambda ip, p: _handle_swarm_post(self, "benchmark", p),
            "/__swarm/inference": lambda ip, p: _handle_swarm_post(self, "inference", p),
            "/__swarm/evaluate": lambda ip, p: _handle_swarm_post(self, "evaluate", p),
            "/__swarm/marathon-round": lambda ip, p: _handle_swarm_post(self, "marathon-round", p),
            "/__swarm/diagnose": lambda ip, p: _handle_swarm_post(self, "diagnose", p),
            "/__swarm/pool/preload": lambda ip, p: _handle_swarm_post(self, "pool-preload", p),
            "/__swarm/pool/drain": lambda ip, p: _handle_swarm_post(self, "pool-drain", p),
            "/__swarm/recommendations": lambda ip, p: _handle_swarm_post(self, "recommendations", p),
        }
        handler = dispatch.get(path)
        if handler:
            handler(ip, payload)
        else:
            self._send_json(404, {"error": "endpoint not found"})

    # ── POST endpoint handlers ─────────────────────────────────────────────

    def _post_chat(self, ip, payload):
        """Non-streaming chat with multi-turn memory and retry."""
        message = str(payload.get("message", "")).strip()[:2000]
        badge = str(payload.get("badge", ""))[:64]
        lang = str(payload.get("lang", "en"))[:10]
        if not message:
            self._send_json(400, {"error": "empty message"})
            return

        db_insert_action(ip, badge, "chatSend", {"message": message[:200]}, now_ts())

        engine, err = get_llm_engine(blocking=False)
        if err or engine is None:
            self._send_json(
                503, {"error": f"AI engine unavailable: {err or 'not loaded'}"}
            )
            return

        # Admission control via request buffer
        if not _admit_request(f"chat:{badge or ip}"):
            self._send_json(503, {"error": "Server busy \u2014 try again shortly"})
            return

        _LANG_NAMES = {
            "en": "English",
            "ro": "Romanian",
            "hu": "Hungarian",
            "de": "German",
            "fr": "French",
        }
        _reply_lang = _LANG_NAMES.get(lang, "English")
        SYSTEM = (
            "You are ZenAI, a smart assistant embedded in ZenAIos — a hospital operations dashboard. "
            "Answer the user's question directly and accurately. "
            "If the question is about hospital operations, clinical data, triage, or patient flow, "
            "give a concise (2–5 sentences) actionable answer and remind the user to verify clinical "
            "data in the official EMR system. "
            "For all other questions (general knowledge, calculations, language, images, etc.), "
            "just answer normally without forcing a medical angle. "
            f"IMPORTANT: Always reply in {_reply_lang}, regardless of the language you were trained in."
        )

        # Build multi-turn messages
        history = _get_conversation(badge or ip)
        _append_conversation(badge or ip, "user", message)

        async def _collect_reply():
            parts = []
            async for chunk in _query_with_retry(
                engine,
                message=message,
                system_prompt=SYSTEM,
                temperature=0.6,
                top_p=0.85,
                max_tokens=512,
                repeat_penalty=1.15,
                stream=True,
                messages=[{"role": "system", "content": SYSTEM}]
                + history
                + [{"role": "user", "content": message}],
            ):
                parts.append(chunk)
            return "".join(parts)

        try:
            future = asyncio.run_coroutine_threadsafe(_collect_reply(), _get_llm_loop())
            reply = future.result(timeout=120)
        except Exception as exc:
            reply = f"❌ Inference error: {exc}"

        _append_conversation(badge or ip, "assistant", reply)
        db_insert_action(ip, badge, "chatReply", {"reply": reply[:200]}, now_ts())
        self._send_json(200, {"reply": reply})

        _release_request()

    def _post_set_model(self, ip, payload):
        model_path = str(payload.get("path", "")).strip()
        if not model_path:
            self._send_json(400, {"error": "path required"})
            return
        result_box = []

        def _switch():
            ok, msg = _do_switch_model(model_path)
            result_box.append((ok, msg))

        t = threading.Thread(target=_switch, daemon=True)
        t.start()
        t.join(timeout=130)
        if not result_box:
            self._send_json(503, {"error": "timeout switching model"})
        elif result_box[0][0]:
            self._send_json(200, {"ok": True, "model": result_box[0][1]})
        else:
            self._send_json(500, {"error": result_box[0][1]})

    def _post_download_model(self, ip, payload):
        repo_id = str(payload.get("repo_id", "")).strip()
        filename = str(payload.get("filename", "")).strip()
        dest_dir = str(payload.get("dest_dir", r"C:\AI\Models")).strip()
        if not repo_id or not filename:
            self._send_json(400, {"error": "repo_id and filename required"})
            return
        filename = os.path.basename(filename)
        if not filename.lower().endswith(".gguf"):
            self._send_json(400, {"error": "only .gguf files allowed"})
            return
        key = filename
        with _download_lock:
            if (
                key in _download_status
                and _download_status[key].get("state") == "running"
            ):
                self._send_json(200, {"ok": True, "state": "already_running"})
                return
            _download_status[key] = {"state": "running", "progress": 0, "error": None}

        def _download():
            try:
                from huggingface_hub import hf_hub_download

                os.makedirs(dest_dir, exist_ok=True)
                local_path = hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    local_dir=dest_dir,
                    revision="main",
                )
                with _download_lock:
                    _download_status[key] = {
                        "state": "done",
                        "progress": 100,
                        "path": local_path,
                        "error": None,
                    }
                print(f"  [DL] ✅ {filename} ready at {local_path}")
            except Exception as exc:
                with _download_lock:
                    _download_status[key] = {
                        "state": "error",
                        "progress": 0,
                        "error": str(exc),
                    }
                print(f"  [DL] ❌ {filename}: {exc}")

        threading.Thread(target=_download, daemon=True, name=f"dl-{key}").start()
        self._send_json(200, {"ok": True, "state": "started", "filename": filename})

    def _post_handover(self, ip, payload):
        badge = str(payload.get("badge", ""))[:64]
        cutoff = now_ts() - 28800  # last 8 hours
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            act_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT badge, action, detail, time_str FROM actions "
                    "WHERE timestamp > ? ORDER BY id DESC LIMIT 100",
                    (cutoff,),
                ).fetchall()
            ]
            ack_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT alert_key, badge, time_str FROM alert_acks "
                    "WHERE timestamp > ? ORDER BY id DESC",
                    (cutoff,),
                ).fetchall()
            ]
            anom_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT type, message, time_str FROM anomalies "
                    "WHERE timestamp > ? ORDER BY id DESC",
                    (cutoff,),
                ).fetchall()
            ]
            conn.close()
        except Exception:
            act_rows, ack_rows, anom_rows = [], [], []

        lines = [
            f"=== SHIFT HANDOVER — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===",
            f"Prepared by: {badge or 'unknown'}",
            "",
            f"ACTIONS THIS SHIFT ({len(act_rows)} events):",
        ]
        for r in act_rows[:30]:
            lines.append(
                f"  [{r['time_str']}] {r['badge'] or '?'} — {r['action']}: {r['detail']}"
            )
        lines += ["", f"ALERTS ACKNOWLEDGED ({len(ack_rows)}):"]
        for r in ack_rows:
            lines.append(
                f"  [{r['time_str']}] {r['badge'] or '?'} acked {r['alert_key']}"
            )
        lines += ["", f"ANOMALIES DETECTED ({len(anom_rows)}):"]
        for r in anom_rows:
            lines.append(f"  [{r['time_str']}] {r['type']}: {r['message']}")

        report = "\n".join(lines)
        db_insert_action(ip, badge, "handover", {"lines": len(lines)}, now_ts())
        self._send_json(200, {"report": report})

    def log_message(self, format, *args):  # noqa: A002 — override must match base class param name
        # Only print to terminal if it's not an admin call or favicon
        path = str(args[0]) if args else ""
        if "/__admin" in path or "favicon" in path or "NOT_FOUND" in path:
            return
        ip = self.client_address[0]
        print(f"  [{now_str()}] {ip:>15}  {path}")


# ─── Resilient TCP server ──────────────────────────────────────────────────────
class ZenServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys

        err = sys.exc_info()[1]
        if isinstance(err, (ConnectionResetError, ConnectionAbortedError,
                             BrokenPipeError)):
            return
        if isinstance(err, OSError) and err.errno in (10053, 10054, 32):
            return
        super().handle_error(request, client_address)


# ─── Entry point ──────────────────────────────────────────────────────────────
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


LOCAL_IP = get_local_ip()
SERVER_START = time.time()

if __name__ == "__main__":
    init_db()  # Create tables on first run
    print()
    _W = 54  # inner box width (visual columns)
    _bar = "═" * _W

    def _row(s: str) -> str:
        # Pad content to _W chars. For lines with no emoji, Python len == visual width.
        return f"  ║{s:<{_W}}║"

    _title = "   \U0001f3e5  ZenAIos Smart Server"  # emoji is 2 visual cols but 1 Python char
    print(f"  ╔{_bar}╗")
    print(f"  ║{_title:<{_W - 1}}║")  # -1 compensates for emoji extra column
    print(f"  ╠{_bar}╣")
    print(_row(f"  App      →  http://localhost:{PORT}/login.html"))
    print(_row(f"  LAN      →  http://{LOCAL_IP}:{PORT}"))
    print(_row(f"  Admin    →  http://localhost:{PORT}/__admin"))
    print(_row(f"  DB Stats →  http://localhost:{PORT}/__admin/db-stats"))
    print(_row(f"  Database →  {os.path.basename(DB_PATH)}"))
    print(f"  ╠{_bar}╣")
    print(_row("  Press Ctrl+C to stop"))
    print(f"  ╚{_bar}╝")
    print()

    # Eagerly warm up the LLM in the background so the model name appears
    # in the log within seconds instead of waiting for the first chat.
    threading.Thread(target=get_llm_engine, daemon=True, name="llm-warmup").start()

    with ZenServer(("", PORT), ZenHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server stopped.")
            httpd.server_close()
