"""
ZenAIos — Chat Server Integration Tests
=========================================
Hits the live /__chat endpoint and verifies:
  1. HTTP response codes and body shape
  2. Rows written to the SQLite `actions` table (chatSend + chatReply)

Prerequisites:
  - Server running on localhost:8787  (python server.py)
  - OR set ZENAI_PORT env var for a different port

Run with:
  python -m pytest tests/test_chat_server.py -v
  python -m pytest tests/test_chat_server.py -v --tb=short

The LLM engine is NOT required — tests that need a real reply are skipped
when the engine is unavailable (503 is the expected no-LLM response).
"""

import json
import os
import sqlite3
import time
import urllib.request
import urllib.error
import urllib.parse
import pytest

# ─── Config ───────────────────────────────────────────────────────────────────

PORT = int(os.environ.get("ZENAI_PORT", 8787))
BASE = f"http://localhost:{PORT}"
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "zenai_activity.db")


# ─── Helpers ──────────────────────────────────────────────────────────────────


def post_chat(message, badge="TEST-MONKEY", timeout=10):
    """POST to /__chat, return (status_code, response_dict)."""
    body = json.dumps({"message": message, "badge": badge}).encode()
    req = urllib.request.Request(
        f"{BASE}/__chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def latest_actions(n=2):
    """Return the n most recent rows from the actions table."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def server_is_up():
    try:
        urllib.request.urlopen(f"{BASE}/login.html", timeout=3)
        return True
    except Exception:
        return False


# ─── Skip marker ──────────────────────────────────────────────────────────────

requires_server = pytest.mark.skipif(
    not server_is_up(), reason=f"ZenAIos server not running on port {PORT}"
)


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestChatEndpointShape:
    @requires_server
    def test_empty_message_returns_400(self):
        code, body = post_chat("")
        assert code == 400
        assert "error" in body
        assert body["error"] == "empty message"

    @requires_server
    def test_whitespace_only_returns_400(self):
        code, body = post_chat("   ")
        assert code == 400
        assert "error" in body

    @requires_server
    def test_valid_message_returns_200_or_503(self):
        """200 = LLM available, 503 = LLM not loaded — both are valid."""
        code, body = post_chat("câte paturi sunt libere?")
        assert code in (200, 503), f"Unexpected status {code}: {body}"
        assert isinstance(body, dict)
        assert "reply" in body or "error" in body

    @requires_server
    def test_200_reply_is_non_empty_string(self):
        code, body = post_chat("câte paturi?")
        if code == 503:
            pytest.skip("LLM engine not available")
        assert code == 200
        assert isinstance(body.get("reply"), str)
        assert len(body["reply"]) > 0

    @requires_server
    def test_503_error_is_string(self):
        code, body = post_chat("salut")
        if code == 200:
            pytest.skip("LLM engine is available — 503 path not exercised")
        assert code == 503
        assert isinstance(body.get("error"), str)
        assert "unavailable" in body["error"].lower() or "AI engine" in body["error"]

    @requires_server
    def test_missing_message_key_returns_400(self):
        """Payload with no 'message' key should be treated as empty."""
        body_bytes = json.dumps({"badge": "TEST"}).encode()
        req = urllib.request.Request(
            f"{BASE}/__chat",
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code, _resp_body = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            code, _resp_body = e.code, json.loads(e.read())
        assert code == 400

    @requires_server
    def test_reply_length_reasonable(self):
        code, body = post_chat("doctor de gardă")
        if code == 503:
            pytest.skip("LLM engine not available")
        reply = body.get("reply", "")
        # Sanity: not absurdly long (max_tokens=512 ≈ ~2000 chars)
        assert len(reply) < 5000, f"Reply suspiciously long: {len(reply)} chars"


class TestChatDatabaseWrites:
    @requires_server
    def test_chatSend_row_written_on_valid_message(self):
        ts_before = time.time()
        message = f"test-db-write-{int(ts_before)}"
        code, _ = post_chat(message, badge="DB-TEST-01")

        rows = latest_actions(10)
        send_rows = [
            r
            for r in rows
            if r["action"] == "chatSend"
            and r["badge"] == "DB-TEST-01"
            and r["timestamp"] >= ts_before
        ]
        assert len(send_rows) >= 1, "No chatSend row found in DB"

        detail = json.loads(send_rows[0]["detail"])
        assert message[:200] in detail.get("message", ""), (
            f"Message not in detail: {detail}"
        )

    @requires_server
    def test_chatReply_row_written_when_llm_available(self):
        ts_before = time.time()
        code, body = post_chat("alerte active?", badge="DB-TEST-02")
        if code == 503:
            pytest.skip("LLM engine not available — chatReply row not expected")

        rows = latest_actions(10)
        reply_rows = [
            r
            for r in rows
            if r["action"] == "chatReply"
            and r["badge"] == "DB-TEST-02"
            and r["timestamp"] >= ts_before
        ]
        assert len(reply_rows) >= 1, "No chatReply row found in DB"

        detail = json.loads(reply_rows[0]["detail"])
        assert "reply" in detail

    @requires_server
    def test_empty_message_writes_no_db_row(self):
        ts_before = time.time()
        post_chat("", badge="DB-EMPTY-TEST")
        time.sleep(0.1)

        rows = latest_actions(10)
        bad_rows = [
            r
            for r in rows
            if r["badge"] == "DB-EMPTY-TEST" and r["timestamp"] >= ts_before
        ]
        assert len(bad_rows) == 0, (
            f"Empty message should write nothing to DB, found: {bad_rows}"
        )

    @requires_server
    def test_badge_stored_correctly(self):
        badge = "BADGE-XYZ-999"
        ts_before = time.time()
        post_chat("paturi", badge=badge)

        rows = latest_actions(10)
        match = [r for r in rows if r["badge"] == badge and r["timestamp"] >= ts_before]
        assert len(match) >= 1
        assert match[0]["badge"] == badge


class TestChatMonkeyEndpoint:
    """Fuzz the endpoint with wild inputs — should never 500."""

    FUZZ_INPUTS = [
        # injections
        "' OR '1'='1",
        '"; DROP TABLE actions; --',
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        # unicode extremes
        "\u0000\u0001\u0002",
        "\ufffd\ufffe\uffff",
        "\u202e reversed",
        "中文日本語한국어",
        # size extremes
        "a" * 2000,  # exactly at server truncation limit
        "a" * 3000,  # over limit — server truncates to 2000
        # mixed media tokens
        "[voice message]",
        "[voice message] paturi alerte doctor raport salut",
        "data:image/png;base64," + "A" * 500,
        # whitespace only (→ 400)
        "\t\n\r ",
        # numeric strings
        "0",
        "-1",
        "3.14",
        "NaN",
        "Infinity",
        # real keywords
        "paturi libere",
        "alerte urgente",
        "personal de gardă",
        "raport kpi zilnic",
        "salut buna ziua",
    ]

    @requires_server
    def test_fuzz_inputs_never_500(self):
        """Every input should return 200, 400, or 503 — never 500."""
        for inp in self.FUZZ_INPUTS:
            code, body = post_chat(inp, badge="FUZZ")
            assert code in (200, 400, 503), (
                f"Unexpected {code} for input {repr(inp[:80])}: {body}"
            )

    @requires_server
    def test_oversized_message_truncated_not_crashed(self):
        """3000-char message: server truncates to 2000, returns 200 or 503."""
        code, body = post_chat("x" * 3000)
        assert code in (200, 503)

    @requires_server
    def test_concurrent_requests_all_succeed(self):
        """10 sequential rapid requests — no 500s, no DB corruption."""
        import threading

        results = []
        errors = []
        lock = threading.Lock()

        def fire(i):
            try:
                code, body = post_chat(f"concurrent test {i}", badge=f"CONC-{i:02d}")
                with lock:
                    results.append((i, code, body))
            except Exception as exc:
                with lock:
                    errors.append((i, str(exc)))

        threads = [threading.Thread(target=fire, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 10, f"Only {len(results)}/10 threads completed"
        for i, code, body in results:
            assert code in (200, 400, 503), f"Thread {i} got unexpected {code}: {body}"
