r"""
Simple LLM Chat — standalone app
=================================
One file. No dependencies beyond Python 3.10+ stdlib.

  1. Finds llama-server.exe
  2. Scans for GGUF models (prefers Qwen 3.5)
  3. Launches llama-server.exe with the best model
  4. Opens a browser with a simple chat UI
  5. Proxies chat requests to llama-server.exe

Usage:
    python chat.py              (auto-detect everything)
    python chat.py --model C:\AI\Models\some.gguf
    python chat.py --port 9090  (default: 8080)

Press Ctrl+C to stop.
"""

import http.server
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

# ── Configuration ─────────────────────────────────────────────────────────────

LLAMA_PORT    = 8888          # llama-server.exe listens here
UI_PORT       = 8080          # browser UI served here
CONTEXT_SIZE  = 4096
MAX_TOKENS    = 2048          # generous budget (Qwen 3.5 uses thinking tokens)

MODEL_DIRS = [
    r"C:\AI\Models",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "lm-studio", "models"),
    os.path.join(os.path.expanduser("~"), ".ollama", "models"),
]

SERVER_SEARCH_PATHS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "llama-server.exe"),
    r"C:\AI\bin\llama-server.exe",
    r"C:\AI\llama-server.exe",
]

# ── Globals ───────────────────────────────────────────────────────────────────

_srv_proc = None  # subprocess.Popen for llama-server.exe


# ── Find llama-server.exe ────────────────────────────────────────────────────

def find_server() -> str | None:
    for p in SERVER_SEARCH_PATHS:
        if os.path.isfile(p):
            return p
    # Check PATH
    import shutil
    return shutil.which("llama-server") or shutil.which("llama-server.exe")


# ── Scan for GGUF models ────────────────────────────────────────────────────

def scan_models() -> list[dict]:
    seen, models = set(), []
    for d in MODEL_DIRS:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.lower().endswith(".gguf"):
                continue
            full = os.path.join(d, f)
            try:
                sz = os.path.getsize(full)
            except OSError:
                continue
            if sz < 50_000_000:  # skip tiny / partial
                continue
            key = f.lower()
            if key in seen:
                continue
            seen.add(key)
            models.append({"name": f, "path": full, "size_gb": round(sz / 1073741824, 2)})
    models.sort(key=lambda m: m["name"].lower())
    return models


def pick_best_model(models: list[dict]) -> dict | None:
    """Pick Qwen 3.5 if available, else Qwen 3, else first model."""
    if not models:
        return None
    # Preference order
    for pat in [r"qwen3[\.\-_]?5", r"qwen3", r"qwen"]:
        for m in models:
            if re.search(pat, m["name"], re.IGNORECASE):
                return m
    return models[0]


# ── llama-server.exe lifecycle ───────────────────────────────────────────────

def start_server(exe: str, model_path: str):
    global _srv_proc
    stop_server()

    cmd = [
        exe, "-m", model_path,
        "--host", "127.0.0.1", "--port", str(LLAMA_PORT),
        "-c", str(CONTEXT_SIZE), "-np", "1", "--no-webui",
    ]
    print(f"  Starting llama-server on :{LLAMA_PORT} …")
    log = open(os.path.join(os.path.dirname(__file__) or ".", "llama-server.log"), "w")
    _srv_proc = subprocess.Popen(cmd, stdout=log, stderr=log)

    # Wait for /health → {"status":"ok"}
    deadline = time.time() + 180
    url = f"http://127.0.0.1:{LLAMA_PORT}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                data = json.loads(r.read())
                if data.get("status") == "ok":
                    print("  ✅  Model loaded and ready!")
                    return
        except Exception:
            if _srv_proc.poll() is not None:
                raise RuntimeError("llama-server.exe crashed during startup")
        time.sleep(1)
    stop_server()
    raise TimeoutError("llama-server.exe did not become ready in 180 s")


def stop_server():
    global _srv_proc
    if _srv_proc and _srv_proc.poll() is None:
        _srv_proc.terminate()
        try:
            _srv_proc.wait(timeout=10)
        except Exception:
            _srv_proc.kill()
    _srv_proc = None


