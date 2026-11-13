"""
zen_eval — Evaluation, Feedback & Prompt Versioning
=====================================================

Features:
  1. Multi-turn conversation judges       → zen_core_libs.eval.judge (imported)
  2. Judge alignment via human feedback    (SQLite-backed calibration)
  3. Prompt versioning with aliases        (SQLite-backed, immutable versions)
  4. ToolCall evaluators                   → zen_core_libs.eval.judge (imported)
  5. Local model gateway                   → zen_core_libs.llm.gateway (imported)
  6. Conversation persistence              (SQLite save/load)

Judges, tool-call evaluators, and the gateway live in zen_core_libs (single
source of truth).  This module re-exports them for backward compatibility
and adds SQLite persistence on top.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── zen_core_libs — single source of truth for judges & gateway ──────────────
from zen_core_libs.eval.judge import (  # noqa: F401 — re-exported for backward compat
    ConversationContext,
    JudgeResult,
    ToolCall,
    ToolCallExpectation,
    TurnData,
    judge_knowledge_retention,
    judge_tool_call_correctness,
    judge_tool_call_efficiency,
    judge_user_frustration,
)
from zen_core_libs.llm.gateway import (  # noqa: F401 — re-exported for backward compat
    GatewayRoute,
    LocalModelGateway,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  Database Layer
# ═══════════════════════════════════════════════════════════════════════════════

_DB_PATH = os.environ.get(
    "ZENEVAL_DB", str(Path(__file__).resolve().parent / "zeneval.db")
)
_db_lock = threading.Lock()


def _get_db() -> sqlite3.Connection:
    """Thread-safe database connection with WAL mode."""
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    """Create all tables if they don't exist.  Idempotent."""
    global _DB_PATH  # xray: ignore[PY-006]
    if db_path:
        _DB_PATH = db_path
    with _db_lock:
        conn = _get_db()
        conn.executescript(_SCHEMA)
        conn.close()


_SCHEMA = """
-- Prompt versioning
CREATE TABLE IF NOT EXISTS prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    template    TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    temperature REAL DEFAULT 0.7,
    max_tokens  INTEGER DEFAULT 512,
    commit_msg  TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS prompt_aliases (
    name        TEXT NOT NULL,
    alias       TEXT NOT NULL,
    version     INTEGER NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (name, alias)
);

-- Judge feedback for alignment
CREATE TABLE IF NOT EXISTS judge_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    judge_name  TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    response    TEXT NOT NULL,
    auto_score  REAL NOT NULL,
    human_score REAL,
    feedback    TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Conversation sessions for multi-turn judges
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    model_name  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    metadata    TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id     TEXT NOT NULL REFERENCES conversations(id),
    turn_num    INTEGER NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content     TEXT NOT NULL,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    metadata    TEXT DEFAULT '{}'
);

-- Gateway routing config
CREATE TABLE IF NOT EXISTS gateway_routes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    strategy    TEXT NOT NULL CHECK(strategy IN ('round_robin','weighted','fallback','ab_test')),
    models      TEXT NOT NULL,
    config      TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Gateway request log
CREATE TABLE IF NOT EXISTS gateway_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    route_name  TEXT NOT NULL,
    model_used  TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    latency_ms  REAL,
    tokens      INTEGER,
    success     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  1. Multi-Turn Conversation Judges → zen_core_libs.eval.judge
#     (judge_user_frustration, judge_knowledge_retention, etc. imported above)
# ═══════════════════════════════════════════════════════════════════════════════


# ── Conversation persistence helpers ─────────────────────────────────────────

def save_conversation(ctx: ConversationContext) -> str:
    """Persist a conversation and its turns to SQLite. Returns conversation_id."""
    with _db_lock:
        conn = _get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO conversations (id, model_name, metadata) VALUES (?, ?, ?)",
                (ctx.conversation_id, ctx.model_name, json.dumps(ctx.metadata)),
            )
            for t in ctx.turns:
                conn.execute(
                    "INSERT INTO conversation_turns (conv_id, turn_num, role, content, metadata) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ctx.conversation_id, t.turn_num, t.role, t.content,
                     json.dumps(t.metadata)),
                )
            conn.commit()
        finally:
            conn.close()
    return ctx.conversation_id


def _safe_json_loads(text: str) -> dict:
    """Parse JSON string, returning empty dict on failure."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def load_conversation(conv_id: str) -> ConversationContext | None:
    """Load a conversation from SQLite."""
    with _db_lock:
        conn = _get_db()
        try:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
            if not row:
                return None
            turns_rows = conn.execute(
                "SELECT * FROM conversation_turns WHERE conv_id = ? ORDER BY turn_num",
                (conv_id,),
            ).fetchall()
            turns = [
                TurnData(
                    role=r["role"], content=r["content"],
                    turn_num=r["turn_num"],
                    metadata=_safe_json_loads(r["metadata"] or "{}"),
                )
                for r in turns_rows
            ]
            return ConversationContext(
                conversation_id=row["id"],
                model_name=row["model_name"],
                turns=turns,
                metadata=_safe_json_loads(row["metadata"] or "{}"),
            )
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  2. Judge Alignment via Human Feedback
# ═══════════════════════════════════════════════════════════════════════════════


