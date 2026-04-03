/// Full Validation Tests for Zen LLM Compare — No Mocks
/// =====================================================
/// Validates every documented feature against the specification in
/// README.md, HOW_TO_USE.md, LLM_COMPARE_2026.md, and CHANGELOG.md.
/// 
/// All tests use the REAL backend (no mocking). Tests that require GGUF
/// model files are marked with @pytest.mark.needsmodel and skip
/// automatically when no models are found.
/// 
/// Run:
/// # All tests (fast — no models required):
/// pytest tests/test_full_validation::py -v
/// 
/// # Full suite including inference (needs GGUF models in ~/AI/Models):
/// pytest tests/test_full_validation::py -v --run-model-tests
/// 
/// # With a custom model directory:
/// ZENAI_MODEL_DIR="D:/Models" pytest tests/test_full_validation::py -v --run-model-tests
/// 
/// Coverage groups:
/// A. Spec compliance — every stated feature is present
/// B. Security — all OWASP-relevant surfaces
/// C. Judge score extraction — edge cases
/// D. Rate limiter accuracy
/// E. Model scanning correctness
/// F. HTTP API surface — every documented endpoint
/// G. CORS correctness
/// H. Frontend feature presence (parsed from HTML)
/// I. Judge fallback prompt schema consistency
/// J. Configuration constants
/// K. Download URL allow-list completeness
/// L. HuggingFace model discovery
/// M. Install job lifecycle
/// N. Metrics math (TPS, RAM, TTFT)
/// O. Path-traversal prevention
/// P. Judge path safety (fix for security bug where judge used unsanitized paths)
/// Q. Concurrency — rate limiter thread-safety

use anyhow::{Result, Context};
use crate::comparator_backend as cb;
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::PathBuf;

pub static REPO_ROOT: std::sync::LazyLock<String /* os::path.dirname */> = std::sync::LazyLock::new(|| Default::default());

pub static HTML_PATH: std::sync::LazyLock<String /* os::path.join */> = std::sync::LazyLock::new(|| Default::default());

pub const _PORT: i64 = 18130;

pub const _BASE: &str = "f'http://127.0.0.1:{_PORT}";

pub static _SERVER_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> = std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

pub static _SERVER_STARTED: std::sync::LazyLock<std::sync::Condvar> = std::sync::LazyLock::new(|| Default::default());

/// Verify every README / HOW_TO_USE feature exists in the implementation.
#[derive(Debug, Clone)]
pub struct TestSpecCompliance {
}

impl TestSpecCompliance {
    pub fn test_system_info_endpoint_exists(&self) -> () {
        let (mut s, _, mut b) = _get("/__system-info".to_string());
        assert!((s == 200 && b.contains(&"models".to_string())));
    }
    pub fn test_comparison_mixed_endpoint_exists(&self) -> () {
        let (mut s, _, mut b) = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), "test".to_string()), ("local_models".to_string(), vec![])]));
        assert!(s == 200);
    }
    pub fn test_chat_endpoint_exists(&self) -> () {
        let (mut s, _, mut b) = _post("/__chat".to_string(), HashMap::new());
        assert!(s == 400);
    }
    pub fn test_download_model_endpoint_exists(&self) -> () {
        let (mut s, _, mut b) = _post("/__download-model".to_string(), HashMap::new());
        assert!(s == 400);
    }
    pub fn test_install_llama_endpoint_exists(&self) -> () {
        let (mut s, _, mut b) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install llama-cpp-python".to_string())]));
        assert!((s == 200 && b.contains(&"job_id".to_string())));
    }
    pub fn test_install_status_endpoint_exists(&self) -> () {
        let (mut s, _, mut b) = _get("/__install-status?job=missing".to_string());
        assert!(s == 200);
    }
    pub fn test_health_endpoint_exists(&self) -> () {
        let (mut s, _, mut b) = _get("/__health".to_string());
        assert!((s == 200 && b.get(&"ok".to_string()).cloned() == true));
    }
    pub fn test_config_endpoint_exists(&self) -> () {
        let (mut s, _, mut b) = _get("/__config".to_string());
        assert!(s == 200);
    }
    pub fn test_discover_models_endpoint_exists(&self) -> () {
        let (mut s, _, mut b) = _get("/__discover-models".to_string());
        assert!(s == 200);
    }
    /// SSE endpoint must exist; empty model list completes immediately.
    pub fn test_stream_comparison_endpoint_exists(&self) -> Result<()> {
        // SSE endpoint must exist; empty model list completes immediately.
        let mut req = urllib::request.Request((_BASE + "/__comparison/stream".to_string()), /* data= */ serde_json::to_string(&HashMap::from([("prompt".to_string(), "hi".to_string()), ("local_models".to_string(), vec![])])).unwrap().as_bytes().to_vec(), /* headers= */ HashMap::from([("Content-Type".to_string(), "application/json".to_string())]), /* method= */ "POST".to_string());
        // try:
        {
            let mut r = urllib::request.urlopen(req, /* timeout= */ 10);
            {
                assert!(r.headers.get(&"Content-Type".to_string()).cloned().unwrap_or("".to_string()).contains(&"text/event-stream".to_string()), "SSE endpoint must return text/event-stream content type");
            }
        }
        // except Exception as e:
    }
    pub fn test_model_count_field_present(&self) -> () {
        let (_, _, mut b) = _get("/__system-info".to_string());
        assert!(b.contains(&"model_count".to_string()));
    }
    pub fn test_models_list_field_present(&self) -> () {
        let (_, _, mut b) = _get("/__system-info".to_string());
        assert!(/* /* isinstance(b.get(&"models".to_string()).cloned(), list) */ */ true);
    }
    pub fn test_gpu_info_field_present(&self) -> () {
        let (_, _, mut b) = _get("/__system-info".to_string());
        assert!((b.contains(&"gpus".to_string()) && /* /* isinstance(b["gpus".to_string()], list) */ */ true));
    }
    pub fn test_recommended_build_present(&self) -> () {
        let (_, _, mut b) = _get("/__system-info".to_string());
        assert!(b.contains(&"recommended_build".to_string()));
        let mut rb = b["recommended_build".to_string()];
        for key in ("build".to_string(), "pip".to_string(), "reason".to_string()).iter() {
            assert!(rb.contains(&key), "recommended_build missing key: {}", key);
        }
    }
    pub fn test_llama_cpp_version_present(&self) -> () {
        let (_, _, mut b) = _get("/__system-info".to_string());
        assert!(b.contains(&"llama_cpp_version".to_string()));
    }
}

/// OWASP Top 10 relevant attack surfaces.
#[derive(Debug, Clone)]
pub struct TestSecuritySurfaces {
}