# ── Chat via llama-server OpenAI API ─────────────────────────────────────────

def chat_completion(messages: list[dict], temperature=0.6, top_p=0.85) -> str:
    """Send messages to llama-server and collect the streamed response."""
    body = json.dumps({
        "model": "local",
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": MAX_TOKENS,
        "stream": True,
    }).encode()

    req = urllib.request.Request(
        f"http://127.0.0.1:{LLAMA_PORT}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    content_parts, thinking_parts = [], []
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    delta = json.loads(line[6:])["choices"][0]["delta"]
                    tok = delta.get("content")
                    if tok:
                        content_parts.append(tok)
                    else:
                        rtok = delta.get("reasoning_content")
                        if rtok:
                            thinking_parts.append(rtok)
                except Exception:
                    pass
    return "".join(content_parts) or "".join(thinking_parts)


# ── HTML UI (embedded) ───────────────────────────────────────────────────────

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
  header { background:var(--accent); padding:12px 20px; display:flex; align-items:center; gap:12px; }
  header h1 { font-size:1.2rem; font-weight:600; }
  header .pill { background:var(--hi); color:#fff; padding:2px 10px; border-radius:12px; font-size:.75rem; }
  #status { margin-left:auto; font-size:.8rem; color:var(--dim); }
  #chat { flex:1; overflow-y:auto; padding:16px 20px; display:flex; flex-direction:column; gap:10px; }
  .msg { max-width:80%; padding:10px 14px; border-radius:12px; line-height:1.5; white-space:pre-wrap; word-wrap:break-word; }
  .msg.user { align-self:flex-end; background:var(--accent); border-bottom-right-radius:2px; }
  .msg.bot  { align-self:flex-start; background:var(--card); border-bottom-left-radius:2px; }
  .msg.bot.thinking { opacity:.6; font-style:italic; font-size:.85rem; }
  #bar { display:flex; gap:8px; padding:12px 20px; background:var(--card); }
  #bar input { flex:1; padding:10px 14px; border:none; border-radius:8px; background:var(--bg); color:var(--txt); font-size:1rem; outline:none; }
  #bar input:focus { box-shadow:0 0 0 2px var(--hi); }
  #bar button { padding:10px 20px; border:none; border-radius:8px; background:var(--hi); color:#fff; font-size:1rem; cursor:pointer; }
  #bar button:disabled { opacity:.5; cursor:not-allowed; }
  #models { padding:8px 20px; background:var(--card); border-top:1px solid #ffffff10; font-size:.8rem; color:var(--dim); }
</style>
</head>
<body>
<header>
  <h1>🤖 AI Chat</h1>
  <span class="pill" id="model-name">loading…</span>
  <span id="status">connecting…</span>
</header>
<div id="chat"></div>
<div id="bar">
  <input id="inp" placeholder="Type a message…" autocomplete="off" disabled>
  <button id="btn" onclick="send()" disabled>Send</button>
</div>
<div id="models"></div>

<script>
const chat = document.getElementById('chat');
const inp  = document.getElementById('inp');
const btn  = document.getElementById('btn');
const status = document.getElementById('status');
const modelName = document.getElementById('model-name');
const modelsDiv = document.getElementById('models');

// Load status
fetch('/__status').then(r=>r.json()).then(d => {
  modelName.textContent = d.model;
  status.textContent = '✅ Ready';
  inp.disabled = false; btn.disabled = false; inp.focus();
  if (d.models && d.models.length > 1) {
    modelsDiv.textContent = 'Available: ' + d.models.map(m => m.name + ' (' + m.size_gb + ' GB)').join(', ');
  }
}).catch(() => { status.textContent = '❌ Not connected'; });

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
  if (!text) return;
  inp.value = '';
  addMsg(text, 'user');
  btn.disabled = true; inp.disabled = true;
  status.textContent = '⏳ Thinking…';
  try {
    const r = await fetch('/__chat', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({message: text})
    });
    const d = await r.json();
    if (d.reply) addMsg(d.reply, 'bot');
    else addMsg('Error: ' + (d.error || 'no reply'), 'bot');
  } catch(e) {
    addMsg('Network error: ' + e.message, 'bot');
  }
  status.textContent = '✅ Ready';
  btn.disabled = false; inp.disabled = false; inp.focus();
}

