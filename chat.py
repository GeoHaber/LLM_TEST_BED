r"""
LLM Chat — Local AI Chat with OpenAI-Compatible API
=====================================================
Single-file app merging the best of Local_LLM + standalone chat.py.

  - FastAPI + Uvicorn for async HTTP & SSE streaming
  - llama-server.exe as inference backend (robust, supports Qwen3.5)
  - OpenAI-compatible /v1/chat/completions endpoint
  - Real-time token streaming to embedded browser UI
  - Multi-turn conversation with session history
  - Smart model discovery & categorization
  - Runtime model switching via API
  - Cross-platform (Windows/macOS/Linux)

Dependencies:
    pip install fastapi uvicorn httpx

Usage:
    python chat.py                         # auto-detect everything
    python chat.py --model path/to.gguf    # specific model
    python chat.py --port 9090             # custom UI port
    python chat.py --no-browser            # headless mode

Press Ctrl+C to stop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chat")

# ── Configuration ─────────────────────────────────────────────────────────────

LLAMA_PORT   = 8888
UI_PORT      = 8080
CONTEXT_SIZE = 4096
MAX_TOKENS   = 2048

_IS_WIN = platform.system() == "Windows"

_DEFAULT_MODELS_DIR = (
    Path(r"C:\AI\Models") if _IS_WIN
    else Path.home() / ".local" / "share" / "models"
)

MODEL_DIRS: list[Path] = [
    Path(os.environ.get("SWARM_MODELS_DIR", "")) or _DEFAULT_MODELS_DIR,
    _DEFAULT_MODELS_DIR,
    Path.home() / "AI" / "Models",
    Path.home() / "models",
    Path.home() / "AppData" / "Local" / "lm-studio" / "models" if _IS_WIN else Path("/dev/null"),
]

_SCRIPT_DIR = Path(__file__).resolve().parent

SERVER_SEARCH_PATHS: list[Path] = [
    _SCRIPT_DIR / "bin" / ("llama-server.exe" if _IS_WIN else "llama-server"),
    Path(r"C:\AI\bin\llama-server.exe") if _IS_WIN else Path("/usr/local/bin/llama-server"),
    Path(r"C:\AI\llama-server.exe") if _IS_WIN else Path("/dev/null"),
]

# ── Model categories ─────────────────────────────────────────────────────────

_WEAK_PATTERNS = ("tinyllama", "smollm", "smol-", "135m", "360m", "phi-2-2.7b", "phi-2.")

_PREFERRED_PATTERNS = [
    r"qwen3[\.\-_]?5",            # Qwen 3.5 — latest, great quality
    r"qwen3(?![\.\-_]?5)",        # Qwen 3 (not 3.5)
    r"llama-3\.2-3b",             # Llama 3.2 3B — fast & smart
    r"phi-3\.5-mini",             # Phi 3.5 mini
    r"mistral-7b-instruct",       # Mistral 7B
    r"qwen2\.5",                  # Qwen 2.5 family
    r"gemma-2",                   # Gemma 2
    r"deepseek",                  # DeepSeek
]


# ═════════════════════════════════════════════════════════════════════════════
# MODEL DISCOVERY
# ═════════════════════════════════════════════════════════════════════════════

class ModelInfo:
    """Lightweight model descriptor."""
    __slots__ = ("name", "path", "size_bytes", "size_gb", "category")

    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.size_bytes = path.stat().st_size
        self.size_gb = round(self.size_bytes / (1024 ** 3), 2)
        self.category = self._categorize()

    def _categorize(self) -> str:
        n = self.name.lower()
        if any(w in n for w in _WEAK_PATTERNS):
            return "toy"
        if self.size_gb < 1.5:
            return "fast"
        if self.size_gb < 10:
            return "balanced"
        return "large"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "path": str(self.path),
            "size_gb": self.size_gb, "category": self.category,
        }


def scan_models() -> list[ModelInfo]:
    """Discover all GGUF models (>= 50 MB) across known directories."""
    seen: set[str] = set()
    models: list[ModelInfo] = []
    for d in MODEL_DIRS:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.suffix.lower() == ".gguf":
                continue
            try:
                sz = f.stat().st_size
            except OSError:
                continue
            if sz < 50_000_000:
                continue
            key = f.name.lower()
            if key in seen:
                continue
            seen.add(key)
            models.append(ModelInfo(f))
    models.sort(key=lambda m: m.name.lower())
    return models


def pick_best_model(models: list[ModelInfo]) -> Optional[ModelInfo]:
    """Pick the best model by preference pattern, skipping toys."""
    usable = [m for m in models if m.category != "toy"]
    if not usable:
        usable = models
    if not usable:
        return None
    for pat in _PREFERRED_PATTERNS:
        for m in usable:
            if re.search(pat, m.name, re.IGNORECASE):
                return m
    # Fallback: smallest balanced, then smallest overall
    balanced = [m for m in usable if m.category == "balanced"]
    if balanced:
        return min(balanced, key=lambda m: m.size_bytes)
    return min(usable, key=lambda m: m.size_bytes)


# ═════════════════════════════════════════════════════════════════════════════
# LLAMA-SERVER LIFECYCLE
# ═════════════════════════════════════════════════════════════════════════════

class LlamaServer:
    """Manages a llama-server.exe subprocess."""

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._exe: Optional[Path] = None
        self._model: Optional[ModelInfo] = None
        self._log_file = None
        self._base_url = f"http://127.0.0.1:{LLAMA_PORT}"

    @property
    def model(self) -> Optional[ModelInfo]:
        return self._model

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def find_executable(self) -> Optional[Path]:
        """Locate llama-server executable."""
        for p in SERVER_SEARCH_PATHS:
            if p.is_file():
                return p
        found = shutil.which("llama-server") or shutil.which("llama-server.exe")
        return Path(found) if found else None

    async def start(self, exe: Path, model: ModelInfo) -> None:
        """Start llama-server with the given model."""
        await self.stop()
        self._exe = exe
        self._model = model

        cmd = [
            str(exe), "-m", str(model.path),
            "--host", "127.0.0.1", "--port", str(LLAMA_PORT),
            "-c", str(CONTEXT_SIZE), "-np", "1", "--no-webui",
        ]
        log.info(f"Starting llama-server on :{LLAMA_PORT} ...")
        self._log_file = open(_SCRIPT_DIR / "llama-server.log", "w")
        self._proc = subprocess.Popen(cmd, stdout=self._log_file, stderr=self._log_file)

        # Wait for /health -> {"status":"ok"}
        deadline = time.time() + 180
        async with httpx.AsyncClient() as client:
            while time.time() < deadline:
                try:
                    r = await client.get(f"{self._base_url}/health", timeout=3)
                    data = r.json()
                    if data.get("status") == "ok":
                        log.info("Model loaded and ready!")
                        return
                except Exception:
                    if self._proc.poll() is not None:
                        raise RuntimeError("llama-server crashed during startup")
                await asyncio.sleep(1)
        await self.stop()
        raise TimeoutError("llama-server did not become ready in 180s")

    async def stop(self) -> None:
        """Gracefully stop the server."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except Exception:
                self._proc.kill()
            log.info("llama-server stopped")
        self._proc = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    async def switch_model(self, model: ModelInfo) -> None:
        """Hot-swap to a different model (restart server)."""
        if not self._exe:
            raise RuntimeError("No executable found")
        log.info(f"Switching model -> {model.name} ({model.size_gb} GB)")
        await self.start(self._exe, model)

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = MAX_TOKENS,
    ) -> AsyncGenerator[str, None]:
        """Stream content tokens from llama-server.

        Qwen 3.5 sends reasoning_content (thinking) before content.
        We only yield content tokens. If the model produces no content
        at all, we fall back to the buffered reasoning tokens.
        """
        body = {
            "model": "local",
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": True,
        }
        reasoning_buf: list[str] = []
        content_seen = False

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                json=body,
                timeout=httpx.Timeout(180, connect=10),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        delta = json.loads(line[6:])["choices"][0]["delta"]
                        tok = delta.get("content")
                        if tok:
                            content_seen = True
                            yield tok
                        else:
                            rtok = delta.get("reasoning_content")
                            if rtok:
                                reasoning_buf.append(rtok)
                    except Exception:
                        pass

        # Fallback: if model only produced reasoning (no content), yield that
        if not content_seen and reasoning_buf:
            for rtok in reasoning_buf:
                yield rtok

    async def chat_complete(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = MAX_TOKENS,
    ) -> str:
        """Non-streaming chat — collects all content tokens."""
        parts: list[str] = []
        async for tok in self.chat_stream(messages, temperature, top_p, max_tokens):
            parts.append(tok)
        return "".join(parts)


