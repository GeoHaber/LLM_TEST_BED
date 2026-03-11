"""
zen_config — Single source of truth for every path, env-var, and sys.path setup.

Both server_with_swarm.py and swarm_bridge.py import this module.
To relocate the project or change the directory layout, edit THIS file only.

sys.path is patched at *import time* — no function call needed.
"""

from __future__ import annotations

import os
import sys

# ─── Base directories ─────────────────────────────────────────────────────────
# SWARM_DIR: the swarm/ folder that contains this file
SWARM_DIR = os.path.dirname(os.path.abspath(__file__))

# REPO_DIR: the LLM_TEST_BED checkout root (parent of swarm/)
REPO_DIR = os.path.normpath(os.path.join(SWARM_DIR, ".."))

# ─── Local_LLM sibling repo ──────────────────────────────────────────────────
# Default: ../../Local_LLM  (sibling of LLM_TEST_BED under the same parent)
# Override: set LOCAL_LLM_PATH env var to an absolute path
LOCAL_LLM_DIR = os.environ.get(
    "LOCAL_LLM_PATH",
    os.path.normpath(os.path.join(REPO_DIR, "..", "Local_LLM")),
)

# ─── llama-server.exe (HTTP backend) ─────────────────────────────────────────
_REPO_LLAMA = os.path.join(REPO_DIR, "bin", "llama-server.exe")
_SYSTEM_LLAMA = os.path.join(r"C:\AI\bin", "llama-server.exe")

LLAMA_SERVER_EXE = os.environ.get(
    "LLAMA_SERVER_EXE",
    _REPO_LLAMA if os.path.isfile(_REPO_LLAMA) else _SYSTEM_LLAMA,
)
LLAMA_SERVER_PORT = int(os.environ.get("LLAMA_SERVER_PORT", "8888"))

# ─── Model search directories ────────────────────────────────────────────────
_HOME = os.path.expanduser("~")
MODELS_DIRS: list[str] = [
    os.environ.get("ZEN_MODELS_DIR", os.path.join(r"C:\AI", "Models")),
    os.path.join(_HOME, "AppData", "Local", "lm-studio", "models"),
    os.path.join(_HOME, ".ollama", "models"),
    os.path.normpath(os.path.join(LOCAL_LLM_DIR, "models")),
]

# ─── Database ─────────────────────────────────────────────────────────────────
DB_PATH = os.environ.get(
    "ZEN_DB_PATH",
    os.path.join(SWARM_DIR, "zenai_activity.db"),
)

# ─── Server config ────────────────────────────────────────────────────────────
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("ZEN_PORT", "8777"))

# ─── Buffer tuning ────────────────────────────────────────────────────────────
REQUEST_BUF_MIN = int(os.environ.get("ZEN_REQ_BUF_MIN", "2"))
REQUEST_BUF_INIT = int(os.environ.get("ZEN_REQ_BUF_INIT", "10"))
REQUEST_BUF_MAX = int(os.environ.get("ZEN_REQ_BUF_MAX", "50"))
RESPONSE_BUF_MAX = int(os.environ.get("ZEN_RESP_BUF_MAX", "100"))
MAX_CONCURRENT_PER_IP = int(os.environ.get("ZEN_MAX_PER_IP", "3"))

# ─── sys.path setup (runs once at import time) ───────────────────────────────
for _d in (SWARM_DIR, LOCAL_LLM_DIR):
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.insert(0, _d)
del _d  # keep module namespace clean
