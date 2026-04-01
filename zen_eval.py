"""
zen_eval — Evaluation, Feedback, Prompt Versioning, Tool-Call Judges & Local Model Gateway
===========================================================================================

Features:
  1. Multi-turn conversation judges (UserFrustration, KnowledgeRetention)
  2. Judge alignment via human feedback (simple weighted calibration, no DSPy)
  3. Prompt versioning with aliases (SQLite-backed, immutable versions)
  4. ToolCall evaluators (function-call correctness & efficiency)
  5. Local model gateway (A/B routing, fallback chains between local GGUFs)

All persistence is SQLite — zero-cloud, zero-install philosophy.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

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
    global _DB_PATH
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
#  1. Multi-Turn Conversation Judges
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TurnData:
    """A single conversation turn."""
    role: str
    content: str
    turn_num: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class ConversationContext:
    """Full conversation context for multi-turn judges."""
    conversation_id: str
    model_name: str
    turns: list[TurnData]
    metadata: dict = field(default_factory=dict)


@dataclass
class JudgeResult:
    """Result from any judge evaluation."""
    judge_name: str
    score: float          # 0.0 to 1.0 normalized
    passed: bool
    rationale: str
    details: dict = field(default_factory=dict)


def _extract_questions_from_turns(turns: list[TurnData]) -> list[str]:
    """Extract user questions from conversation turns."""
    questions = []
    for t in turns:
        if t.role == "user":
            # Split compound questions
            parts = re.split(r'[?]\s+', t.content)
            for p in parts:
                p = p.strip()
                if len(p) > 5:
                    questions.append(p if p.endswith("?") else p + "?")
    return questions


def _assistant_turns(turns: list[TurnData]) -> list[TurnData]:
    """Filter to only assistant turns."""
    return [t for t in turns if t.role == "assistant"]


def _user_turns(turns: list[TurnData]) -> list[TurnData]:
    """Filter to only user turns."""
    return [t for t in turns if t.role == "user"]


# ── UserFrustration Judge ─────────────────────────────────────────────────────

_FRUSTRATION_INDICATORS = {
    "high": [
        r"\b(this is (terrible|awful|broken|useless|stupid|wrong))\b",
        r"\b(you('re| are) (not|never) (helping|useful|listening))\b",
        r"\b(i('ve| have) (already|just) (told|said|asked|explained))\b",
        r"\b(for the (last|third|fourth|fifth) time)\b",
        r"\b(stop (repeating|saying|giving me))\b",
        r"\b(are you (even|actually) (listening|reading))\b",
        r"\b(this (doesn't|does not) (work|help|answer|make sense))\b",
        r"\b(what('s| is) wrong with you)\b",
        r"\b(forget it|never ?mind|give up)\b",
    ],
    "medium": [
        r"\b(that('s| is) not what I (asked|meant|said|wanted))\b",
        r"\b(I (already|just) (said|mentioned|asked|told you))\b",
        r"\b(please (try|read|listen) (again|carefully|more carefully))\b",
        r"\b(no,? (that's|that is) (wrong|incorrect|not right))\b",
        r"\b(can you (please |actually |just )?focus)\b",
        r"\b(you('re| are) (missing|ignoring|not addressing))\b",
        r"\b(I (need|want) (a |an )?(different|better|actual) (answer|response|solution))\b",
    ],
    "low": [
        r"\b(I (don't|do not) (think|understand))\b",
        r"\b(could you (please )?clarify)\b",
        r"\b(I('m| am) confused)\b",
        r"\b(let me rephrase)\b",
    ],
}

# Weights: high=1.0, medium=0.5, low=0.2
_FRUSTRATION_WEIGHTS = {"high": 1.0, "medium": 0.5, "low": 0.2}


def judge_user_frustration(ctx: ConversationContext) -> JudgeResult:
    """Detect user frustration in a multi-turn conversation.

    Scoring:
      - Scans all user turns for frustration indicators (regex patterns)
      - Weights: high-severity=1.0, medium=0.5, low=0.2
      - Score = weighted_hits / total_user_turns (normalized 0-1)
      - Checks for escalation pattern (frustration increasing over time)
      - Checks if frustration was resolved (no indicators in last turn)
    """
    user_t = _user_turns(ctx.turns)
    if not user_t:
        return JudgeResult(
            judge_name="UserFrustration", score=0.0, passed=True,
            rationale="No user turns to evaluate.",
        )

    per_turn_scores: list[float] = []
    total_weighted = 0.0
    all_matches: list[dict] = []

    for turn in user_t:
        text = turn.content.lower()
        turn_score = 0.0
        for severity, patterns in _FRUSTRATION_INDICATORS.items():
            weight = _FRUSTRATION_WEIGHTS[severity]
            for pat in patterns:
                if re.search(pat, text, re.IGNORECASE):
                    turn_score += weight
                    all_matches.append({
                        "turn": turn.turn_num,
                        "severity": severity,
                        "pattern": pat[:40],
                    })
        per_turn_scores.append(min(turn_score, 3.0))  # cap per-turn
        total_weighted += min(turn_score, 3.0)

    n = len(user_t)
    raw_score = total_weighted / (n * 3.0) if n > 0 else 0.0
    score = min(raw_score, 1.0)

    # Escalation detection: is frustration growing?
    escalating = False
    if len(per_turn_scores) >= 3:
        last_half = per_turn_scores[len(per_turn_scores) // 2:]
        first_half = per_turn_scores[:len(per_turn_scores) // 2]
        if sum(last_half) > sum(first_half) * 1.5:
            escalating = True
            score = min(score + 0.15, 1.0)

    # Resolution: last user turn has no frustration
    resolved = per_turn_scores[-1] == 0.0 if per_turn_scores else True

    passed = score <= 0.3  # low frustration

    rationale_parts = []
    if score == 0.0:
        rationale_parts.append("No frustration detected.")
    else:
        rationale_parts.append(
            f"Frustration score: {score:.2f} "
            f"({len(all_matches)} indicator(s) across {n} user turn(s))."
        )
        if escalating:
            rationale_parts.append("Frustration is ESCALATING over the conversation.")
        if resolved:
            rationale_parts.append("Frustration appears resolved in the last turn.")
        else:
            rationale_parts.append("Frustration is UNRESOLVED at conversation end.")

    return JudgeResult(
        judge_name="UserFrustration",
        score=round(score, 3),
        passed=passed,
        rationale=" ".join(rationale_parts),
        details={
            "per_turn_scores": [round(s, 2) for s in per_turn_scores],
            "escalating": escalating,
            "resolved": resolved,
            "matches": all_matches[:20],  # cap for payload size
        },
    )


# ── KnowledgeRetention Judge ─────────────────────────────────────────────────

def _extract_facts(text: str) -> list[str]:
    """Extract key factual claims from text for retention checking.

    Uses sentence splitting + filters for substantive content.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    facts = []
    for s in sentences:
        s = s.strip()
        # Skip very short, question-only, or filler sentences
        if len(s) < 15:
            continue
        if s.endswith("?"):
            continue
        if re.match(r"^(ok|okay|sure|yes|no|thanks|thank you|got it|i see)", s, re.I):
            continue
        facts.append(s.lower())
    return facts