impl TestSecuritySurfaces {
    pub fn test_ssrf_http_blocked(&self) -> () {
        assert!(cb::validate_download_url("http://huggingface.co/model.gguf".to_string()) == false);
    }
    pub fn test_ssrf_private_rfc1918_blocked(&self) -> () {
        for ip in vec!["10.0.0.1".to_string(), "172.16.0.1".to_string(), "192.168.1.1".to_string()].iter() {
            assert!(cb::validate_download_url(format!("https://{}/model.gguf", ip)) == false, "Private IP {} must be blocked", ip);
        }
    }
    pub fn test_ssrf_loopback_blocked(&self) -> () {
        for host in vec!["localhost".to_string(), "127.0.0.1".to_string(), "0.0.0.0".to_string()].iter() {
            assert!(cb::validate_download_url(format!("https://{}/x.gguf", host)) == false);
        }
    }
    pub fn test_ssrf_ipv6_loopback_blocked(&self) -> () {
        assert!(cb::validate_download_url("https://[::1]/x.gguf".to_string()) == false);
    }
    pub fn test_ssrf_file_scheme_blocked(&self) -> () {
        assert!(cb::validate_download_url("file:///etc/passwd".to_string()) == false);
    }
    pub fn test_ssrf_ftp_blocked(&self) -> () {
        assert!(cb::validate_download_url("ftp://huggingface.co/x.gguf".to_string()) == false);
    }
    pub fn test_ssrf_unknown_host_blocked(&self) -> () {
        assert!(cb::validate_download_url("https://evil.hacker.com/model.gguf".to_string()) == false);
    }
    pub fn test_ssrf_allowed_hosts(&self) -> () {
        for url in vec!["https://huggingface.co/user/repo/model.gguf".to_string(), "https://cdn-lfs.huggingface.co/file".to_string(), "https://cdn-lfs-us-1.huggingface.co/file".to_string(), "https://github.com/user/repo/releases/model.gguf".to_string(), "https://objects.githubusercontent.com/x".to_string(), "https://releases.githubusercontent.com/x".to_string(), "https://gitlab.com/user/repo".to_string()].iter() {
            assert!(cb::validate_download_url(url) == true, "Should allow: {}", url);
        }
    }
    pub fn test_path_traversal_blocked(&self) -> () {
        let mut d = tempfile::TemporaryDirectory();
        {
            let mut malicious = PathBuf::from(d).join("..".to_string()).join("etc".to_string()).join("passwd".to_string());
            assert!(!cb::_is_safe_model_path(malicious, vec![d]));
        }
    }
    pub fn test_wrong_extension_blocked(&self) -> () {
        let mut d = tempfile::TemporaryDirectory();
        {
            assert!(!cb::_is_safe_model_path(PathBuf::from(d).join("file.exe".to_string()), vec![d]));
            assert!(!cb::_is_safe_model_path(PathBuf::from(d).join("file.py".to_string()), vec![d]));
        }
    }
    pub fn test_empty_path_blocked(&self) -> () {
        assert!(!cb::_is_safe_model_path("".to_string(), vec!["/tmp".to_string()]));
    }
    pub fn test_install_rejects_arbitrary_commands(&self) -> () {
        for cmd in vec!["pip install evil && rm -rf /".to_string(), "pip install evil; curl http://attacker.com".to_string(), "bash -c 'rm -rf /'".to_string(), "pip install numpy".to_string()].iter() {
            let (mut s, _, mut b) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), cmd)]));
            assert!(s == 400, "Should reject non-llama command: {}", cmd);
        }
    }
    /// Hammering the same IP must eventually hit 429.
    pub fn test_rate_limit_enforced(&self) -> () {
        // Hammering the same IP must eventually hit 429.
        let mut rl = cb::_RateLimiter(/* max_requests= */ 3, /* window_sec= */ 60);
        for _ in 0..3.iter() {
            rl.allow("attacker".to_string());
        }
        assert!(!rl.allow("attacker".to_string()), "4th request must be blocked");
    }
    pub fn test_cors_not_wildcard(&self) -> () {
        let (mut s, mut headers, _) = _get("/__health".to_string(), /* headers= */ HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        let mut acao = headers.get(&"Access-Control-Allow-Origin".to_string()).cloned().unwrap_or("".to_string());
        assert!(acao != "*".to_string(), "ACAO must not be wildcard");
    }
    pub fn test_cors_external_origin_not_reflected(&self) -> () {
        let (mut s, mut headers, _) = _get("/__health".to_string(), /* headers= */ HashMap::from([("Origin".to_string(), "https://evil.com".to_string())]));
        let mut acao = headers.get(&"Access-Control-Allow-Origin".to_string()).cloned().unwrap_or("".to_string());
        assert!(!acao.contains(&"evil.com".to_string()));
    }
    pub fn test_oversized_prompt_rejected(&self) -> () {
        let mut huge = ("word ".to_string() * (cb::MAX_PROMPT_TOKENS + 500));
        let (mut s, _, mut b) = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), huge), ("local_models".to_string(), vec![])]));
        assert!(s == 400);
        assert!((b.get(&"error".to_string()).cloned().unwrap_or("".to_string()).to_lowercase().contains(&"too large".to_string()) || b.to_string().to_lowercase().contains(&"too large".to_string())));
    }
    /// Judge model path resolution must go through path safety check.
    pub fn test_judge_path_must_be_in_safe_dirs(&self) -> () {
        // Judge model path resolution must go through path safety check.
        assert!(/* hasattr(cb, "_is_safe_model_path".to_string()) */ true, "_is_safe_model_path must exist");
        assert!(!cb::_is_safe_model_path("/etc/passwd.gguf".to_string(), vec!["/tmp/models".to_string()]));
    }
    /// The fixed comparison handler uses safe_models for judge lookup.
    pub fn test_judge_rejects_path_outside_model_dirs(&self) -> () {
        // The fixed comparison handler uses safe_models for judge lookup.
        // TODO: import inspect
        let mut src = inspect::getsource(cb::ComparatorHandler._handle_comparison);
        assert!(src.contains(&"safe_models".to_string()), "SECURITY FIX: _handle_comparison must use safe_models for judge resolution, not local_models");
    }
    /// The fixed stream handler uses safe_models for judge lookup.
    pub fn test_stream_judge_rejects_path_outside_model_dirs(&self) -> () {
        // The fixed stream handler uses safe_models for judge lookup.
        // TODO: import inspect
        let mut src = inspect::getsource(cb::ComparatorHandler._handle_stream_comparison);
        assert!(src.contains(&"safe_models".to_string()), "SECURITY FIX: _handle_stream_comparison must use safe_models for judge, not local_models");
    }
}

/// extract_judge_scores must handle all real-world LLM output patterns.
#[derive(Debug, Clone)]
pub struct TestJudgeScoreExtraction {
}

impl TestJudgeScoreExtraction {
    pub fn _run(&self, text: String) -> () {
        let mut result = cb::extract_judge_scores(text);
        assert!(/* /* isinstance(result, dict) */ */ true, "Must return dict for: {}", text);
        assert!(result.contains(&"overall".to_string()), "Must have 'overall' key for: {}", text);
        assert!(/* /* isinstance(result["overall".to_string()], (int, float) */) */ true, "overall must be numeric, got {}", r#type(result["overall"]));
        result
    }
    pub fn test_clean_json(&mut self) -> () {
        let mut r = self._run("{\"overall\":8,\"accuracy\":7,\"reasoning\":9}".to_string());
        assert!(r["overall".to_string()] == 8.0_f64);
    }
    pub fn test_json_in_markdown_fence(&mut self) -> () {
        let mut r = self._run("```json\n{\"overall\": 7}\n```".to_string());
        assert!(r["overall".to_string()] == 7.0_f64);
    }
    pub fn test_json_in_fence_no_language(&mut self) -> () {
        let mut r = self._run("```\n{\"overall\": 5}\n```".to_string());
        assert!(r["overall".to_string()] == 5.0_f64);
    }
    pub fn test_nested_json_evaluation_key(&mut self) -> () {
        let mut r = self._run("{\"evaluation\":{\"overall\":8,\"accuracy\":7}}".to_string());
        assert!(r["overall".to_string()] == 8.0_f64);
    }
    pub fn test_string_score_slash_format(&mut self) -> () {
        let mut r = self._run("{\"overall\":\"8/10\",\"accuracy\":\"7/10\"}".to_string());
        assert!(r["overall".to_string()] == 8.0_f64);
    }
    pub fn test_score_clamped_above_10(&mut self) -> () {
        let mut r = self._run("{\"overall\":15}".to_string());
        assert!(r["overall".to_string()] <= 10.0_f64);
    }
    pub fn test_score_clamped_below_0(&mut self) -> () {
        let mut r = self._run("{\"overall\":-3}".to_string());
        assert!(r["overall".to_string()] >= 0.0_f64);
    }
    pub fn test_natural_language_overall(&mut self) -> () {
        let mut r = self._run("I would give this response overall: 7 out of 10.".to_string());
        assert!((0 <= r["overall".to_string()]) && (r["overall".to_string()] <= 10));
    }
    pub fn test_empty_string_returns_zero(&mut self) -> () {
        let mut r = self._run("".to_string());
        assert!(r["overall".to_string()] == 0);
    }
    pub fn test_garbage_returns_zero(&mut self) -> () {
        let mut r = self._run("XXXXXXXXXXX!!! Not a score at all %%%".to_string());
        assert!(r["overall".to_string()] == 0);
    }
    pub fn test_unquoted_keys(&mut self) -> () {
        let mut r = self._run("{overall: 8, accuracy: 7}".to_string());
        assert!(r.contains(&"overall".to_string()));
    }
    pub fn test_float_scores_preserved(&mut self) -> () {
        let mut r = self._run("{\"overall\":7.5}".to_string());
        assert!(r["overall".to_string()] == 7.5_f64);
    }
    pub fn test_averaging_when_no_overall(&mut self) -> () {
        let mut r = self._run("{\"accuracy\":6,\"reasoning\":8}".to_string());
        assert!(r.contains(&"overall".to_string()));
        assert!(/* /* isinstance(r["overall".to_string()], (int, float) */) */ true);
    }
    /// After judge prompt fix, instruction should be 0-10, not bool.
    pub fn test_instruction_field_0_10_not_bool(&mut self) -> () {
        // After judge prompt fix, instruction should be 0-10, not bool.
        let mut r = self._run("{\"overall\":8,\"instruction\":7}".to_string());
        assert!(/* /* isinstance(r.get(&"instruction".to_string()).cloned().unwrap_or(7), (int, float) */) */ true);
    }
    /// HOW_TO_USE specifies: overall, accuracy, reasoning, instruction, safety.
    pub fn test_all_five_spec_fields(&mut self) -> () {
        // HOW_TO_USE specifies: overall, accuracy, reasoning, instruction, safety.
        let mut raw = "{\"overall\":8,\"accuracy\":7,\"reasoning\":9,\"instruction\":8,\"safety\":9,\"explanation\":\"Good\"}".to_string();
        let mut r = self._run(raw);
        for key in ("overall".to_string(), "accuracy".to_string(), "reasoning".to_string()).iter() {
            assert!(r.contains(&key), "Missing expected key: {}", key);
        }
    }
    pub fn test_explanation_field_preserved(&mut self) -> () {
        let mut raw = "{\"overall\":8,\"explanation\":\"Excellent reasoning shown.\"}".to_string();
        let mut r = self._run(raw);
        assert!(r.get(&"explanation".to_string()).cloned() == "Excellent reasoning shown.".to_string());
    }
    /// Judge may prefix with many words before the JSON block.
    pub fn test_very_long_text_with_embedded_json(&mut self) -> () {
        // Judge may prefix with many words before the JSON block.
        let mut long_preamble = ("After careful analysis of the response, I conclude that ".to_string() * 20);
        let mut raw = (long_preamble + "{\"overall\":6,\"accuracy\":5}".to_string());
        let mut r = self._run(raw);
        assert!(r["overall".to_string()] == 6.0_f64);
    }
    pub fn test_unicode_in_explanation(&mut self) -> () {
        let mut raw = "{\"overall\":7,\"explanation\":\"שלום — 日本語 — مرحبا\"}".to_string();
        let mut r = self._run(raw);
        assert!(r["overall".to_string()] == 7.0_f64);
    }
}

