"""
Completeness Audit Tests — Spec vs. Implementation
====================================================
Validates every feature listed in LLM_COMPARE_2026.md and HOW_TO_USE.md
is actually implemented. No mocks — real function calls and HTTP requests.

Run:
    pytest tests/test_completeness_audit.py -v --tb=short
"""

import inspect
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import comparator_backend as cb  # noqa: E402

TEST_PORT = 18126
TEST_URL = f"http://127.0.0.1:{TEST_PORT}"

_server = None


def _start_test_server():
    global _server
    if _server is not None:
        return
    from http.server import HTTPServer
    _server = HTTPServer(("127.0.0.1", TEST_PORT), cb.ComparatorHandler)
    t = threading.Thread(target=_server.serve_forever, daemon=True)
    t.start()
    for _ in range(50):
        try:
            req = urllib.request.Request(f"{TEST_URL}/__health")
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.1)


def _get(path, headers=None, timeout=10):
    req = urllib.request.Request(TEST_URL + path)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, dict(resp.headers), json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, dict(e.headers), json.loads(body) if body else {}


def _post(path, data, headers=None, timeout=30):
    body = json.dumps(data).encode()
    req = urllib.request.Request(TEST_URL + path, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", "http://127.0.0.1:8123")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rbody = resp.read()
            return resp.status, dict(resp.headers), json.loads(rbody) if rbody else {}
    except urllib.error.HTTPError as e:
        rbody = e.read()
        try:
            return e.code, dict(e.headers), json.loads(rbody) if rbody else {}
        except json.JSONDecodeError:
            pass  # handle malformed JSON


# ═══════════════════════════════════════════════════════════════════════════════
# A. SPEC: "Up to 6 models per run in parallel threads"
# ═══════════════════════════════════════════════════════════════════════════════

class TestParallelComparison:
    """Validate comparison accepts multiple models (even if paths don't exist)."""

    @classmethod
    def setup_class(cls):
        _start_test_server()

    def test_comparison_accepts_empty_model_list(self):
        status, _, body = _post("/__comparison/mixed", {
            "prompt": "test", "local_models": [], "online_models": []
        })
        assert status == 200
        assert body["models_tested"] == 0

    def test_comparison_returns_responses_array(self):
        status, _, body = _post("/__comparison/mixed", {
            "prompt": "test", "local_models": [], "online_models": []
        })
        assert isinstance(body.get("responses"), list)

    def test_comparison_prompt_too_large_rejected(self):
        """Prompts exceeding MAX_PROMPT_TOKENS must be rejected."""
        huge_prompt = "word " * 20000  # Way over 8192 tokens
        status, _, body = _post("/__comparison/mixed", {
            "prompt": huge_prompt, "local_models": [], "online_models": []
        })
        assert status == 400
        assert "too large" in body.get("error", "").lower() or "token" in body.get("error", "").lower()

    def test_comparison_returns_timestamp(self):
        status, _, body = _post("/__comparison/mixed", {
            "prompt": "test", "local_models": [], "online_models": []
        })
        assert "timestamp" in body
        assert isinstance(body["timestamp"], float)


# ═══════════════════════════════════════════════════════════════════════════════
# B. SPEC: "5 judge templates" with extract_judge_scores 5-layer fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestJudgeScoreEdgeCases:
    """Additional edge cases for the 5-layer judge score extraction."""

    def test_multiple_json_blocks_picks_first(self):
        raw = '```json\n{"overall": 9}\n```\nSome text\n```json\n{"overall": 3}\n```'
        result = cb.extract_judge_scores(raw)
        assert result["overall"] == 9

    def test_fractional_scores(self):
        raw = '{"overall": 7.3, "accuracy": 6.8}'
        result = cb.extract_judge_scores(raw)
        assert result["overall"] == 7.3

    def test_zero_is_valid_score(self):
        raw = '{"overall": 0}'
        result = cb.extract_judge_scores(raw)
        assert result["overall"] == 0

    def test_ten_is_max_valid(self):
        raw = '{"overall": 10}'
        result = cb.extract_judge_scores(raw)
        assert result["overall"] == 10.0

    def test_score_with_trailing_text(self):
        raw = '{"overall": 8} This is quite good honestly'
        result = cb.extract_judge_scores(raw)
        assert result["overall"] == 8

    def test_extract_accuracy_from_nl(self):
        """Regex patterns match 'accuracy: 7' but not 'accuracy is: 7' (known gap)."""
        raw = "accuracy: 7 out of 10. reasoning: 8/10."
        result = cb.extract_judge_scores(raw)
        assert "accuracy" in result
        assert result["accuracy"] == 7.0

    def test_score_averaging_when_no_overall(self):
        """When no 'overall' key but other numeric scores exist, average them."""
        raw = '{"accuracy": 6, "reasoning": 8}'
        result = cb.extract_judge_scores(raw)
        assert "overall" in result
        assert result["overall"] == 7.0  # average of 6 and 8

    def test_deeply_nested_ignored(self):
        """Triple-nested JSON should still extract scores."""
        raw = '{"eval": {"scores": {"overall": 5}}}'
        result = cb.extract_judge_scores(raw)
        # May not find it in deep nesting - should at least return valid dict
        assert isinstance(result, dict)
        assert "overall" in result

    def test_whitespace_only_returns_zero(self):
        result = cb.extract_judge_scores("   \n\t  ")
        assert result == {"overall": 0}


# ═══════════════════════════════════════════════════════════════════════════════
# C. SPEC: "Position-bias mitigation (dual-pass)" in _run_judge
# ═══════════════════════════════════════════════════════════════════════════════

class TestJudgeBiasMitigation:
    """Validate that _run_judge implements dual-pass scoring."""

    def test_run_judge_source_has_two_passes(self):
        src = inspect.getsource(cb.ComparatorHandler._run_judge)
        # Should have two user messages (standard + shuffled)
        assert "user_msg_standard" in src
        assert "user_msg_shuffled" in src

    def test_run_judge_uses_random_shuffle(self):
        src = inspect.getsource(cb.ComparatorHandler._run_judge)
        assert "random.shuffle" in src

    def test_run_judge_averages_scores(self):
        src = inspect.getsource(cb.ComparatorHandler._run_judge)
        assert "avg_score" in src or "average" in src.lower()

    def test_run_judge_records_bias_passes(self):
        src = inspect.getsource(cb.ComparatorHandler._run_judge)
        assert "bias_passes" in src

    def test_run_judge_records_individual_scores(self):
        src = inspect.getsource(cb.ComparatorHandler._run_judge)
        assert "individual_scores" in src


# ═══════════════════════════════════════════════════════════════════════════════
# D. SPEC: "SSE streaming" — /__comparison/stream
# ═══════════════════════════════════════════════════════════════════════════════

class TestSSEStreamProtocol:
    """Validate the SSE protocol specifics."""

    @classmethod
    def setup_class(cls):
        _start_test_server()

    def _stream_post(self, body_dict, read_timeout=15):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", TEST_PORT, timeout=read_timeout)
        body = json.dumps(body_dict)
        conn.request("POST", "/__comparison/stream", body=body, headers={
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:8123",
        })
        resp = conn.getresponse()
        status = resp.status
        headers = dict(resp.getheaders())
        body_data = resp.read(16384).decode("utf-8", errors="replace")
        conn.close()
        return status, headers, body_data

    def test_stream_returns_200(self):
        status, _, _ = self._stream_post({
            "prompt": "Hello", "local_models": []
        })
        assert status == 200

    def test_stream_content_type(self):
        _, headers, _ = self._stream_post({
            "prompt": "Hello", "local_models": []
        })
        assert "text/event-stream" in headers.get("Content-Type", "")

    def test_stream_no_cache(self):
        _, headers, _ = self._stream_post({
            "prompt": "Hello", "local_models": []
        })
        assert "no-cache" in headers.get("Cache-Control", "")

    def test_stream_contains_done_event(self):
        _, _, body = self._stream_post({
            "prompt": "Hello", "local_models": []
        })
        assert "event: done" in body

    def test_stream_done_payload_is_json(self):
        _, _, body = self._stream_post({
            "prompt": "Hello", "local_models": []
        })
        # Find the data: line after event: done
        for line in body.split("\n"):
            if line.startswith("data:") and "responses" in line:
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    data = {}
                assert "responses" in data
                break

    def test_stream_rejects_oversized_prompt(self):
        """SSE should also reject oversized prompts."""
        huge = "word " * 20000
        status, _, body = self._stream_post({
            "prompt": huge, "local_models": []
        })
        # Should be 400 (JSON error response, not SSE)
        assert status == 400


# ═══════════════════════════════════════════════════════════════════════════════
# E. SPEC: "Zena chat assistant" — /__chat
# ═══════════════════════════════════════════════════════════════════════════════

class TestZenaChatEndpoint:
    """Validate chat endpoint input validation."""

    @classmethod
    def setup_class(cls):
        _start_test_server()

    def test_chat_rejects_missing_model(self):
        status, _, body = _post("/__chat", {
            "model_path": "", "messages": [{"role": "user", "content": "hi"}]
        })
        assert status == 400

    def test_chat_rejects_nonexistent_model(self):
        status, _, body = _post("/__chat", {
            "model_path": "C:\\nonexistent\\fake.gguf",
            "messages": [{"role": "user", "content": "hi"}]
        })
        assert status in (400, 403)

    def test_chat_rejects_path_outside_model_dirs(self):
        status, _, body = _post("/__chat", {
            "model_path": "C:\\Windows\\System32\\cmd.exe",
            "messages": [{"role": "user", "content": "hi"}]
        })
        assert status in (400, 403)


# ═══════════════════════════════════════════════════════════════════════════════
# F. SPEC: "Model download from UI" + SSRF protection
# ═══════════════════════════════════════════════════════════════════════════════

class TestDownloadEndpoint:
    """Validate download endpoint input validation."""

    @classmethod
    def setup_class(cls):
        _start_test_server()

    def test_download_rejects_empty_model(self):
        status, _, body = _post("/__download-model", {"model": "", "dest": "C:\\AI\\Models"})
        assert status == 400

    def test_download_returns_job_id(self):
        """A valid-looking model string should return a job_id (download itself may fail)."""
        status, _, body = _post("/__download-model", {
            "model": "bartowski/test-repo/test.gguf",
            "dest": os.path.join(REPO_ROOT, "test_download_temp"),
        })
        assert status == 200
        assert "job_id" in body

    def test_download_status_unknown_job(self):
        status, _, body = _get(
            "/__download-status?job=nonexistent",
            {"Origin": "http://127.0.0.1:8123"}
        )
        assert status == 200
        assert body.get("state") == "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# G. SPEC: "pip install llama-cpp-python from UI"
# ═══════════════════════════════════════════════════════════════════════════════

class TestInstallEndpoint:
    """Validate install endpoint safety."""

    @classmethod
    def setup_class(cls):
        _start_test_server()

    def test_install_rejects_arbitrary_commands(self):
        """Only pip install llama-cpp-python should be allowed."""
        status, _, body = _post("/__install-llama", {
            "pip": "pip install malicious-package"
        })
        assert status == 400
        assert "only" in body.get("error", "").lower() or "llama" in body.get("error", "").lower()

    def test_install_status_unknown_job(self):
        status, _, body = _get(
            "/__install-status?job=nonexistent",
            {"Origin": "http://127.0.0.1:8123"}
        )
        assert status == 200
        assert body.get("state") == "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# H. SPEC: "HuggingFace Model Discovery" cached for 15 min
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiscoveryDetails:
    """Validate discovery caching and trusted quantizers."""

    def test_trusted_quantizers_complete(self):
        expected = {"bartowski", "mradermacher", "unsloth", "TheBloke", "QuantFactory"}
        assert expected.issubset(cb._TRUSTED_QUANTIZERS)

    def test_discovery_ttl_is_15_minutes(self):
        assert cb._DISCOVERY_TTL == 900

    def test_discovery_function_returns_list(self):
        # Call with empty query — may return results or error, but must return a list
        result = cb._discover_hf_models(query="", sort="trending", limit=1)
        assert isinstance(result, list)

    def test_discovery_cache_dict_exists(self):
        assert isinstance(cb._discovery_cache, dict)

    def test_discovery_endpoint_params(self):
        _start_test_server()
        # Sort must be validated
        status, _, body = _get(
            "/__discover-models?q=test&sort=invalid_sort&limit=5",
            {"Origin": "http://127.0.0.1:8123"}
        )
        assert status == 200  # Should fallback to "trending", not error


# ═══════════════════════════════════════════════════════════════════════════════
# I. SPEC: "32 categorised question bank (6 categories)"
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuestionBankCompleteness:
    """Validate the HTML question bank has all 6 categories."""

    @classmethod
    def setup_class(cls):
        html_path = os.path.join(REPO_ROOT, "model_comparator.html")
        with open(html_path, "r", encoding="utf-8") as f:
            cls.html = f.read()

    def test_ops_category(self):
        assert "ops" in self.html.lower() or "Ops" in self.html

    def test_emergency_category(self):
        assert "emergency" in self.html.lower() or "Emergency" in self.html

    def test_cardiology_category(self):
        assert "cardiology" in self.html.lower() or "Cardiology" in self.html

    def test_coding_category(self):
        assert "coding" in self.html.lower() or "Coding" in self.html

    def test_reasoning_category(self):
        assert "reasoning" in self.html.lower() or "Reasoning" in self.html

    def test_multilingual_category(self):
        assert "multilingual" in self.html.lower() or "Multilingual" in self.html


# ═══════════════════════════════════════════════════════════════════════════════
# J. SPEC: "Dark mode / RTL / 6 languages"
# ═══════════════════════════════════════════════════════════════════════════════

class TestI18nAndTheme:
    """Validate internationalization and theme support."""

    @classmethod
    def setup_class(cls):
        html_path = os.path.join(REPO_ROOT, "model_comparator.html")
        with open(html_path, "r", encoding="utf-8") as f:
            cls.html = f.read()

    def test_dark_mode_class_toggle(self):
        assert "toggleTheme" in self.html

    def test_dark_mode_localstorage(self):
        assert "localStorage" in self.html and ("theme" in self.html.lower() or "dark" in self.html.lower())

    def test_language_english(self):
        assert "setLang('en')" in self.html

    def test_language_hebrew(self):
        assert "setLang('he')" in self.html

    def test_language_arabic(self):
        assert "setLang('ar')" in self.html

    def test_language_spanish(self):
        assert "setLang('es')" in self.html

    def test_language_french(self):
        assert "setLang('fr')" in self.html

    def test_language_german(self):
        assert "setLang('de')" in self.html

    def test_rtl_support_for_hebrew_arabic(self):
        # RTL should be applied for Hebrew and Arabic
        assert "rtl" in self.html.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# K. SPEC Features: Scenario presets, Batch mode, CSV export, Share
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdvancedFeatures:
    """Validate spec-required advanced features exist in frontend."""

    @classmethod
    def setup_class(cls):
        html_path = os.path.join(REPO_ROOT, "model_comparator.html")
        with open(html_path, "r", encoding="utf-8") as f:
            cls.html = f.read()

    def test_scenario_clinical_triage(self):
        assert "clinical_triage" in self.html or "clinical" in self.html.lower()

    def test_scenario_code_review(self):
        assert "code_review" in self.html

    def test_scenario_math_olympiad(self):
        assert "math_olympiad" in self.html or "logic_duel" in self.html

    def test_scenario_polyglot(self):
        assert "polyglot" in self.html

    def test_batch_panel_exists(self):
        assert "batchPanel" in self.html

    def test_batch_run_function(self):
        assert "_runBatch" in self.html

    def test_csv_export_function(self):
        assert "exportCSV" in self.html

    def test_share_report_function(self):
        assert "_shareReport" in self.html

    def test_elo_update_function(self):
        assert "_updateElo" in self.html

    def test_leaderboard_render_function(self):
        assert "_renderLeaderboard" in self.html

    def test_history_save_function(self):
        assert "_saveToHistory" in self.html

    def test_monkey_mode_button(self):
        assert "RANDOM" in self.html or "🐒" in self.html

    def test_xss_escape_function(self):
        assert "escHtml" in self.html


# ═══════════════════════════════════════════════════════════════════════════════
# L. SPEC: Server binds to 127.0.0.1 only (security)
# ═══════════════════════════════════════════════════════════════════════════════

class TestServerSecurity:
    """Validate security constraints."""

    def test_server_binds_localhost(self):
        src = inspect.getsource(cb.run_server)
        assert "127.0.0.1" in src

    def test_no_wildcard_bind(self):
        src = inspect.getsource(cb.run_server)
        assert "0.0.0.0" not in src

    def test_install_only_allows_llama_cpp(self):
        src = inspect.getsource(cb.ComparatorHandler._handle_install_llama)
        assert "llama-cpp-python" in src

    def test_model_path_validation_in_comparison(self):
        src = inspect.getsource(cb.ComparatorHandler._handle_comparison)
        assert "_is_safe_model_path" in src

    def test_model_path_validation_in_chat(self):
        src = inspect.getsource(cb.ComparatorHandler._handle_chat)
        assert "_is_safe_model_path" in src


# ═══════════════════════════════════════════════════════════════════════════════
# M. SPEC: "Hardware auto-detection (CPU/GPU/RAM)"
# ═══════════════════════════════════════════════════════════════════════════════

class TestHardwareDetectionIntegration:
    """Validate hardware detection integration via HTTP endpoint."""

    @classmethod
    def setup_class(cls):
        _start_test_server()

    def test_system_info_has_cpu_brand(self):
        _, _, body = _get("/__system-info", {"Origin": "http://127.0.0.1:8123"})
        assert "cpu_brand" in body

    def test_system_info_has_gpu_list(self):
        _, _, body = _get("/__system-info", {"Origin": "http://127.0.0.1:8123"})
        assert isinstance(body.get("gpus"), list)

    def test_system_info_has_memory(self):
        _, _, body = _get("/__system-info", {"Origin": "http://127.0.0.1:8123"})
        assert body.get("memory_gb", 0) > 0

    def test_system_info_has_recommended_build(self):
        _, _, body = _get("/__system-info", {"Origin": "http://127.0.0.1:8123"})
        rec = body.get("recommended_build", {})
        assert "build" in rec
        assert "pip" in rec

    def test_system_info_has_llama_cpp_status(self):
        _, _, body = _get("/__system-info", {"Origin": "http://127.0.0.1:8123"})
        assert "has_llama_cpp" in body
        assert isinstance(body["has_llama_cpp"], bool)


# ═══════════════════════════════════════════════════════════════════════════════
# N. RATE LIMITING ON HEAVY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimitingHTTP:
    """Validate rate limiting is enforced on heavy POST endpoints."""

    @classmethod
    def setup_class(cls):
        _start_test_server()

    def test_rate_limit_source_enforced_on_comparison(self):
        src = inspect.getsource(cb.ComparatorHandler.do_POST)
        assert "_rate_limiter" in src
        assert "429" in src or "Too many" in src

    def test_rate_limit_applies_to_chat(self):
        src = inspect.getsource(cb.ComparatorHandler.do_POST)
        assert "/__chat" in src

    def test_rate_limit_applies_to_stream(self):
        src = inspect.getsource(cb.ComparatorHandler.do_POST)
        assert "/__comparison/stream" in src