def _fact_overlap(facts_a: list[str], facts_b: list[str]) -> float:
    """Measure overlap between two fact sets using token Jaccard similarity."""
    if not facts_a or not facts_b:
        return 0.0

    def _tokens(text: str) -> set[str]:
        return set(re.findall(r'\b\w{3,}\b', text.lower()))

    a_tokens = set()
    for f in facts_a:
        a_tokens |= _tokens(f)
    b_tokens = set()
    for f in facts_b:
        b_tokens |= _tokens(f)

    if not a_tokens or not b_tokens:
        return 0.0

    intersection = a_tokens & b_tokens
    union = a_tokens | b_tokens
    return len(intersection) / len(union) if union else 0.0


def judge_knowledge_retention(ctx: ConversationContext) -> JudgeResult:
    """Evaluate whether the assistant retains and correctly references
    information provided by the user earlier in the conversation.

    Scoring:
      - Extracts facts stated by user in turns 1..N-2
      - Checks if later assistant responses reference those facts
      - Penalises contradictions (assistant states opposite of user fact)
      - Score = fact_retention_ratio (0-1)
    """
    turns = ctx.turns
    if len(turns) < 4:
        return JudgeResult(
            judge_name="KnowledgeRetention", score=1.0, passed=True,
            rationale="Too few turns to evaluate retention (need >= 4).",
        )

    # Collect facts from user turns (excluding last user turn)
    user_facts: list[str] = []
    assistant_responses: list[str] = []
    last_user_idx = -1
    for i, t in enumerate(turns):
        if t.role == "user":
            last_user_idx = i

    for t in turns:
        if t.role == "user" and t.turn_num < last_user_idx:
            user_facts.extend(_extract_facts(t.content))

    # Collect all assistant responses after the first user fact
    for t in turns:
        if t.role == "assistant":
            assistant_responses.append(t.content)

    if not user_facts:
        return JudgeResult(
            judge_name="KnowledgeRetention", score=1.0, passed=True,
            rationale="No substantial user facts to track retention.",
        )

    # Check which user facts are referenced/retained in assistant responses
    all_assistant_text = " ".join(assistant_responses).lower()
    retained = 0
    forgotten = 0
    fact_details: list[dict] = []

    for fact in user_facts:
        fact_tokens = set(re.findall(r'\b\w{4,}\b', fact))
        if not fact_tokens:
            continue

        # Check token overlap with all assistant text
        overlap = sum(1 for t in fact_tokens if t in all_assistant_text)
        ratio = overlap / len(fact_tokens) if fact_tokens else 0

        if ratio >= 0.4:
            retained += 1
            fact_details.append({"fact": fact[:80], "status": "retained", "overlap": round(ratio, 2)})
        else:
            forgotten += 1
            fact_details.append({"fact": fact[:80], "status": "forgotten", "overlap": round(ratio, 2)})

    total = retained + forgotten
    score = retained / total if total > 0 else 1.0

    # Bonus: Check for later-in-conversation recall
    later_turns = [t for t in turns if t.role == "assistant" and t.turn_num > len(turns) // 2]
    later_text = " ".join(t.content for t in later_turns).lower()
    later_overlap = _fact_overlap(user_facts, [later_text]) if later_text else 1.0

    # Weight: 70% overall, 30% later-turn recall
    final_score = 0.7 * score + 0.3 * later_overlap
    final_score = round(min(final_score, 1.0), 3)

    passed = final_score >= 0.5

    rationale = (
        f"Retained {retained}/{total} user facts (score: {final_score:.2f}). "
        f"Later-turn recall: {later_overlap:.2f}."
    )

    return JudgeResult(
        judge_name="KnowledgeRetention",
        score=final_score,
        passed=passed,
        rationale=rationale,
        details={
            "retained_count": retained,
            "forgotten_count": forgotten,
            "total_facts": total,
            "later_recall": round(later_overlap, 3),
            "facts": fact_details[:20],
        },
    )


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
#  4. ToolCall Evaluators
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ToolCall:
    """A single tool/function call made by a model."""
    name: str
    arguments: dict
    result: str | None = None


@dataclass
class ToolCallExpectation:
    """Expected tool call for evaluation."""
    name: str
    arguments: dict | None = None         # None = don't check args
    required: bool = True                  # Must be called?
    order: int | None = None              # Expected position (None = any)


def _arg_similarity(expected: dict, actual: dict) -> float:
    """Compute argument similarity between expected and actual tool call args.

    Checks key presence and value equality.  Returns 0-1.
    """
    if not expected:
        return 1.0
    if not actual:
        return 0.0

    total_keys = set(expected.keys()) | set(actual.keys())
    if not total_keys:
        return 1.0

    matches = 0
    for key in expected:
        if key in actual:
            if _values_match(expected[key], actual[key]):
                matches += 1
            else:
                matches += 0.5  # partial: key exists, value differs

    return matches / len(expected)


def _values_match(expected: Any, actual: Any) -> bool:
    """Flexible value comparison for tool arguments."""
    if expected == actual:
        return True
    # String comparison: case-insensitive
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower().strip() == actual.lower().strip()
    # Numeric comparison: within 1% tolerance
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if expected == 0:
            return actual == 0
        return abs(expected - actual) / max(abs(expected), 1e-9) < 0.01
    return False


def judge_tool_call_correctness(
    actual_calls: list[ToolCall],
    expected_calls: list[ToolCallExpectation],
) -> JudgeResult:
    """Evaluate whether the model made the correct tool calls.

    Checks:
      - Were all required tools called?
      - Were the arguments correct?
      - Were any unexpected tools called?
      - Was the call order correct (if order specified)?

    Returns score 0-1 and detailed breakdown.
    """
    if not expected_calls:
        # No expectations: pass if no calls made, warn if calls made
        if not actual_calls:
            return JudgeResult(
                judge_name="ToolCallCorrectness", score=1.0, passed=True,
                rationale="No tool calls expected and none made.",
            )
        return JudgeResult(
            judge_name="ToolCallCorrectness", score=0.7, passed=True,
            rationale=f"No expectations defined but {len(actual_calls)} call(s) made.",
            details={"unexpected_calls": [c.name for c in actual_calls]},
        )

    matched: list[dict] = []
    unmatched_expected: list[str] = []
    unmatched_actual = list(actual_calls)  # copy for consumption

    for exp in expected_calls:
        best_match = None
        best_score = 0.0
        best_idx = -1

        for i, act in enumerate(unmatched_actual):
            if act.name.lower() == exp.name.lower():
                if exp.arguments is not None:
                    sim = _arg_similarity(exp.arguments, act.arguments)
                else:
                    sim = 1.0
                if sim > best_score:
                    best_score = sim
                    best_match = act
                    best_idx = i

        if best_match and best_score > 0.3:
            matched.append({
                "expected": exp.name,
                "actual": best_match.name,
                "arg_similarity": round(best_score, 2),
                "required": exp.required,
            })
            unmatched_actual.pop(best_idx)
        elif exp.required:
            unmatched_expected.append(exp.name)

    # Score components
    required_expected = [e for e in expected_calls if e.required]
    required_found = len([m for m in matched if m["required"]])
    required_total = len(required_expected)

    # Correctness: required coverage * argument accuracy
    coverage = required_found / required_total if required_total > 0 else 1.0
    avg_arg_sim = (
        sum(m["arg_similarity"] for m in matched) / len(matched)
        if matched else 0.0
    )
    unexpected_penalty = min(len(unmatched_actual) * 0.1, 0.3)

    score = max(0.0, coverage * 0.6 + avg_arg_sim * 0.4 - unexpected_penalty)
    score = round(min(score, 1.0), 3)

    # Order check
    order_correct = True
    if any(e.order is not None for e in expected_calls):
        ordered_expects = sorted(
            [e for e in expected_calls if e.order is not None],
            key=lambda e: e.order,  # type: ignore[arg-type]
        )
        actual_names = [c.name.lower() for c in actual_calls]
        ordered_names = [e.name.lower() for e in ordered_expects]
        last_idx = -1
        for on in ordered_names:
            try:
                idx = actual_names.index(on, last_idx + 1)
                last_idx = idx
            except ValueError:
                order_correct = False
                break
        if not order_correct:
            score = max(0.0, score - 0.1)

    passed = score >= 0.6

    rationale_parts = [
        f"Matched {len(matched)}/{len(expected_calls)} expected call(s).",
    ]
    if unmatched_expected:
        rationale_parts.append(f"Missing required: {', '.join(unmatched_expected)}.")
    if unmatched_actual:
        rationale_parts.append(
            f"Unexpected calls: {', '.join(c.name for c in unmatched_actual)}."
        )
    if not order_correct:
        rationale_parts.append("Call order does not match expectations.")

    return JudgeResult(
        judge_name="ToolCallCorrectness",
        score=score,
        passed=passed,
        rationale=" ".join(rationale_parts),
        details={
            "matched": matched,
            "missing_required": unmatched_expected,
            "unexpected": [c.name for c in unmatched_actual],
            "order_correct": order_correct,
            "coverage": round(coverage, 3),
            "avg_arg_similarity": round(avg_arg_sim, 3),
        },
    )


def judge_tool_call_efficiency(
    actual_calls: list[ToolCall],
    min_expected: int = 1,
    max_expected: int | None = None,
) -> JudgeResult:
    """Evaluate tool call efficiency — no redundant or duplicate calls.

    Checks:
      - Duplicate calls (same function + same args)
      - Call count within expected bounds
      - Calls with no result (wasted calls)
    """
    n = len(actual_calls)

    # Detect duplicates
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for c in actual_calls:
        key = f"{c.name}:{json.dumps(c.arguments, sort_keys=True)}"
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            duplicates.append(c.name)

    # Count wasted (no result)
    wasted = sum(1 for c in actual_calls if c.result is None)

    # Scoring
    dup_penalty = min(len(duplicates) * 0.15, 0.5)
    waste_penalty = min(wasted * 0.1, 0.3)

    bounds_ok = n >= min_expected and (max_expected is None or n <= max_expected)
    bounds_penalty = 0.0 if bounds_ok else 0.2

    score = max(0.0, 1.0 - dup_penalty - waste_penalty - bounds_penalty)
    score = round(score, 3)
    passed = score >= 0.7

    parts = [f"{n} tool call(s) made."]
    if duplicates:
        parts.append(f"Duplicates: {', '.join(duplicates)}.")
    if not bounds_ok:
        parts.append(f"Expected {min_expected}-{max_expected or '∞'} calls.")
    if wasted:
        parts.append(f"{wasted} call(s) returned no result.")

    return JudgeResult(
        judge_name="ToolCallEfficiency",
        score=score,
        passed=passed,
        rationale=" ".join(parts),
        details={
            "total_calls": n,
            "duplicates": duplicates,
            "wasted_calls": wasted,
            "bounds_ok": bounds_ok,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  5. Local Model Gateway
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class GatewayRoute:
    """A routing configuration for the local model gateway."""
    name: str
    strategy: str     # round_robin, weighted, fallback, ab_test
    models: list[str]  # model paths or names
    config: dict = field(default_factory=dict)
    enabled: bool = True


class LocalModelGateway:
    """Routes inference requests across local GGUF models.

    Strategies:
      - round_robin: Cycle through models evenly
      - weighted:    Route by weight percentages (A/B testing)
      - fallback:    Try first model, fall back to next on failure
      - ab_test:     Split traffic by percentage for comparison

    All routing decisions are logged to SQLite for analysis.
    """

    def __init__(self) -> None:
        self._routes: dict[str, GatewayRoute] = {}
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()
        self._load_routes()

    def _load_routes(self) -> None:
        """Load routes from database."""
        try:
            with _db_lock:
                conn = _get_db()
                try:
                    rows = conn.execute(
                        "SELECT * FROM gateway_routes WHERE enabled = 1"
                    ).fetchall()
                    for r in rows:
                        self._routes[r["name"]] = GatewayRoute(
                            name=r["name"],
                            strategy=r["strategy"],
                            models=json.loads(r["models"]),
                            config=json.loads(r["config"]),
                            enabled=bool(r["enabled"]),
                        )
                finally:
                    conn.close()
        except Exception:
            pass  # DB might not be initialized yet

    def add_route(self, route: GatewayRoute) -> None:
        """Add or update a gateway route."""
        with _db_lock:
            conn = _get_db()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO gateway_routes (name, strategy, models, config, enabled) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (route.name, route.strategy, json.dumps(route.models),
                     json.dumps(route.config), int(route.enabled)),
                )
                conn.commit()
            finally:
                conn.close()
        with self._lock:
            self._routes[route.name] = route

    def remove_route(self, name: str) -> bool:
        """Remove a gateway route."""
        with _db_lock:
            conn = _get_db()
            try:
                cur = conn.execute("DELETE FROM gateway_routes WHERE name = ?", (name,))
                conn.commit()
                removed = cur.rowcount > 0
            finally:
                conn.close()
        with self._lock:
            self._routes.pop(name, None)
            self._counters.pop(name, None)
        return removed

    def resolve(self, route_name: str) -> str | None:
        """Resolve a route to a specific model path based on strategy.

        Returns the model path to use for this request.
        """
        with self._lock:
            route = self._routes.get(route_name)
            if not route or not route.models:
                return None

            if route.strategy == "round_robin":
                idx = self._counters.get(route_name, 0)
                model = route.models[idx % len(route.models)]
                self._counters[route_name] = idx + 1
                return model

            elif route.strategy == "weighted":
                weights = route.config.get("weights", [1.0] * len(route.models))
                # Ensure weights list matches models list length
                while len(weights) < len(route.models):
                    weights.append(1.0)
                total = sum(weights)
                if total <= 0:
                    return route.models[0]
                r = random.random() * total
                cumulative = 0.0
                for model, w in zip(route.models, weights):
                    cumulative += w
                    if r <= cumulative:
                        return model
                return route.models[-1]

            elif route.strategy == "fallback":
                # Return first model; caller should try next on failure
                return route.models[0]

            elif route.strategy == "ab_test":
                # Similar to weighted but uses explicit percentages
                split = route.config.get("split", [50, 50])
                while len(split) < len(route.models):
                    split.append(0)
                total = sum(split)
                if total <= 0:
                    return route.models[0]
                r = random.random() * total
                cumulative = 0.0
                for model, pct in zip(route.models, split):
                    cumulative += pct
                    if r <= cumulative:
                        return model
                return route.models[-1]

            return route.models[0]

    def get_fallback_chain(self, route_name: str) -> list[str]:
        """Get the full fallback chain for a route."""
        with self._lock:
            route = self._routes.get(route_name)
            if not route:
                return []
            return list(route.models)

    def log_request(
        self,
        route_name: str,
        model_used: str,
        prompt: str,
        latency_ms: float = 0,
        tokens: int = 0,
        success: bool = True,
    ) -> None:
        """Log a gateway routing decision for analytics."""
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        try:
            with _db_lock:
                conn = _get_db()
                try:
                    conn.execute(
                        "INSERT INTO gateway_log (route_name, model_used, prompt_hash, "
                        "latency_ms, tokens, success) VALUES (?, ?, ?, ?, ?, ?)",
                        (route_name, model_used, prompt_hash, latency_ms, tokens,
                         int(success)),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass  # Don't let logging errors break inference

    def get_route_stats(self, route_name: str) -> dict:
        """Get statistics for a gateway route."""
        with _db_lock:
            conn = _get_db()
            try:
                rows = conn.execute(
                    "SELECT model_used, COUNT(*) as count, "
                    "AVG(latency_ms) as avg_latency, "
                    "SUM(tokens) as total_tokens, "
                    "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes, "
                    "SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures "
                    "FROM gateway_log WHERE route_name = ? GROUP BY model_used",
                    (route_name,),
                ).fetchall()

                if not rows:
                    return {"route_name": route_name, "models": {}, "total_requests": 0}

                models: dict[str, dict] = {}
                total = 0
                for r in rows:
                    count = r["count"]
                    total += count
                    models[r["model_used"]] = {
                        "requests": count,
                        "avg_latency_ms": round(r["avg_latency"] or 0, 1),
                        "total_tokens": r["total_tokens"] or 0,
                        "success_rate": round(
                            r["successes"] / count if count > 0 else 0, 3
                        ),
                    }

                return {
                    "route_name": route_name,
                    "models": models,
                    "total_requests": total,
                }
            finally:
                conn.close()

    def list_routes(self) -> list[dict]:
        """List all gateway routes."""
        with self._lock:
            return [asdict(r) for r in self._routes.values()]


# Module-level gateway instance
_gateway = LocalModelGateway()


def get_gateway() -> LocalModelGateway:
    """Get the module-level gateway instance."""
    return _gateway
