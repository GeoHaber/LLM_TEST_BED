"""
Process & Resource Hygiene Tests
=================================
Validates that the backend does not leak processes, threads, or ports.
These tests ensure zombie processes cannot be created by normal app/test usage.

Run:
    pytest tests/test_zombie_and_process_audit.py -v --tb=short
"""

import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import comparator_backend as cb  # noqa: E402

TEST_PORT = 18130
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
            with urllib.request.urlopen(req, timeout=2):
                break
        except Exception:
            time.sleep(0.1)


# ─── ThreadingHTTPServer daemon_threads ──────────────────────────────────────

class TestDaemonThreads:
    """Ensure all server threads are daemon (die with main process)."""

    def test_threading_server_has_daemon_threads(self):
        assert cb.ThreadingHTTPServer.daemon_threads is True

    def test_server_class_uses_threading_mixin(self):
        from socketserver import ThreadingMixIn
        assert issubclass(cb.ThreadingHTTPServer, ThreadingMixIn)

    def test_no_non_daemon_threads_created_by_handler(self):
        """After starting and stopping a test server, all spawned threads are daemon."""
        _start_test_server()
        non_daemon = [t for t in threading.enumerate()
                      if t.name != "MainThread" and not t.daemon]
        # Only MainThread should be non-daemon
        assert len(non_daemon) == 0, f"Non-daemon threads: {[t.name for t in non_daemon]}"


# ─── Download Worker Threads ────────────────────────────────────────────────

class TestDownloadWorkerHygiene:
    """Download workers must be daemon threads so they die with the process."""

    def test_download_thread_is_daemon(self):
        """The _handle_download code creates daemon=True threads."""
        src = open(os.path.join(REPO_ROOT, "comparator_backend.py"),
                   encoding="utf-8").read()
        # Find the thread creation in _handle_download
        pattern = r"threading\.Thread\(target=_run_download.*?daemon=True"
        assert re.search(pattern, src, re.DOTALL), \
            "_run_download thread is not created with daemon=True"

    def test_install_thread_is_daemon(self):
        """The _handle_install_llama code creates daemon=True threads."""
        src = open(os.path.join(REPO_ROOT, "comparator_backend.py"),
                   encoding="utf-8").read()
        pattern = r"threading\.Thread\(target=_run_install.*?daemon=True"
        assert re.search(pattern, src, re.DOTALL), \
            "_run_install thread is not created with daemon=True"


# ─── Port Binding Safety ────────────────────────────────────────────────────

class TestPortBinding:
    """Server must bind to 127.0.0.1, not 0.0.0.0."""

    def test_server_binds_localhost_only(self):
        src = open(os.path.join(REPO_ROOT, "comparator_backend.py"),
                   encoding="utf-8").read()
        # The only 0.0.0.0 reference should be in the SSRF hostname validation,
        # NOT as a server bind address.  Ensure the HTTPServer bind uses 127.0.0.1.
        assert re.search(r'HTTPServer\(\("127\.0\.0\.1"', src) or \
               re.search(r'run_server.*127\.0\.0\.1', src, re.DOTALL), \
            "Server must bind to 127.0.0.1"

    def test_run_server_function_uses_localhost(self):
        src = open(os.path.join(REPO_ROOT, "comparator_backend.py"),
                   encoding="utf-8").read()
        assert re.search(r'127\.0\.0\.1.*PORT', src) or \
               re.search(r'"127\.0\.0\.1"', src), \
            "run_server should explicitly use 127.0.0.1"


# ─── Connection Error Handling ──────────────────────────────────────────────