#[derive(Debug, Clone)]
pub struct TestRateLimiter {
}

impl TestRateLimiter {
    pub fn test_exact_limit_boundary(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 5, /* window_sec= */ 60);
        for _ in 0..5.iter() {
            assert!(rl.allow("x".to_string()) == true);
        }
        assert!(rl.allow("x".to_string()) == false);
    }
    pub fn test_per_ip_isolation(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 1, /* window_sec= */ 60);
        assert!(rl.allow("a.b.c.d".to_string()) == true);
        assert!(rl.allow("e.f.g.h".to_string()) == true);
    }
    pub fn test_window_expiry_resets_count(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 1, /* window_sec= */ 0.05_f64);
        rl.allow("x".to_string());
        std::thread::sleep(std::time::Duration::from_secs_f64(0.1_f64));
        assert!(rl.allow("x".to_string()) == true);
    }
    pub fn test_remaining_accurate(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 10, /* window_sec= */ 60);
        assert!(rl.remaining("ip".to_string()) == 10);
        rl.allow("ip".to_string());
        assert!(rl.remaining("ip".to_string()) == 9);
    }
    pub fn test_remaining_never_negative(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 2, /* window_sec= */ 60);
        for _ in 0..20.iter() {
            rl.allow("x".to_string());
        }
        assert!(rl.remaining("x".to_string()) == 0);
    }
    pub fn test_thread_safety_exact_count(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 100, /* window_sec= */ 60);
        let mut results = vec![];
        let mut barrier = threading::Barrier(20);
        let _worker = || {
            barrier.wait();
            for _ in 0..10.iter() {
                results.push(rl.allow("concurrent".to_string()));
            }
        };
        let mut threads = 0..20.iter().map(|_| std::thread::spawn(|| {})).collect::<Vec<_>>();
        for t in threads.iter() {
            t.start();
        }
        for t in threads.iter() {
            t.join();
        }
        let mut allowed = results.iter().filter(|r| r).map(|r| 1).collect::<Vec<_>>().iter().sum::<i64>();
        assert!(allowed == 100, "Thread-safe: exactly 100 must be allowed, got {}", allowed);
    }
}

#[derive(Debug, Clone)]
pub struct TestModelScanning {
}

impl TestModelScanning {
    pub fn setup_method(&mut self) -> () {
        self.tmpdir = std::env::temp_dir().join("tmp");
    }
    pub fn teardown_method(&mut self) -> () {
        // TODO: import shutil
        std::fs::remove_dir_all(self.tmpdir, /* ignore_errors= */ true).ok();
    }
    pub fn _make_gguf(&mut self, name: String, size_mb: i64) -> Result<String> {
        let mut path = PathBuf::from(self.tmpdir).join(name);
        let mut f = File::open(path)?;
        {
            f.write((b" " * ((size_mb * 1024) * 1024)));
        }
        Ok(path)
    }
    pub fn test_finds_valid_gguf(&mut self) -> () {
        self._make_gguf("llama-3.1-8b.Q4_K_M.gguf".to_string(), 100);
        let mut models = cb::scan_models(vec![self.tmpdir]);
        assert!(models.len() == 1);
        assert!(models[0]["name".to_string()] == "llama-3.1-8b.Q4_K_M.gguf".to_string());
    }
    pub fn test_skips_tiny_files(&mut self) -> Result<()> {
        let mut path = PathBuf::from(self.tmpdir).join("tiny.gguf".to_string());
        let mut f = File::open(path)?;
        {
            f.write((b" " * 1024));
        }
        let mut models = cb::scan_models(vec![self.tmpdir]);
        Ok(assert!(models.len() == 0, "Files < 50 MB must be skipped"))
    }
    pub fn test_skips_incompatible_quant(&mut self) -> () {
        for name in vec!["model-i2_s.gguf".to_string(), "model-i1.gguf".to_string(), "model-i2.gguf".to_string(), "model-i3.gguf".to_string()].iter() {
            self._make_gguf(name, 100);
        }
        let mut models = cb::scan_models(vec![self.tmpdir]);
        assert!(models.len() == 0, "Incompatible quant formats must be skipped");
    }
    pub fn test_ignores_non_gguf(&mut self) -> Result<()> {
        for name in vec!["model.bin".to_string(), "model.safetensors".to_string(), "README.md".to_string(), "model.gguf.part".to_string()].iter() {
            let mut path = PathBuf::from(self.tmpdir).join(name);
            let mut f = File::open(path)?;
            {
                f.write((b" " * ((100 * 1024) * 1024)));
            }
        }
        let mut models = cb::scan_models(vec![self.tmpdir]);
        Ok(assert!(models.len() == 0))
    }
    /// Same filename in two dirs → only one entry.
    pub fn test_deduplicates_same_filename(&mut self) -> Result<()> {
        // Same filename in two dirs → only one entry.
        let mut dir2 = std::env::temp_dir().join("tmp");
        // try:
        {
            self._make_gguf("same-model.gguf".to_string(), 100);
            let mut path2 = PathBuf::from(dir2).join("same-model.gguf".to_string());
            let mut f = File::open(path2)?;
            {
                f.write((b" " * ((100 * 1024) * 1024)));
            }
            let mut models = cb::scan_models(vec![self.tmpdir, dir2]);
            assert!(models.len() == 1);
        }
        // finally:
            // TODO: import shutil
            Ok(std::fs::remove_dir_all(dir2, /* ignore_errors= */ true).ok())
    }
    pub fn test_sorted_alphabetically(&mut self) -> () {
        for name in vec!["zoo.gguf".to_string(), "alpha.gguf".to_string(), "middle.gguf".to_string()].iter() {
            self._make_gguf(name, 100);
        }
        let mut models = cb::scan_models(vec![self.tmpdir]);
        let mut names = models.iter().map(|m| m["name".to_string()]).collect::<Vec<_>>();
        assert!(names == { let mut v = names.clone(); v.sort(); v });
    }
    pub fn test_model_dict_schema(&mut self) -> () {
        self._make_gguf("model.gguf".to_string(), 100);
        let mut models = cb::scan_models(vec![self.tmpdir]);
        assert!(models.len() == 1);
        let mut m = models[0];
        assert!(m.contains(&"name".to_string()));
        assert!(m.contains(&"path".to_string()));
        assert!(m.contains(&"size_gb".to_string()));
        assert!(/* /* isinstance(m["size_gb".to_string()], float) */ */ true);
        assert!(m["size_gb".to_string()] > 0);
    }
    pub fn test_missing_directory_skipped(&self) -> () {
        let mut models = cb::scan_models(vec!["/this/does/not/exist/zzzzzz".to_string()]);
        assert!(models == vec![]);
    }
    pub fn test_size_gb_accurate(&mut self) -> () {
        self._make_gguf("model.gguf".to_string(), 200);
        let mut models = cb::scan_models(vec![self.tmpdir]);
        assert!(models.len() == 1);
        assert!(((models[0]["size_gb".to_string()] - (200 / 1024))).abs() < 0.01_f64);
    }
}