inp.addEventListener('keydown', e => { if (e.key === 'Enter' && !btn.disabled) send(); });
</script>
</body>
</html>"""


# ── HTTP Handler ─────────────────────────────────────────────────────────────

class ChatHandler(http.server.BaseHTTPRequestHandler):
    server_version = "AI-Chat/1.0"
    model_info = {}   # set at startup
    all_models = []

    def log_message(self, fmt, *args):
        pass  # quiet

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = HTML_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/__status":
            self._json(200, {
                "model": self.model_info.get("name", "?"),
                "size_gb": self.model_info.get("size_gb", 0),
                "models": self.all_models,
            })
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/__chat":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw)
            except Exception:
                self._json(400, {"error": "bad JSON"})
                return
            message = str(payload.get("message", "")).strip()
            if not message:
                self._json(400, {"error": "empty message"})
                return

            system = (
                "You are a helpful, friendly AI assistant. "
                "Answer clearly and concisely."
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user",   "content": message},
            ]
            try:
                reply = chat_completion(messages)
                self._json(200, {"reply": reply})
            except Exception as exc:
                self._json(503, {"error": str(exc)})
        else:
            self.send_error(404)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    global UI_PORT
    import argparse
    ap = argparse.ArgumentParser(description="Simple LLM Chat")
    ap.add_argument("--model", default="", help="Path to a .gguf model file")
    ap.add_argument("--port",  type=int, default=UI_PORT, help=f"UI port (default {UI_PORT})")
    ap.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = ap.parse_args()

    UI_PORT = args.port

    # 1. Find llama-server.exe
    print("\n  ── AI Chat Setup ──────────────────────────────")
    exe = find_server()
    if not exe:
        print("  ❌  llama-server.exe not found!")
        print("      Put it in C:\\AI\\bin\\ or add to PATH.")
        print("      Download: https://github.com/ggerganov/llama.cpp/releases")
        sys.exit(1)
    print(f"  ✅  llama-server: {exe}")

    # 2. Find models
    models = scan_models()
    if not models:
        print("  ❌  No .gguf models found!")
        print(f"      Place models in: {', '.join(MODEL_DIRS)}")
        sys.exit(1)
    print(f"  ✅  Found {len(models)} model(s):")
    for m in models:
        print(f"      • {m['name']}  ({m['size_gb']} GB)")

    # 3. Pick model
    if args.model:
        chosen = {"name": os.path.basename(args.model), "path": args.model,
                  "size_gb": round(os.path.getsize(args.model) / 1073741824, 2)}
    else:
        chosen = pick_best_model(models)
    if not chosen:
        print("  ❌  No suitable model found.")
        sys.exit(1)
    print(f"\n  🎯  Using: {chosen['name']}  ({chosen['size_gb']} GB)")

    # 4. Start llama-server.exe
    print()
    try:
        start_server(exe, chosen["path"])
    except Exception as exc:
        print(f"  ❌  Failed to start llama-server: {exc}")
        sys.exit(1)

    # 5. Start UI
    ChatHandler.model_info = chosen
    ChatHandler.all_models = models

    # Graceful shutdown
    def _shutdown(sig, frame):
        print("\n  Shutting down…")
        stop_server()
        sys.exit(0)
    signal.signal(signal.SIGINT, _shutdown)

    httpd = http.server.HTTPServer(("", UI_PORT), ChatHandler)
    print("\n  ── Chat Ready ─────────────────────────────────")
    print(f"  🌐  http://localhost:{UI_PORT}")
    print(f"  📦  Model: {chosen['name']}")
    print("  Press Ctrl+C to stop\n")

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{UI_PORT}")).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_server()
        httpd.server_close()
        print("  Bye!")


if __name__ == "__main__":
    main()