# ═════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS (OpenAI-compatible)
# ═════════════════════════════════════════════════════════════════════════════

class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        if v not in {"system", "user", "assistant"}:
            raise ValueError(f"role must be system/user/assistant, got {v!r}")
        return v


class ChatCompletionRequest(BaseModel):
    model: str = "local"
    messages: list[ChatMessage]
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    max_tokens: int = Field(default=MAX_TOKENS, ge=1, le=131072)
    stream: bool = False

    @field_validator("messages")
    @classmethod
    def non_empty(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        if not v:
            raise ValueError("messages must not be empty")
        return v


class ModelSwitchRequest(BaseModel):
    model: str  # filename or full path


# ═════════════════════════════════════════════════════════════════════════════
# CONVERSATION HISTORY
# ═════════════════════════════════════════════════════════════════════════════

class ConversationManager:
    """Manages multi-turn conversation sessions."""

    def __init__(self, max_turns: int = 20):
        self.max_turns = max_turns
        self._sessions: Dict[str, list[dict]] = {}
        self._system = (
            "You are a helpful, friendly AI assistant. "
            "Answer clearly and concisely."
        )

    def get_messages(self, session_id: str, user_msg: str) -> list[dict]:
        """Add user message and return full history for inference."""
        if session_id not in self._sessions:
            self._sessions[session_id] = [
                {"role": "system", "content": self._system}
            ]
        history = self._sessions[session_id]
        history.append({"role": "user", "content": user_msg})
        # Trim to max turns (keep system + last N pairs)
        if len(history) > 1 + self.max_turns * 2:
            history[:] = history[:1] + history[-(self.max_turns * 2):]
        return list(history)

    def add_assistant_reply(self, session_id: str, reply: str) -> None:
        if session_id in self._sessions:
            self._sessions[session_id].append(
                {"role": "assistant", "content": reply}
            )

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        self._sessions.clear()


# ═════════════════════════════════════════════════════════════════════════════
# FASTAPI APPLICATION
# ═════════════════════════════════════════════════════════════════════════════

server = LlamaServer()
conversations = ConversationManager()
all_models: list[ModelInfo] = []

# Startup/shutdown model path — set by main() before uvicorn.run()
_startup_model_path: Optional[str] = None
_open_browser: bool = True
_ui_port: int = UI_PORT


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Modern lifespan handler replacing deprecated on_event."""
    await startup(_startup_model_path)
    if _open_browser:
        import threading
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{_ui_port}")).start()
    yield
    await server.stop()
    log.info("Bye!")


app = FastAPI(
    title="LLM Chat — Local AI",
    version="2.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health / Status ───────────────────────────────────────────────────────

@app.get("/health")
async def health():
    m = server.model
    return {
        "status": "ok" if server.is_running else "starting",
        "model_loaded": server.is_running,
        "model": m.name if m else None,
    }


@app.get("/__status")
async def ui_status():
    m = server.model
    return {
        "model": m.name if m else "none",
        "size_gb": m.size_gb if m else 0,
        "category": m.category if m else "unknown",
        "models": [mi.to_dict() for mi in all_models],
        "running": server.is_running,
    }


# ── OpenAI-compatible: List models ────────────────────────────────────────

@app.get("/v1/models")
async def list_models():
    data = [
        {
            "id": m.name,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "local",
            "meta": {"size_gb": m.size_gb, "category": m.category},
        }
        for m in all_models
    ]
    return {"object": "list", "data": data}


# ── OpenAI-compatible: Chat completions ───────────────────────────────────

async def _sse_chat_stream(req: ChatCompletionRequest) -> AsyncGenerator[str, None]:
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    mdl = server.model.name if server.model else "local"
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]

    # Role delta
    yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': mdl, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"

    async for token in server.chat_stream(
        msgs, req.temperature, req.top_p, req.max_tokens
    ):
        chunk = {
            "id": cid, "object": "chat.completion.chunk",
            "created": created, "model": mdl,
            "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"

    final = {
        "id": cid, "object": "chat.completion.chunk",
        "created": created, "model": mdl,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    if not server.is_running:
        raise HTTPException(503, "Model not loaded")

    if req.stream:
        return StreamingResponse(
            _sse_chat_stream(req),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    text = await server.chat_complete(msgs, req.temperature, req.top_p, req.max_tokens)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": server.model.name if server.model else "local",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
    }


# ── Model switching ───────────────────────────────────────────────────────

@app.post("/__switch")
async def switch_model(req: ModelSwitchRequest):
    target = req.model
    match = None
    for m in all_models:
        if m.name == target or str(m.path) == target:
            match = m
            break
    if not match:
        for m in all_models:
            if target.lower() in m.name.lower():
                match = m
                break
    if not match:
        raise HTTPException(404, f"Model not found: {target}")
    await server.switch_model(match)
    conversations.clear_all()
    return {"status": "ok", "model": match.name, "size_gb": match.size_gb}


# ── Browser chat endpoint (with conversation history) ─────────────────────

class UIChatRequest(BaseModel):
    message: str
    session_id: str = "default"


@app.post("/__chat")
async def ui_chat(req: UIChatRequest):
    if not server.is_running:
        return JSONResponse(status_code=503, content={"error": "Model not loaded"})
    msg = req.message.strip()
    if not msg:
        return JSONResponse(status_code=400, content={"error": "empty message"})

    messages = conversations.get_messages(req.session_id, msg)
    try:
        reply = await server.chat_complete(messages)
        conversations.add_assistant_reply(req.session_id, reply)
        return {"reply": reply}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})


# ── Browser chat with SSE streaming ───────────────────────────────────────

@app.post("/__chat_stream")
async def ui_chat_stream(req: UIChatRequest):
    if not server.is_running:
        raise HTTPException(503, "Model not loaded")
    msg = req.message.strip()
    if not msg:
        raise HTTPException(400, "empty message")

    messages = conversations.get_messages(req.session_id, msg)

    async def generate():
        parts: list[str] = []
        async for tok in server.chat_stream(messages):
            parts.append(tok)
            yield f"data: {json.dumps({'token': tok})}\n\n"
        reply = "".join(parts)
        conversations.add_assistant_reply(req.session_id, reply)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── Clear conversation ────────────────────────────────────────────────────

@app.post("/__clear")
async def clear_conversation(session_id: str = "default"):
    conversations.clear(session_id)
    return {"status": "ok"}


# ═════════════════════════════════════════════════════════════════════════════
# EMBEDDED BROWSER UI
# ═════════════════════════════════════════════════════════════════════════════

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Chat</title>
<style>
  :root { --bg:#1a1a2e; --card:#16213e; --accent:#0f3460; --hi:#e94560; --txt:#eee; --dim:#888; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'Segoe UI',system-ui,sans-serif; background:var(--bg); color:var(--txt); display:flex; flex-direction:column; height:100vh; }
  header { background:var(--accent); padding:12px 20px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  header h1 { font-size:1.2rem; font-weight:600; }
  .pill { background:var(--hi); color:#fff; padding:2px 10px; border-radius:12px; font-size:.75rem; cursor:default; }
  .pill.cat { background:#2d6a4f; }
  #status { margin-left:auto; font-size:.8rem; color:var(--dim); }
  #chat { flex:1; overflow-y:auto; padding:16px 20px; display:flex; flex-direction:column; gap:10px; }
  .msg { max-width:80%; padding:10px 14px; border-radius:12px; line-height:1.5; white-space:pre-wrap; word-wrap:break-word; }
  .msg.user { align-self:flex-end; background:var(--accent); border-bottom-right-radius:2px; }
  .msg.bot  { align-self:flex-start; background:var(--card); border-bottom-left-radius:2px; }
  #bar { display:flex; gap:8px; padding:12px 20px; background:var(--card); }
  #bar input { flex:1; padding:10px 14px; border:none; border-radius:8px; background:var(--bg); color:var(--txt); font-size:1rem; outline:none; }
  #bar input:focus { box-shadow:0 0 0 2px var(--hi); }
  #bar button { padding:10px 16px; border:none; border-radius:8px; background:var(--hi); color:#fff; font-size:1rem; cursor:pointer; transition:.15s; }
  #bar button:hover:not(:disabled) { filter:brightness(1.15); }
  #bar button:disabled { opacity:.5; cursor:not-allowed; }
  #bar button.clear { background:#555; font-size:.85rem; padding:10px 12px; }
  footer { padding:6px 20px; background:var(--card); border-top:1px solid #ffffff10; font-size:.75rem; color:var(--dim); display:flex; gap:16px; flex-wrap:wrap; }
  footer select { background:var(--bg); color:var(--txt); border:1px solid #ffffff20; border-radius:4px; padding:2px 6px; font-size:.75rem; }
</style>
</head>
<body>
<header>
  <h1>AI Chat</h1>
  <span class="pill" id="model-name">loading...</span>
  <span class="pill cat" id="model-cat"></span>
  <span id="status">connecting...</span>
</header>
<div id="chat"></div>
<div id="bar">
  <input id="inp" placeholder="Type a message..." autocomplete="off" disabled>
  <button id="btn" onclick="send()" disabled>Send</button>
  <button class="clear" onclick="clearChat()" title="Clear conversation">Clear</button>
</div>
<footer>
  <span id="info"></span>
  <label>Model: <select id="model-select" onchange="switchModel(this.value)"></select></label>
</footer>

<script>
const chat = document.getElementById('chat');
const inp  = document.getElementById('inp');
const btn  = document.getElementById('btn');
const st   = document.getElementById('status');
const mn   = document.getElementById('model-name');
const mc   = document.getElementById('model-cat');
const sel  = document.getElementById('model-select');
const info = document.getElementById('info');
let busy = false;

async function loadStatus() {
  try {
    const r = await fetch('/__status');
    const d = await r.json();
    mn.textContent = d.model;
    mc.textContent = d.category;
    st.textContent = d.running ? 'Ready' : 'Loading...';
    inp.disabled = !d.running; btn.disabled = !d.running;
    if (d.running) inp.focus();
    sel.innerHTML = '';
    (d.models||[]).forEach(m => {
      const o = document.createElement('option');
      o.value = m.name;
      o.textContent = m.name + ' (' + m.size_gb + 'GB, ' + m.category + ')';
      if (m.name === d.model) o.selected = true;
      sel.appendChild(o);
    });
    info.textContent = (d.models||[]).length + ' models available';
  } catch(e) { st.textContent = 'Not connected'; }
}
loadStatus();

function addMsg(text, cls) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
  return d;
}

async function send() {
  const text = inp.value.trim();
  if (!text || busy) return;
  inp.value = '';
  addMsg(text, 'user');
  busy = true; btn.disabled = true; inp.disabled = true;
  st.textContent = 'Thinking...';

  const botDiv = addMsg('', 'bot');
  try {
    const r = await fetch('/__chat_stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text})
    });
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (line.startsWith('data: ') && line !== 'data: [DONE]') {
          try {
            const tok = JSON.parse(line.slice(6)).token;
            if (tok) { botDiv.textContent += tok; chat.scrollTop = chat.scrollHeight; }
          } catch(e) {}
        }
      }
    }
  } catch(e) {
    botDiv.textContent = 'Error: ' + e.message;
  }
  if (!botDiv.textContent) botDiv.textContent = '(empty response)';
  st.textContent = 'Ready';
  busy = false; btn.disabled = false; inp.disabled = false; inp.focus();
}

async function clearChat() {
  await fetch('/__clear', {method:'POST'});
  chat.innerHTML = '';
}

async function switchModel(name) {
  if (!name) return;
  st.textContent = 'Switching model...';
  btn.disabled = true; inp.disabled = true;
  try {
    const r = await fetch('/__switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: name})
    });
    const d = await r.json();
    if (d.status === 'ok') {
      chat.innerHTML = '';
      await loadStatus();
    } else {
      st.textContent = 'Error: ' + (d.error || 'Switch failed');
    }
  } catch(e) { st.textContent = 'Error: ' + e.message; }
  btn.disabled = false; inp.disabled = false;
}

inp.addEventListener('keydown', e => { if (e.key === 'Enter' && !busy) send(); });
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTML_PAGE


# ═════════════════════════════════════════════════════════════════════════════
# STARTUP & MAIN
# ═════════════════════════════════════════════════════════════════════════════

async def startup(model_path: Optional[str] = None) -> None:
    """Discover models, find server, start inference."""
    global all_models

    print("\n  -- AI Chat Setup ------------------------------------------------")

    # 1. Find llama-server
    exe = server.find_executable()
    if not exe:
        print("  [ERROR]  llama-server not found!")
        print("      Put it in ./bin/ or C:\\AI\\bin\\ or add to PATH.")
        print("      Download: https://github.com/ggml-org/llama.cpp/releases")
        sys.exit(1)
    print(f"  [OK]  llama-server: {exe}")

    # 2. Discover models
    all_models = scan_models()
    if not all_models:
        print("  [ERROR]  No .gguf models found!")
        print(f"      Place models in: {_DEFAULT_MODELS_DIR}")
        sys.exit(1)
    print(f"  [OK]  Found {len(all_models)} model(s):")
    for m in all_models:
        tag = f"[{m.category}]"
        print(f"      - {m.name:45s} {m.size_gb:6.2f} GB  {tag}")

    # 3. Pick model
    if model_path:
        p = Path(model_path)
        if not p.exists():
            print(f"  [ERROR]  Model not found: {model_path}")
            sys.exit(1)
        chosen = ModelInfo(p)
    else:
        chosen = pick_best_model(all_models)
    if not chosen:
        print("  [ERROR]  No suitable model found.")
        sys.exit(1)
    print(f"\n  >>> Using: {chosen.name}  ({chosen.size_gb} GB, {chosen.category})")

    # 4. Start llama-server
    print()
    await server.start(exe, chosen)


def main():
    global _startup_model_path, _open_browser, _ui_port

    import argparse
    ap = argparse.ArgumentParser(description="LLM Chat — Local AI with OpenAI API")
    ap.add_argument("--model", default="", help="Path to a .gguf model file")
    ap.add_argument("--port", type=int, default=UI_PORT, help=f"UI port (default {UI_PORT})")
    ap.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = ap.parse_args()

    _startup_model_path = args.model or None
    _open_browser = not args.no_browser
    _ui_port = args.port

    print("\n  -- LLM Chat ------------------------------------------------------")
    print(f"  UI:        http://localhost:{_ui_port}")
    print(f"  OpenAI:    http://localhost:{_ui_port}/v1/chat/completions")
    print("  Press Ctrl+C to stop\n")

    uvicorn.run(app, host="0.0.0.0", port=_ui_port, log_level="warning")


if __name__ == "__main__":
    main()