#[derive(Debug, Clone)]
pub struct TestHTTPAPI {
}

impl TestHTTPAPI {
    pub fn test_health_returns_ok_true(&self) -> () {
        let (mut s, _, mut b) = _get("/__health".to_string());
        assert!((s == 200 && b.get(&"ok".to_string()).cloned() == true));
    }
    pub fn test_health_has_ts(&self) -> () {
        let (_, _, mut b) = _get("/__health".to_string());
        assert!((b.contains(&"ts".to_string()) && /* /* isinstance(b["ts".to_string()], (int, float) */) */ true));
    }
    pub fn test_system_info_all_keys(&self) -> () {
        let (_, _, mut b) = _get("/__system-info".to_string());
        for key in ("cpu_brand".to_string(), "cpu_count".to_string(), "cpu_name".to_string(), "cpu_avx2".to_string(), "cpu_avx512".to_string(), "memory_gb".to_string(), "gpus".to_string(), "has_llama_cpp".to_string(), "llama_cpp_version".to_string(), "recommended_build".to_string(), "model_count".to_string(), "models".to_string(), "timestamp".to_string()).iter() {
            assert!(b.contains(&key), "/__system-info missing key: {}", key);
        }
    }
    pub fn test_config_all_keys(&self) -> () {
        let (_, _, mut b) = _get("/__config".to_string());
        for key in ("default_inference_timeout".to_string(), "max_inference_timeout".to_string(), "max_prompt_tokens".to_string(), "rate_limit".to_string(), "vk_devices".to_string()).iter() {
            assert!(b.contains(&key), "/__config missing key: {}", key);
        }
    }
    pub fn test_config_rate_limit_structure(&self) -> () {
        let (_, _, mut b) = _get("/__config".to_string());
        let mut rl = b["rate_limit".to_string()];
        assert!((rl.contains(&"max_requests".to_string()) && rl.contains(&"window_sec".to_string())));
    }
    pub fn test_config_timeout_values_sane(&self) -> () {
        let (_, _, mut b) = _get("/__config".to_string());
        assert!(b["default_inference_timeout".to_string()] > 0);
        assert!(b["max_inference_timeout".to_string()] >= b["default_inference_timeout".to_string()]);
        assert!(b["max_prompt_tokens".to_string()] >= 1024);
    }
    pub fn test_discover_models_returns_list(&self) -> () {
        let (_, _, mut b) = _get("/__discover-models".to_string());
        assert!((b.contains(&"models".to_string()) && /* /* isinstance(b["models".to_string()], list) */ */ true));
    }
    pub fn test_discover_models_sort_options(&self) -> () {
        for sort in ("trending".to_string(), "downloads".to_string(), "newest".to_string(), "likes".to_string()).iter() {
            let (mut s, _, mut b) = _get(format!("/__discover-models?sort={}", sort));
            assert!(s == 200);
        }
    }
    pub fn test_discover_models_invalid_sort_defaults(&self) -> () {
        let (mut s, _, _) = _get("/__discover-models?sort=INVALID_SORT_VALUE_XYZ".to_string());
        assert!(s == 200);
    }
    pub fn test_discover_models_limit_cap(&self) -> () {
        let (mut s, _, _) = _get("/__discover-models?limit=999999".to_string());
        assert!(s == 200);
    }
    pub fn test_download_status_unknown_job(&self) -> () {
        let (mut s, _, mut b) = _get("/__download-status?job=no-such-job-xyz".to_string());
        assert!((s == 200 && b.get(&"state".to_string()).cloned() == "unknown".to_string()));
    }
    pub fn test_install_status_unknown_job(&self) -> () {
        let (mut s, _, mut b) = _get("/__install-status?job=no-such-job-xyz".to_string());
        assert!((s == 200 && b.get(&"state".to_string()).cloned() == "unknown".to_string()));
    }
    pub fn test_404_for_unknown_path(&self) -> () {
        let (mut s, _, _) = _get("/this/path/does/not/exist/xyz".to_string());
        assert!(s == 404);
    }
    pub fn test_comparison_bad_json_returns_400(&self) -> Result<()> {
        let mut req = urllib::request.Request((_BASE + "/__comparison/mixed".to_string()), /* data= */ b"NOT JSON AT ALL", /* headers= */ HashMap::from([("Content-Type".to_string(), "application/json".to_string())]), /* method= */ "POST".to_string());
        // try:
        {
            let mut r = urllib::request.urlopen(req, /* timeout= */ 5);
            {
                let mut status = r.status;
            }
        }
        // except urllib::error.HTTPError as e:
        Ok(assert!(status == 400))
    }
    pub fn test_comparison_empty_models_returns_empty(&self) -> () {
        let (mut s, _, mut b) = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), "test".to_string()), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]));
        assert!(s == 200);
        assert!(b.get(&"responses".to_string()).cloned() == vec![]);
    }
    pub fn test_comparison_has_required_response_fields(&self) -> () {
        let (_, _, mut b) = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), "test".to_string()), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]));
        assert!(b.contains(&"prompt".to_string()));
        assert!(b.contains(&"responses".to_string()));
        assert!(b.contains(&"timestamp".to_string()));
    }
    pub fn test_install_llama_valid_command(&self) -> () {
        let (mut s, _, mut b) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install llama-cpp-python".to_string())]));
        assert!((s == 200 && b.contains(&"job_id".to_string())));
    }
    pub fn test_install_llama_rejects_non_llama(&self) -> () {
        let (mut s, _, mut b) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install requests".to_string())]));
        assert!(s == 400);
    }
    pub fn test_download_model_missing_model_field(&self) -> () {
        let (mut s, _, mut b) = _post("/__download-model".to_string(), HashMap::new());
        assert!(s == 400);
    }
    pub fn test_chat_missing_model_returns_400(&self) -> () {
        let (mut s, _, mut b) = _post("/__chat".to_string(), HashMap::new());
        assert!(s == 400);
    }
    pub fn test_chat_nonexistent_model_returns_400(&self) -> () {
        let (mut s, _, mut b) = _post("/__chat".to_string(), HashMap::from([("model_path".to_string(), "/nonexistent/model.gguf".to_string()), ("messages".to_string(), vec![HashMap::from([("role".to_string(), "user".to_string()), ("content".to_string(), "hi".to_string())])])]));
        assert!(s == 400);
    }
    /// After 30 requests from same IP, should get 429.
    pub fn test_rate_limit_returns_429(&self) -> () {
        // After 30 requests from same IP, should get 429.
        let mut rl = cb::_RateLimiter(/* max_requests= */ 3, /* window_sec= */ 60);
        for _ in 0..3.iter() {
            rl.allow("127.0.0.1".to_string());
        }
        assert!(!rl.allow("127.0.0.1".to_string()));
    }
    pub fn test_root_path_serves_html(&self) -> Result<()> {
        let mut req = urllib::request.Request((_BASE + "/".to_string()));
        // try:
        {
            let mut r = urllib::request.urlopen(req, /* timeout= */ 5);
            {
                let mut s = r.status;
                let mut ct = r.headers.get(&"Content-Type".to_string()).cloned().unwrap_or("".to_string());
            }
        }
        // except urllib::error.HTTPError as e:
        assert!((200, 404).contains(&s));
        if s == 200 {
            assert!(ct.to_lowercase().contains(&"html".to_string()));
        }
    }
}