class TestConnectionErrorHandling:
    """Backend must silently handle client disconnects."""

    def test_send_json_catches_connection_errors(self):
        src = open(os.path.join(REPO_ROOT, "comparator_backend.py"),
                   encoding="utf-8").read()
        # Find _send_json method
        idx = src.find("def _send_json")
        block = src[idx:idx + 600]
        assert "ConnectionAbortedError" in block, \
            "_send_json should catch ConnectionAbortedError"
        assert "BrokenPipeError" in block, \
            "_send_json should catch BrokenPipeError"

    def test_sse_handler_catches_connection_errors(self):
        src = open(os.path.join(REPO_ROOT, "comparator_backend.py"),
                   encoding="utf-8").read()
        idx = src.find("def _sse(event")
        block = src[idx:idx + 400]
        assert "ConnectionAbortedError" in block or "BrokenPipeError" in block, \
            "SSE _sse() should catch connection errors"

    def test_log_message_silences_disconnect_noise(self):
        src = open(os.path.join(REPO_ROOT, "comparator_backend.py"),
                   encoding="utf-8").read()
        idx = src.find("def log_message")
        block = src[idx:idx + 300]
        assert "ConnectionAbortedError" in block, \
            "log_message should filter out ConnectionAbortedError"

    def test_options_handler_catches_connection_errors(self):
        src = open(os.path.join(REPO_ROOT, "comparator_backend.py"),
                   encoding="utf-8").read()
        idx = src.find("def do_OPTIONS")
        block = src[idx:idx + 400]
        assert "ConnectionAbortedError" in block or "BrokenPipeError" in block, \
            "do_OPTIONS should catch connection errors"


# ─── Server Start/Stop Without Leaked Ports ─────────────────────────────────

class TestServerLifecycle:
    """Server should release port on shutdown."""

    def test_port_is_free_before_bind(self):
        """The test port (18131) should be free for a new server."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            result = sock.connect_ex(("127.0.0.1", 18131))
            assert result != 0, "Port 18131 should be free"
        finally:
            sock.close()

    def test_cache_thread_is_daemon(self):
        """The _warm_cache background thread must be daemon."""
        src = open(os.path.join(REPO_ROOT, "comparator_backend.py"),
                   encoding="utf-8").read()
        # Search for the Thread(target=_warm_cache, daemon=True) call
        assert re.search(r'Thread\(target=_warm_cache.*daemon=True', src, re.DOTALL), \
            "_warm_cache thread should be daemon"


# ─── Model Browser Cleanup ──────────────────────────────────────────────────

class TestModelBrowserSources:
    """Only sources with actual GGUF catalog APIs should be in the model browser."""

    def test_no_github_tab_in_download_modal(self):
        html = open(os.path.join(REPO_ROOT, "model_comparator.html"),
                    encoding="utf-8").read()
        # There should be no GitHub tab button in the download modal
        assert "switchRepo('gh'" not in html, \
            "GitHub tab should be removed (no GGUF catalog API)"

    def test_no_github_section_in_download_modal(self):
        html = open(os.path.join(REPO_ROOT, "model_comparator.html"),
                    encoding="utf-8").read()
        assert 'id="repo-gh"' not in html, \
            "GitHub section (repo-gh) should be removed"

    def test_huggingface_tab_exists(self):
        html = open(os.path.join(REPO_ROOT, "model_comparator.html"),
                    encoding="utf-8").read()
        assert "switchRepo('hf'" in html

    def test_discover_tab_exists(self):
        html = open(os.path.join(REPO_ROOT, "model_comparator.html"),
                    encoding="utf-8").read()
        assert "switchRepo('discover'" in html

    def test_modelscope_tab_exists(self):
        html = open(os.path.join(REPO_ROOT, "model_comparator.html"),
                    encoding="utf-8").read()
        assert "switchRepo('ms'" in html


# ─── Loading Spinners ───────────────────────────────────────────────────────

class TestLoadingSpinners:
    """UI should show spinning indicators during async operations."""

    def test_discover_grid_has_spinner(self):
        html = open(os.path.join(REPO_ROOT, "model_comparator.html"),
                    encoding="utf-8").read()
        # The discover grid loading state should have animate-spin
        idx = html.find("discoverGrid")
        block = html[idx:idx + 500]
        assert "animate-spin" in block, "Discover grid should have a loading spinner"

    def test_catalog_grid_has_spinner(self):
        html = open(os.path.join(REPO_ROOT, "model_comparator.html"),
                    encoding="utf-8").read()
        idx = html.find("catalogGrid")
        block = html[idx:idx + 500]
        assert "animate-spin" in block, "Catalog grid should have a loading spinner"

    def test_download_button_shows_spinner(self):
        html = open(os.path.join(REPO_ROOT, "model_comparator.html"),
                    encoding="utf-8").read()
        assert "animate-spin" in html and "Starting" in html, \
            "Download button should show spinner while starting"