def record_feedback(
    judge_name: str,
    prompt: str,
    response: str,
    auto_score: float,
    human_score: float | None = None,
    feedback: str = "",
) -> int:
    """Record a judge feedback entry.  Returns the feedback ID."""
    with _db_lock:
        conn = _get_db()
        try:
            cur = conn.execute(
                "INSERT INTO judge_feedback (judge_name, prompt, response, auto_score, "
                "human_score, feedback) VALUES (?, ?, ?, ?, ?, ?)",
                (judge_name, prompt, response, auto_score, human_score, feedback),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]
        finally:
            conn.close()


def update_human_score(feedback_id: int, human_score: float, feedback: str = "") -> bool:
    """Update the human score for an existing feedback entry."""
    with _db_lock:
        conn = _get_db()
        try:
            cur = conn.execute(
                "UPDATE judge_feedback SET human_score = ?, feedback = ? WHERE id = ?",
                (human_score, feedback, feedback_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_alignment_stats(judge_name: str) -> dict:
    """Compute alignment statistics between auto and human scores.

    Returns:
        {
            "judge_name": str,
            "total_feedback": int,
            "aligned_count": int (human scores within 1.0 of auto),
            "alignment_rate": float (0-1),
            "avg_bias": float (auto - human, positive=overscoring),
            "calibration_offset": float (recommended adjustment),
            "correlation": float (Pearson r, if enough data),
        }
    """
    with _db_lock:
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT auto_score, human_score FROM judge_feedback "
                "WHERE judge_name = ? AND human_score IS NOT NULL",
                (judge_name,),
            ).fetchall()
        finally:
            conn.close()

    if not rows:
        return {
            "judge_name": judge_name, "total_feedback": 0,
            "aligned_count": 0, "alignment_rate": 0.0,
            "avg_bias": 0.0, "calibration_offset": 0.0, "correlation": 0.0,
        }

    pairs = [(r["auto_score"], r["human_score"]) for r in rows]
    n = len(pairs)
    diffs = [a - h for a, h in pairs]
    avg_bias = sum(diffs) / n

    aligned = sum(1 for d in diffs if abs(d) <= 1.0)
    alignment_rate = aligned / n

    # Pearson correlation
    correlation = 0.0
    if n >= 3:
        mean_a = sum(a for a, _ in pairs) / n
        mean_h = sum(h for _, h in pairs) / n
        cov = sum((a - mean_a) * (h - mean_h) for a, h in pairs) / n
        std_a = (sum((a - mean_a) ** 2 for a, _ in pairs) / n) ** 0.5
        std_h = (sum((h - mean_h) ** 2 for _, h in pairs) / n) ** 0.5
        if std_a > 0 and std_h > 0:
            correlation = cov / (std_a * std_h)

    return {
        "judge_name": judge_name,
        "total_feedback": n,
        "aligned_count": aligned,
        "alignment_rate": round(alignment_rate, 3),
        "avg_bias": round(avg_bias, 3),
        "calibration_offset": round(-avg_bias, 3),
        "correlation": round(correlation, 3),
    }


def calibrate_score(
    judge_name: str, raw_score: float, scale: float = 10.0
) -> float:
    """Apply learned calibration offset from human feedback.

    Adjusts the raw judge score using the average bias between
    auto-scores and human-scores.  Simple and transparent.

    Args:
        judge_name: The judge template name (e.g. "medical", "coding")
        raw_score: The raw auto-generated score (0 to scale)
        scale: Score scale (default 10.0)

    Returns:
        Calibrated score, clamped to [0, scale]
    """
    stats = get_alignment_stats(judge_name)
    offset = stats["calibration_offset"]
    calibrated = raw_score + offset
    return round(max(0.0, min(calibrated, scale)), 2)


def get_feedback_history(
    judge_name: str | None = None, limit: int = 50
) -> list[dict]:
    """Retrieve recent feedback entries."""
    with _db_lock:
        conn = _get_db()
        try:
            if judge_name:
                rows = conn.execute(
                    "SELECT * FROM judge_feedback WHERE judge_name = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (judge_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM judge_feedback ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  3. Prompt Versioning with Aliases
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Prompt:
    """A versioned prompt template."""
    name: str
    version: int
    template: str
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 512
    commit_msg: str = ""
    created_at: str = ""


def register_prompt(
    name: str,
    template: str,
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 512,
    commit_msg: str = "",
) -> Prompt:
    """Register a new version of a prompt.  Versions auto-increment.

    Prompt templates are IMMUTABLE once created.  To update, register a new version.
    """
    with _db_lock:
        conn = _get_db()
        try:
            # Find next version
            row = conn.execute(
                "SELECT MAX(version) as v FROM prompts WHERE name = ?", (name,)
            ).fetchone()
            next_ver = (row["v"] or 0) + 1

            conn.execute(
                "INSERT INTO prompts (name, version, template, system_prompt, "
                "temperature, max_tokens, commit_msg) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, next_ver, template, system_prompt, temperature, max_tokens, commit_msg),
            )
            conn.commit()

            return Prompt(
                name=name, version=next_ver, template=template,
                system_prompt=system_prompt, temperature=temperature,
                max_tokens=max_tokens, commit_msg=commit_msg,
            )
        finally:
            conn.close()


def load_prompt(
    name: str,
    version: int | None = None,
    alias: str | None = None,
) -> Prompt | None:
    """Load a prompt by name + version, name + alias, or latest.

    Resolution order:
      1. If alias given → resolve alias to version
      2. If version given → load that version
      3. Otherwise → load latest version

    Examples:
      load_prompt("medical_triage", version=3)
      load_prompt("medical_triage", alias="production")
      load_prompt("medical_triage")  # latest
    """
    with _db_lock:
        conn = _get_db()
        try:
            if alias:
                arow = conn.execute(
                    "SELECT version FROM prompt_aliases WHERE name = ? AND alias = ?",
                    (name, alias),
                ).fetchone()
                if not arow:
                    return None
                version = arow["version"]

            if version:
                row = conn.execute(
                    "SELECT * FROM prompts WHERE name = ? AND version = ?",
                    (name, version),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM prompts WHERE name = ? ORDER BY version DESC LIMIT 1",
                    (name,),
                ).fetchone()

            if not row:
                return None
            return Prompt(
                name=row["name"], version=row["version"],
                template=row["template"], system_prompt=row["system_prompt"],
                temperature=row["temperature"], max_tokens=row["max_tokens"],
                commit_msg=row["commit_msg"], created_at=row["created_at"],
            )
        finally:
            conn.close()


def set_alias(name: str, alias: str, version: int) -> bool:
    """Point an alias to a specific prompt version.

    Example: set_alias("medical_triage", "production", 3)
    """
    with _db_lock:
        conn = _get_db()
        try:
            # Verify the version exists
            row = conn.execute(
                "SELECT 1 FROM prompts WHERE name = ? AND version = ?",
                (name, version),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "INSERT OR REPLACE INTO prompt_aliases (name, alias, version) "
                "VALUES (?, ?, ?)",
                (name, alias, version),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def list_prompts(name: str | None = None) -> list[dict]:
    """List all prompts or all versions of a named prompt."""
    with _db_lock:
        conn = _get_db()
        try:
            if name:
                rows = conn.execute(
                    "SELECT p.*, GROUP_CONCAT(pa.alias) as aliases "
                    "FROM prompts p LEFT JOIN prompt_aliases pa "
                    "ON p.name = pa.name AND p.version = pa.version "
                    "WHERE p.name = ? GROUP BY p.id ORDER BY p.version DESC",
                    (name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT p.name, MAX(p.version) as latest_version, COUNT(*) as total_versions "
                    "FROM prompts p GROUP BY p.name ORDER BY p.name",
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def list_aliases(name: str) -> list[dict]:
    """List all aliases for a prompt."""
    with _db_lock:
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM prompt_aliases WHERE name = ? ORDER BY alias",
                (name,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def delete_alias(name: str, alias: str) -> bool:
    """Remove an alias."""
    with _db_lock:
        conn = _get_db()
        try:
            cur = conn.execute(
                "DELETE FROM prompt_aliases WHERE name = ? AND alias = ?",
                (name, alias),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  4. ToolCall Evaluators → zen_core_libs.eval.judge
#     (judge_tool_call_correctness, judge_tool_call_efficiency, etc. imported above)
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
#  5. Local Model Gateway → zen_core_libs.llm.gateway
#     (LocalModelGateway, GatewayRoute imported above)
# ═══════════════════════════════════════════════════════════════════════════════

# Module-level gateway instance (in-memory; SQLite tables above are retained
# for backward compatibility but no longer actively written to)
_gateway = LocalModelGateway()


def get_gateway() -> LocalModelGateway:
    """Get the module-level gateway instance."""
    return _gateway