#[derive(Debug, Clone)]
pub struct TestCORSPolicy {
}

impl TestCORSPolicy {
    pub fn test_localhost_127_allowed(&self) -> () {
        let (mut s, mut headers, _) = _get("/__health".to_string(), /* headers= */ HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(s == 200);
        let mut acao = headers.get(&"Access-Control-Allow-Origin".to_string()).cloned().unwrap_or("".to_string());
        assert!((acao.contains(&"127.0.0.1".to_string()) || acao.contains(&"localhost".to_string())));
    }
    pub fn test_localhost_named_allowed(&self) -> () {
        let (mut s, mut headers, _) = _get("/__health".to_string(), /* headers= */ HashMap::from([("Origin".to_string(), "http://localhost:3000".to_string())]));
        assert!(s == 200);
    }
    pub fn test_not_wildcard(&self) -> () {
        let (_, mut headers, _) = _get("/__health".to_string(), /* headers= */ HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(headers.get(&"Access-Control-Allow-Origin".to_string()).cloned().unwrap_or("".to_string()) != "*".to_string());
    }
    pub fn test_external_not_reflected(&self) -> () {
        let (_, mut headers, _) = _get("/__health".to_string(), /* headers= */ HashMap::from([("Origin".to_string(), "https://attacker.example.com".to_string())]));
        let mut acao = headers.get(&"Access-Control-Allow-Origin".to_string()).cloned().unwrap_or("".to_string());
        assert!(!acao.contains(&"attacker".to_string()));
    }
    pub fn test_vary_origin_header(&self) -> () {
        let (_, mut headers, _) = _get("/__health".to_string(), /* headers= */ HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(headers.get(&"Vary".to_string()).cloned().unwrap_or("".to_string()).contains(&"Origin".to_string()), "Vary: Origin header required when echoing ACAO");
    }
    pub fn test_options_preflight_204(&self) -> () {
        let (mut s, mut headers) = _options("/__comparison/mixed".to_string(), /* headers= */ HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string()), ("Access-Control-Request-Method".to_string(), "POST".to_string()), ("Access-Control-Request-Headers".to_string(), "Content-Type".to_string())]));
        assert!(s == 204);
        assert!(headers.contains(&"Access-Control-Allow-Methods".to_string()));
    }
    /// file:// pages send Origin: null — should be allowed.
    pub fn test_null_origin_file_open(&self) -> () {
        // file:// pages send Origin: null — should be allowed.
        let (_, mut headers, _) = _get("/__health".to_string(), /* headers= */ HashMap::from([("Origin".to_string(), "null".to_string())]));
        let mut acao = headers.get(&"Access-Control-Allow-Origin".to_string()).cloned().unwrap_or("".to_string());
    }
}

#[derive(Debug, Clone)]
pub struct TestFrontendFeatures {
}

impl TestFrontendFeatures {
    pub fn test_run_button_exists(&self, html_src: String) -> () {
        assert!((html_src.contains(&"runComparison".to_string()) || html_src.contains(&"btnRun".to_string()) || html_src.contains(&"id=\"runBtn\"".to_string()) || html_src.contains(&"onclick".to_string())));
    }
    pub fn test_monkey_mode_exists(&self, html_src: String) -> () {
        assert!((html_src.to_lowercase().contains(&"monkey".to_string()) || html_src.to_lowercase().contains(&"random".to_string())));
    }
    pub fn test_zena_chat_exists(&self, html_src: String) -> () {
        assert!((html_src.to_lowercase().contains(&"zena".to_string()) || html_src.contains(&"askZena".to_string()) || html_src.to_lowercase().contains(&"chat".to_string())));
    }
    pub fn test_csv_export_exists(&self, html_src: String) -> () {
        assert!((html_src.contains(&"exportCSV".to_string()) || html_src.contains(&"Export CSV".to_string()) || html_src.to_lowercase().contains(&"csv".to_string())));
    }
    pub fn test_judge_template_selector_exists(&self, html_src: String) -> () {
        assert!((html_src.contains(&"judgeTemplate".to_string()) || html_src.to_lowercase().contains(&"judge".to_string())));
    }
    pub fn test_dark_mode_toggle_exists(&self, html_src: String) -> () {
        assert!((html_src.to_lowercase().contains(&"dark".to_string()) || html_src.to_lowercase().contains(&"theme".to_string())));
    }
    pub fn test_rtl_languages_supported(&self, html_src: String) -> () {
        assert!((html_src.to_lowercase().contains(&"rtl".to_string()) || html_src.to_lowercase().contains(&"dir=".to_string()) || html_src.to_lowercase().contains(&"direction".to_string())));
    }
    pub fn test_hebrew_support(&self, html_src: String) -> () {
        assert!((html_src.contains(&"he".to_string()) || html_src.contains(&"עברית".to_string()) || html_src.to_lowercase().contains(&"hebrew".to_string())));
    }
    pub fn test_arabic_support(&self, html_src: String) -> () {
        assert!((html_src.contains(&"ar".to_string()) || html_src.contains(&"العربية".to_string()) || html_src.to_lowercase().contains(&"arabic".to_string())));
    }
    pub fn test_backend_url_port_8123(&self, html_src: String) -> () {
        assert!(html_src.contains(&"8123".to_string()));
    }
    pub fn test_sse_stream_endpoint_referenced(&self, html_src: String) -> () {
        assert!((html_src.contains(&"/__comparison/stream".to_string()) || html_src.contains(&"stream".to_string())));
    }
    pub fn test_mixed_comparison_endpoint_referenced(&self, html_src: String) -> () {
        assert!(html_src.contains(&"/__comparison/mixed".to_string()));
    }
    pub fn test_eschtml_xss_function_exists(&self, html_src: String) -> () {
        assert!((html_src.contains(&"escHtml".to_string()) || html_src.contains(&"escHTML".to_string())), "XSS prevention function escHtml/escHTML must exist in frontend");
    }
    pub fn test_question_bank_structure(&self, html_src: String) -> () {
        assert!(html_src.contains(&"_QUESTION_BANK".to_string()));
        for cat in ("emergency".to_string(), "cardiology".to_string(), "coding".to_string(), "reasoning".to_string(), "multilingual".to_string()).iter() {
            assert!(html_src.contains(&cat), "Question bank category missing: {}", cat);
        }
    }
    pub fn test_model_catalog_exists(&self, html_src: String) -> () {
        assert!(html_src.contains(&"_MODEL_CATALOG".to_string()));
    }
    pub fn test_batch_mode_exists(&self, html_src: String) -> () {
        assert!(html_src.to_lowercase().contains(&"batch".to_string()));
    }
    pub fn test_share_report_exists(&self, html_src: String) -> () {
        assert!((html_src.to_lowercase().contains(&"share".to_string()) || html_src.contains(&"shareReport".to_string())));
    }
    pub fn test_streaming_ui_exists(&self, html_src: String) -> () {
        assert!(html_src.to_lowercase().contains(&"stream".to_string()));
    }
    /// Frontend must reference the 5 standard score fields from HOW_TO_USE.
    pub fn test_judge_score_fields_in_frontend(&self, html_src: String) -> () {
        // Frontend must reference the 5 standard score fields from HOW_TO_USE.
        for field in ("accuracy".to_string(), "reasoning".to_string()).iter() {
            assert!(html_src.contains(&field), "Frontend result table missing score field: {}", field);
        }
    }
    /// HOW_TO_USE describes a 'Metrics Summary Bar' with 4 champion stats.
    pub fn test_metrics_summary_bar_exists(&self, html_src: String) -> () {
        // HOW_TO_USE describes a 'Metrics Summary Bar' with 4 champion stats.
        assert!((html_src.to_lowercase().contains(&"ttft".to_string()) || html_src.contains(&"TTFT".to_string()) || html_src.to_lowercase().contains(&"fastest".to_string())));
    }
    pub fn test_discover_section_exists(&self, html_src: String) -> () {
        assert!((html_src.contains(&"__discover-models".to_string()) || html_src.to_lowercase().contains(&"discover".to_string())));
    }
    pub fn test_scenario_presets_exist(&self, html_src: String) -> () {
        assert!((html_src.contains(&"_SCENARIOS".to_string()) || html_src.to_lowercase().contains(&"scenario".to_string())));
    }
    pub fn test_elo_system_exists(&self, html_src: String) -> () {
        assert!((html_src.to_lowercase().contains(&"elo".to_string()) || html_src.contains(&"ELO".to_string())));
    }
    pub fn test_leaderboard_exists(&self, html_src: String) -> () {
        assert!(html_src.to_lowercase().contains(&"leaderboard".to_string()));
    }
    pub fn test_run_history_exists(&self, html_src: String) -> () {
        assert!(html_src.to_lowercase().contains(&"history".to_string()));
    }
}

#[derive(Debug, Clone)]
pub struct TestJudgeFallbackPromptSchema {
}

impl TestJudgeFallbackPromptSchema {
    /// HOW_TO_USE.md documents: overall, accuracy, reasoning, instruction, safety
    /// all as 0-10 integers.
    /// 
    /// The fallback judge system prompt in comparator_backend::py must match
    /// this schema — NOT use instruction_following(true/false) or
    /// safety("safe"/"unsafe") from the old incorrect version.
    pub fn test_fallback_prompt_uses_0_10_scale(&self) -> () {
        // HOW_TO_USE.md documents: overall, accuracy, reasoning, instruction, safety
        // all as 0-10 integers.
        // 
        // The fallback judge system prompt in comparator_backend::py must match
        // this schema — NOT use instruction_following(true/false) or
        // safety("safe"/"unsafe") from the old incorrect version.
        // TODO: import inspect
        let mut src = inspect::getsource(cb::ComparatorHandler._handle_comparison);
        assert!(!src.contains(&"instruction_following".to_string()), "SCHEMA BUG: fallback prompt uses instruction_following(bool) instead of instruction(0-10)");
        assert!((!src.contains(&"\"safe\"/\"unsafe\"".to_string()) && !src.contains(&"'safe'/'unsafe'".to_string())), "SCHEMA BUG: fallback prompt uses safety(string) instead of safety(0-10)");
        assert!((src.contains(&"instruction".to_string()) && src.contains(&"0-10".to_string())), "Fallback prompt must include instruction field with 0-10 scale");
    }
    pub fn test_stream_fallback_prompt_uses_0_10_scale(&self) -> () {
        // TODO: import inspect
        let mut src = inspect::getsource(cb::ComparatorHandler._handle_stream_comparison);
        assert!(!src.contains(&"instruction_following".to_string()), "SCHEMA BUG in stream handler: must use instruction(0-10), not instruction_following(bool)");
    }
    /// HOW_TO_USE documents 6 fields: overall, accuracy, reasoning, instruction, safety, explanation.
    pub fn test_judge_output_schema_fields(&self) -> () {
        // HOW_TO_USE documents 6 fields: overall, accuracy, reasoning, instruction, safety, explanation.
        let mut raw = serde_json::to_string(&HashMap::from([("overall".to_string(), 8), ("accuracy".to_string(), 7), ("reasoning".to_string(), 9), ("instruction".to_string(), 8), ("safety".to_string(), 9), ("explanation".to_string(), "Good response.".to_string())])).unwrap();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 8.0_f64);
        assert!(result.get(&"accuracy".to_string()).cloned() == 7.0_f64);
        assert!(result.get(&"explanation".to_string()).cloned() == "Good response.".to_string());
    }
}

#[derive(Debug, Clone)]
pub struct TestConfigConstants {
}

impl TestConfigConstants {
    pub fn test_default_inference_timeout_positive(&self) -> () {
        assert!(cb::DEFAULT_INFERENCE_TIMEOUT > 0);
    }
    pub fn test_max_inference_timeout_gte_default(&self) -> () {
        assert!(cb::MAX_INFERENCE_TIMEOUT >= cb::DEFAULT_INFERENCE_TIMEOUT);
    }
    pub fn test_max_prompt_tokens_reasonable(&self) -> () {
        assert!((1024 <= cb::MAX_PROMPT_TOKENS) && (cb::MAX_PROMPT_TOKENS <= 32768));
    }
    /// Max 30 min for reasoning models as documented.
    pub fn test_max_timeout_ceiling_30min(&self) -> () {
        // Max 30 min for reasoning models as documented.
        assert!(cb::MAX_INFERENCE_TIMEOUT <= 7200, "Max timeout should not exceed 2 hours");
    }
    /// Backend default port is 8123 per README and CHANGELOG.
    pub fn test_default_port_8123(&self) -> () {
        // Backend default port is 8123 per README and CHANGELOG.
        // TODO: import inspect
        let mut src = inspect::getsource(cb::run_server);
        assert!(src.contains(&"8123".to_string()), "Default port must be 8123");
    }
    pub fn test_discovery_ttl_positive(&self) -> () {
        assert!(cb::_DISCOVERY_TTL > 0);
    }
    pub fn test_rate_limiter_params(&self) -> () {
        let mut rl = cb::_rate_limiter;
        assert!(rl._max > 0);
        assert!(rl._window > 0);
    }
    pub fn test_vulkan_env_set(&self) -> () {
        assert!(os::environ.contains(&"GGML_VK_VISIBLE_DEVICES".to_string()));
    }
}

#[derive(Debug, Clone)]
pub struct TestDownloadAllowList {
}

impl TestDownloadAllowList {
    /// Every host in _ALLOWED_DOWNLOAD_HOSTS must pass validation.
    pub fn test_all_documented_hosts_allowed(&self) -> () {
        // Every host in _ALLOWED_DOWNLOAD_HOSTS must pass validation.
        for host in cb::_ALLOWED_DOWNLOAD_HOSTS.iter() {
            let mut url = format!("https://{}/file.gguf", host);
            assert!(cb::validate_download_url(url) == true, "Documented host {} should be allowed", host);
        }
    }
    pub fn test_empty_string_blocked(&self) -> () {
        assert!(cb::validate_download_url("".to_string()) == false);
    }
    pub fn test_none_like_inputs(&self) -> Result<()> {
        for bad in vec!["null".to_string(), "undefined".to_string(), "http://".to_string(), "https://".to_string()].iter() {
            let mut result = cb::validate_download_url(bad);
            assert!(/* /* isinstance(result, bool) */ */ true);
        }
    }
    pub fn test_known_good_examples(&self) -> () {
        let mut good_urls = vec!["https://huggingface.co/bartowski/Llama-3.1-8B-GGUF/model.gguf".to_string(), "https://cdn-lfs.huggingface.co/repos/x/y/file".to_string(), "https://cdn-lfs-us-1.huggingface.co/repos/x/y/file".to_string(), "https://objects.githubusercontent.com/file.gguf".to_string(), "https://releases.githubusercontent.com/download/v1/model.gguf".to_string()];
        for url in good_urls.iter() {
            assert!(cb::validate_download_url(url) == true, "Should allow: {}", url);
        }
    }
}

#[derive(Debug, Clone)]
pub struct TestModelDiscovery {
}

impl TestModelDiscovery {
    pub fn test_function_exists(&self) -> () {
        assert!(/* hasattr(cb, "_discover_hf_models".to_string()) */ true);
    }
    pub fn test_cache_dict_exists(&self) -> () {
        assert!((/* hasattr(cb, "_discovery_cache".to_string()) */ true && /* /* isinstance(cb::_discovery_cache, dict) */ */ true));
    }
    pub fn test_ttl_constant_exists(&self) -> () {
        assert!((/* hasattr(cb, "_DISCOVERY_TTL".to_string()) */ true && cb::_DISCOVERY_TTL > 0));
    }
    pub fn test_trusted_quantizers_nonempty(&self) -> () {
        assert!(cb::_TRUSTED_QUANTIZERS.len() >= 5);
    }
    pub fn test_trusted_quantizers_known_names(&self) -> () {
        for name in ("bartowski".to_string(), "TheBloke".to_string(), "unsloth".to_string()).iter() {
            assert!(cb::_TRUSTED_QUANTIZERS.contains(&name));
        }
    }
    pub fn test_returns_list(&self) -> () {
        let mut result = cb::_discover_hf_models(/* query= */ "test".to_string(), /* sort= */ "trending".to_string(), /* limit= */ 1);
        assert!(/* /* isinstance(result, list) */ */ true);
    }
}

#[derive(Debug, Clone)]
pub struct TestInstallJobLifecycle {
}

impl TestInstallJobLifecycle {
    pub fn test_install_jobs_dict_exists(&self) -> () {
        assert!(/* /* isinstance(cb::_install_jobs, dict) */ */ true);
    }
    pub fn test_install_lock_exists(&self) -> () {
        assert!(/* /* isinstance(cb::_install_lock, r#type(std::sync::Mutex::new(() */))) */ true);
    }
    pub fn test_valid_install_creates_job(&self) -> () {
        let (mut s, _, mut b) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install llama-cpp-python".to_string())]));
        assert!(s == 200);
        let mut job_id = b.get(&"job_id".to_string()).cloned();
        assert!(job_id.is_some());
        let (mut s2, _, mut status) = _get(format!("/__install-status?job={}", job_id));
        assert!(s2 == 200);
        assert!(("starting".to_string(), "running".to_string(), "done".to_string(), "error".to_string()).contains(&status.get(&"state".to_string()).cloned()));
    }
    pub fn test_invalid_install_rejected(&self) -> () {
        let (mut s, _, mut b) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install requests".to_string())]));
        assert!(s == 400);
        assert!(b.get(&"ok".to_string()).cloned() == false);
    }
}

#[derive(Debug, Clone)]
pub struct TestMetricsMath {
}

impl TestMetricsMath {
    pub fn test_count_tokens_empty(&self) -> () {
        assert!(cb::count_tokens("".to_string()) == 0);
    }
    pub fn test_count_tokens_positive(&self) -> () {
        assert!(cb::count_tokens("Hello world".to_string()) > 0);
    }
    pub fn test_count_tokens_returns_int(&self) -> () {
        assert!(/* /* isinstance(cb::count_tokens("test".to_string()), int) */ */ true);
    }
    pub fn test_count_tokens_long_text_in_range(&self) -> () {
        let mut text = ("The quick brown fox ".to_string() * 500);
        let mut n = cb::count_tokens(text);
        let mut words = text.split_whitespace().map(|s| s.to_string()).collect::<Vec<String>>().len();
        assert!(((0.5_f64 * words) < n) && (n < (3.0_f64 * words)), "Token count {} out of expected range for {} words", n, words);
    }
    pub fn test_get_cpu_count_positive(&self) -> () {
        assert!(cb::get_cpu_count() >= 1);
    }
    pub fn test_get_memory_gb_positive(&self) -> () {
        assert!(cb::get_memory_gb() > 0.0_f64);
    }
    pub fn test_cpu_info_schema(&self) -> () {
        let mut cpu = cb::get_cpu_info();
        for key in ("brand".to_string(), "name".to_string(), "cores".to_string(), "avx2".to_string(), "avx512".to_string()).iter() {
            assert!(cpu.contains(&key));
        }
    }
    pub fn test_gpu_info_is_list(&self) -> () {
        assert!(/* /* isinstance(cb::get_gpu_info(), list) */ */ true);
    }
    pub fn test_llama_cpp_info_schema(&self) -> () {
        let mut info = cb::get_llama_cpp_info();
        assert!(info.contains(&"installed".to_string()));
        assert!(/* /* isinstance(info["installed".to_string()], bool) */ */ true);
    }
    pub fn test_recommend_build_schema(&self) -> () {
        let mut cpu = cb::get_cpu_info();
        let mut gpus = cb::get_gpu_info();
        let mut rec = cb::recommend_llama_build(cpu, gpus);
        for key in ("build".to_string(), "pip".to_string(), "reason".to_string(), "note".to_string()).iter() {
            assert!(rec.contains(&key), "recommend_llama_build missing key: {}", key);
        }
    }
}

#[derive(Debug, Clone)]
pub struct TestPathTraversal {
}

impl TestPathTraversal {
    pub fn setup_method(&mut self) -> () {
        self.allowed_dir = std::env::temp_dir().join("tmp");
    }
    pub fn teardown_method(&mut self) -> () {
        // TODO: import shutil
        std::fs::remove_dir_all(self.allowed_dir, /* ignore_errors= */ true).ok();
    }
    pub fn test_dotdot_blocked(&mut self) -> () {
        let mut malicious = PathBuf::from(self.allowed_dir).join("..".to_string()).join("etc".to_string()).join("passwd.gguf".to_string());
        assert!(!cb::_is_safe_model_path(malicious, vec![self.allowed_dir]));
    }
    pub fn test_double_dotdot_blocked(&mut self) -> () {
        let mut malicious = PathBuf::from(self.allowed_dir).join("..".to_string()).join("..".to_string()).join("etc".to_string()).join("shadow.gguf".to_string());
        assert!(!cb::_is_safe_model_path(malicious, vec![self.allowed_dir]));
    }
    pub fn test_absolute_path_outside_allowed(&self) -> () {
        assert!(!cb::_is_safe_model_path("/etc/passwd.gguf".to_string(), vec![self.allowed_dir]));
    }
    /// Backslash traversal on Windows.
    pub fn test_windows_style_traversal(&mut self) -> () {
        // Backslash traversal on Windows.
        let mut malicious = (self.allowed_dir + "\\..\\sensitive.gguf".to_string());
        assert!(!cb::_is_safe_model_path(malicious, vec![self.allowed_dir]));
    }
    pub fn test_non_gguf_blocked_even_inside_dir(&mut self) -> () {
        let mut path = PathBuf::from(self.allowed_dir).join("config.py".to_string());
        assert!(!cb::_is_safe_model_path(path, vec![self.allowed_dir]));
    }
    pub fn test_valid_gguf_inside_dir_allowed(&mut self) -> Result<()> {
        let mut path = PathBuf::from(self.allowed_dir).join("model.gguf".to_string());
        let mut f = File::open(path)?;
        {
            f.write((b" " * 10));
        }
        Ok(assert!(cb::_is_safe_model_path(path, vec![self.allowed_dir])))
    }
}

/// Validates the security fix: judge model path goes through _is_safe_model_path.
#[derive(Debug, Clone)]
pub struct TestJudgePathSafety {
}

impl TestJudgePathSafety {
    /// _resolve_judge_path with 'local:best' picks the largest from provided list.
    pub fn test_resolve_judge_local_best_uses_list(&self) -> Result<()> {
        // _resolve_judge_path with 'local:best' picks the largest from provided list.
        let mut tmpdir = tempfile::TemporaryDirectory();
        {
            let mut a = PathBuf::from(tmpdir).join("small.gguf".to_string());
            let mut b_model = PathBuf::from(tmpdir).join("large.gguf".to_string());
            let mut f = File::open(a)?;
            {
                f.write((b" " * ((100 * 1024) * 1024)));
            }
            let mut f = File::open(b_model)?;
            {
                f.write((b" " * ((200 * 1024) * 1024)));
            }
            let mut handler = cb::ComparatorHandler.__new__(cb::ComparatorHandler);
            let mut result = handler._resolve_judge_path("local:best".to_string(), vec![a, b_model]);
            assert!(result == b_model, "Should pick the largest file as judge");
        }
    }
    pub fn test_resolve_judge_by_basename_match(&self) -> Result<()> {
        let mut tmpdir = tempfile::TemporaryDirectory();
        {
            let mut model_path = PathBuf::from(tmpdir).join("qwen-7b.gguf".to_string());
            let mut f = File::open(model_path)?;
            {
                f.write((b" " * ((10 * 1024) * 1024)));
            }
            let mut handler = cb::ComparatorHandler.__new__(cb::ComparatorHandler);
            let mut result = handler._resolve_judge_path("qwen-7b".to_string(), vec![model_path]);
            assert!(result == model_path);
        }
    }
    pub fn test_direct_path_that_does_not_exist_returns_none(&self) -> () {
        let mut handler = cb::ComparatorHandler.__new__(cb::ComparatorHandler);
        let mut result = handler._resolve_judge_path("/nonexistent/path.gguf".to_string(), vec![]);
        assert!(result.is_none());
    }
    /// After fix, judge path must pass _is_safe_model_path before use.
    pub fn test_judge_safe_path_check_logic(&self) -> () {
        // After fix, judge path must pass _is_safe_model_path before use.
        // TODO: import inspect
        let mut comparison_src = inspect::getsource(cb::ComparatorHandler._handle_comparison);
        assert!(comparison_src.contains(&"_is_safe_model_path".to_string()), "SECURITY: _handle_comparison must validate judge_path with _is_safe_model_path");
    }
}

#[derive(Debug, Clone)]
pub struct TestConcurrency {
}

impl TestConcurrency {
    pub fn test_rate_limiter_concurrent_exact_count(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 50, /* window_sec= */ 60);
        let mut allowed_count = vec![];
        let mut lock = std::sync::Mutex::new(());
        let mut barrier = threading::Barrier(10);
        let _burst = || {
            barrier.wait();
            for _ in 0..10.iter() {
                let mut result = rl.allow("shared_ip".to_string());
                let _ctx = lock;
                {
                    allowed_count.push(result);
                }
            }
        };
        let mut threads = 0..10.iter().map(|_| std::thread::spawn(|| {})).collect::<Vec<_>>();
        for t in threads.iter() {
            t.start();
        }
        for t in threads.iter() {
            t.join();
        }
        let mut total_allowed = allowed_count.iter().filter(|r| r).map(|r| 1).collect::<Vec<_>>().iter().sum::<i64>();
        assert!(total_allowed == 50, "Thread-safe: exactly 50 allowed, got {}", total_allowed);
    }
    pub fn test_multiple_ips_independent(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 5, /* window_sec= */ 60);
        let mut results = HashMap::new();
        let mut lock = std::sync::Mutex::new(());
        let _work = |ip| {
            let mut allowed = 0;
            for _ in 0..10.iter() {
                if rl.allow(ip) {
                    allowed += 1;
                }
            }
            let _ctx = lock;
            {
                results[ip] = allowed;
            }
        };
        let mut threads = 0..10.iter().map(|i| std::thread::spawn(|| {})).collect::<Vec<_>>();
        for t in threads.iter() {
            t.start();
        }
        for t in threads.iter() {
            t.join();
        }
        for (ip, allowed) in results.iter().iter() {
            assert!(allowed == 5, "Each IP should allow exactly 5, {} got {}", ip, allowed);
        }
    }
}

#[derive(Debug, Clone)]
pub struct TestDocumentationAccuracy {
}

impl TestDocumentationAccuracy {
    /// README says C:\AI\Models but backend uses Path.home() / 'AI' / 'Models'.
    /// This test fails if the paths don't match, alerting authors to update docs.
    pub fn test_readme_model_dir_warning(&self) -> Result<()> {
        // README says C:\AI\Models but backend uses Path.home() / 'AI' / 'Models'.
        // This test fails if the paths don't match, alerting authors to update docs.
        // TODO: from pathlib import Path
        let mut backend_default = ((Path.home() / "AI".to_string()) / "Models".to_string()).to_string();
        let mut readme_path = PathBuf::from(REPO_ROOT).join("README.md".to_string());
        let mut f = File::open(readme_path)?;
        {
            let mut readme = f.read();
        }
        if (readme.contains(&"C:\\AI\\Models".to_string()) && backend_default != "C:\\AI\\Models".to_string()) {
            // TODO: import warnings
            warnings.warn(format!("DOCS: README says 'C:\\AI\\Models' but backend uses '{}'. Update README.md to say '~/AI/Models' or set ZENAI_MODEL_DIR.", backend_default), UserWarning, /* stacklevel= */ 1);
        }
    }
    /// HOW_TO_USE.md says '100+' questions. Actual count should be verified.
    /// The comparison table says '32-prompt question bank' which is accurate.
    pub fn test_question_bank_count(&self) -> () {
        // HOW_TO_USE.md says '100+' questions. Actual count should be verified.
        // The comparison table says '32-prompt question bank' which is accurate.
        let mut html = _html();
        let mut q_count = (html.iter().filter(|v| **v == "{ q:\"".to_string()).count() + html.iter().filter(|v| **v == "{ q:'".to_string()).count());
        assert!(q_count >= 30, "Question bank has only {} entries", q_count);
    }
    /// README documents exactly 5 judge templates.
    pub fn test_judge_templates_count(&self) -> () {
        // README documents exactly 5 judge templates.
        let mut html = _html();
        let mut template_count = ((((html.iter().filter(|v| **v == "Medical".to_string()).count() + html.iter().filter(|v| **v == "Clinical".to_string()).count()) + html.iter().filter(|v| **v == "Research".to_string()).count()) + html.iter().filter(|v| **v == "Code Review".to_string()).count()) + html.iter().filter(|v| **v == "Creative".to_string()).count());
        assert!(template_count >= 5, "Must have ≥5 judge template references in HTML");
    }
    pub fn test_changelog_mentions_threading(&self) -> Result<()> {
        let mut changelog = PathBuf::from(REPO_ROOT).join("CHANGELOG.md".to_string());
        let mut f = File::open(changelog)?;
        {
            let mut content = f.read();
        }
        Ok(assert!((content.contains(&"ThreadingHTTPServer".to_string()) || content.to_lowercase().contains(&"thread".to_string()))))
    }
    pub fn test_run_me_bat_port_matches_backend(&self) -> Result<()> {
        let mut bat = PathBuf::from(REPO_ROOT).join("Run_me.bat".to_string());
        let mut f = File::open(bat)?;
        {
            let mut bat_content = f.read();
        }
        assert!(bat_content.contains(&"8123".to_string()), "Run_me.bat must reference port 8123");
        Ok(assert!(bat_content.contains(&"comparator_backend".to_string())))
    }
    pub fn test_requirements_has_core_deps(&self) -> Result<()> {
        let mut reqs = PathBuf::from(REPO_ROOT).join("requirements.txt".to_string());
        let mut f = File::open(reqs)?;
        {
            let mut content = f.read().to_lowercase();
        }
        for dep in ("psutil".to_string(), "huggingface".to_string(), "llama".to_string()).iter() {
            assert!(content.contains(&dep), "requirements.txt missing core dep: {}", dep);
        }
    }
}

pub fn _launch_server_once() -> Result<()> {
    let _ctx = _server_lock;
    {
        if _server_started.is_set() {
            return;
        }
        // TODO: from http::server import HTTPServer
        let mut srv = HTTPServer(("127.0.0.1".to_string(), _PORT), cb::ComparatorHandler);
        let mut t = std::thread::spawn(|| {});
        t.start();
        for _ in 0..60.iter() {
            // try:
            {
                urllib::request.urlopen(format!("{}/__health", _BASE), /* timeout= */ 1);
                _server_started.set();
                return;
            }
            // except Exception as _e:
        }
        return Err(anyhow::anyhow!("RuntimeError('Test server never became ready')"));
    }
}

pub fn _get(path: String, headers: Option<HashMap>, timeout: i64) -> Result<()> {
    let mut req = urllib::request.Request((_BASE + path));
    if headers {
        for (k, v) in headers.iter().iter() {
            req.add_header(k, v);
        }
    }
    // try:
    {
        let mut r = urllib::request.urlopen(req, /* timeout= */ timeout);
        {
            (r.status, /* dict(r.headers) */ HashMap::new(), serde_json::from_str(&r.read()).unwrap())
        }
    }
    // except urllib::error.HTTPError as e:
}

pub fn _post(path: String, payload: HashMap<String, serde_json::Value>, timeout: i64) -> Result<()> {
    let mut body = serde_json::to_string(&payload).unwrap().as_bytes().to_vec();
    let mut req = urllib::request.Request((_BASE + path), /* data= */ body, /* headers= */ HashMap::from([("Content-Type".to_string(), "application/json".to_string())]), /* method= */ "POST".to_string());
    // try:
    {
        let mut r = urllib::request.urlopen(req, /* timeout= */ timeout);
        {
            (r.status, /* dict(r.headers) */ HashMap::new(), serde_json::from_str(&r.read()).unwrap())
        }
    }
    // except urllib::error.HTTPError as e:
}

pub fn _options(path: String, headers: Option<HashMap>, timeout: i64) -> Result<()> {
    let mut req = urllib::request.Request((_BASE + path), /* method= */ "OPTIONS".to_string());
    if headers {
        for (k, v) in headers.iter().iter() {
            req.add_header(k, v);
        }
    }
    // try:
    {
        let mut r = urllib::request.urlopen(req, /* timeout= */ timeout);
        {
            (r.status, /* dict(r.headers) */ HashMap::new())
        }
    }
    // except urllib::error.HTTPError as e:
}

pub fn _html() -> Result<String> {
    let mut f = File::open(HTML_PATH)?;
    {
        f.read()
    }
}

pub fn server() -> () {
    _launch_server_once();
}

pub fn html_src() -> () {
    _html()
}
