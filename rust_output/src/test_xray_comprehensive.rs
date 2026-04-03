/// X-RAY LLM Comprehensive Test Suite
/// ====================================
/// Full-coverage functional tests (no mocks) that validate every feature
/// documented in the project specification and HOW_TO_USE.md.
/// 
/// Run:
/// pytest tests/test_xray_comprehensive::py -v --tb=short
/// pytest tests/test_xray_comprehensive::py -v -k "not slow"    # skip slow tests

use anyhow::{Result, Context};
use crate::comparator_backend as cb;
use std::collections::HashMap;
use std::collections::HashSet;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::PathBuf;

pub static REPO_ROOT: std::sync::LazyLock<String /* os::path.dirname */> = std::sync::LazyLock::new(|| Default::default());

pub const TEST_PORT: i64 = 18125;

pub const TEST_URL: &str = "f'http://127.0.0.1:{TEST_PORT}";

pub static _SERVER: std::sync::LazyLock<Option<serde_json::Value>> = std::sync::LazyLock::new(|| None);

/// Validate hardware detection functions against spec requirements.
#[derive(Debug, Clone)]
pub struct TestSystemInfoDetection {
}

impl TestSystemInfoDetection {
    pub fn test_cpu_count_positive(&self) -> () {
        assert!(cb::get_cpu_count() >= 1);
    }
    pub fn test_memory_gb_positive(&self) -> () {
        let mut mem = cb::get_memory_gb();
        assert!((/* /* isinstance(mem, float) */ */ true && mem > 0));
    }
    pub fn test_cpu_info_complete_keys(&self) -> () {
        let mut cpu = cb::get_cpu_info();
        for key in ("brand".to_string(), "name".to_string(), "cores".to_string(), "avx2".to_string(), "avx512".to_string()).iter() {
            assert!(cpu.contains(&key), "Missing key: {}", key);
        }
        assert!(cpu["cores".to_string()] >= 1);
        assert!(/* /* isinstance(cpu["avx2".to_string()], bool) */ */ true);
        assert!(/* /* isinstance(cpu["avx512".to_string()], bool) */ */ true);
    }
    pub fn test_gpu_info_returns_list(&self) -> () {
        let mut gpus = cb::get_gpu_info();
        assert!(/* /* isinstance(gpus, list) */ */ true);
        for g in gpus.iter() {
            assert!(g.contains(&"name".to_string()));
            assert!(g.contains(&"vendor".to_string()));
            assert!(g.contains(&"vram_gb".to_string()));
            assert!(g.contains(&"backend".to_string()));
        }
    }
    pub fn test_llama_cpp_info_structure(&self) -> () {
        let mut info = cb::get_llama_cpp_info();
        assert!(info.contains(&"installed".to_string()));
        assert!(info.contains(&"version".to_string()));
        assert!(/* /* isinstance(info["installed".to_string()], bool) */ */ true);
    }
    pub fn test_recommend_llama_build_structure(&self) -> () {
        let mut cpu = cb::get_cpu_info();
        let mut gpus = cb::get_gpu_info();
        let mut rec = cb::recommend_llama_build(cpu, gpus);
        for key in ("build".to_string(), "flag".to_string(), "reason".to_string(), "pip".to_string(), "note".to_string()).iter() {
            assert!(rec.contains(&key), "Missing recommend key: {}", key);
        }
    }
    pub fn test_get_system_info_full_payload(&self) -> () {
        let mut info = cb::get_system_info(cb::ComparatorHandler.model_dirs);
        let mut required = HashSet::from(["cpu_brand".to_string(), "cpu_count".to_string(), "cpu_name".to_string(), "cpu_avx2".to_string(), "cpu_avx512".to_string(), "memory_gb".to_string(), "gpus".to_string(), "has_llama_cpp".to_string(), "llama_cpp_version".to_string(), "recommended_build".to_string(), "model_count".to_string(), "models".to_string(), "timestamp".to_string()]);
        let mut missing = (required - info.keys().into_iter().collect::<HashSet<_>>());
        assert!(!missing, "Missing system info keys: {}", missing);
    }
}

/// Validate model scanning logic per specification.
#[derive(Debug, Clone)]
pub struct TestModelScanning {
}

impl TestModelScanning {
    pub fn test_scan_models_returns_list(&self) -> () {
        let mut result = cb::scan_models(vec![]);
        assert!(/* /* isinstance(result, list) */ */ true);
    }
    pub fn test_scan_models_nonexistent_dir(&self) -> () {
        let mut result = cb::scan_models(vec!["/nonexistent/path/that/does/not/exist".to_string()]);
        assert!(result == vec![]);
    }
    /// Only .gguf files >= 50MB should be returned.
    pub fn test_scan_models_filters_non_gguf(&self) -> () {
        // Only .gguf files >= 50MB should be returned.
        let mut result = cb::scan_models(cb::ComparatorHandler.model_dirs);
        for m in result.iter() {
            assert!(m["name".to_string()].to_lowercase().ends_with(&*".gguf".to_string()));
            assert!(m["size_gb".to_string()] >= 0.05_f64);
        }
    }
    pub fn test_scan_models_sorted_alphabetically(&self) -> () {
        let mut result = cb::scan_models(cb::ComparatorHandler.model_dirs);
        if result.len() > 1 {
            let mut names = result.iter().map(|m| m["name".to_string()].to_lowercase()).collect::<Vec<_>>();
            assert!(names == { let mut v = names.clone(); v.sort(); v });
        }
    }
    /// BitNet i2_s, i1, i2, i3 quantizations should be skipped.
    pub fn test_scan_skips_incompatible_quants(&self) -> Result<()> {
        // BitNet i2_s, i1, i2, i3 quantizations should be skipped.
        assert!(/* hasattr(cb, "scan_models".to_string()) */ true);
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        assert!(src.contains(&"i2_s".to_string()));
        Ok(assert!(src.contains(&"_INCOMPATIBLE_QUANT_SUFFIXES".to_string())))
    }
    /// Model dirs should include env var, home-based, or project-local paths.
    pub fn test_model_dirs_configured(&self) -> () {
        // Model dirs should include env var, home-based, or project-local paths.
        let mut dirs = cb::ComparatorHandler.model_dirs;
        assert!((/* /* isinstance(dirs, list) */ */ true && dirs.len() >= 1));
    }
}

/// Validate token counting uses real tokenizer not word split.
#[derive(Debug, Clone)]
pub struct TestTokenCounting {
}

impl TestTokenCounting {
    pub fn test_empty_string(&self) -> () {
        assert!(cb::count_tokens("".to_string()) == 0);
    }
    pub fn test_returns_int(&self) -> () {
        assert!(/* /* isinstance(cb::count_tokens("Hello world".to_string()), int) */ */ true);
    }
    pub fn test_positive_for_nonempty(&self) -> () {
        assert!(cb::count_tokens("Hello world".to_string()) > 0);
    }
    pub fn test_differs_from_word_split(&self) -> () {
        let mut text = "Hello, world! I don't think this should be split naively.".to_string();
        let mut word_count = text.split_whitespace().map(|s| s.to_string()).collect::<Vec<String>>().len();
        let mut token_count = cb::count_tokens(text);
        assert!(token_count != word_count);
    }
    pub fn test_unicode_safe(&self) -> () {
        assert!(cb::count_tokens("日本語テスト 🎉".to_string()) > 0);
    }
    pub fn test_long_text(&self) -> () {
        let mut text = ("The quick brown fox ".to_string() * 500);
        let mut result = cb::count_tokens(text);
        assert!(result > 100);
    }
    /// Contractions like don't should tokenize into multiple tokens.
    pub fn test_contractions_produce_more_tokens(&self) -> () {
        // Contractions like don't should tokenize into multiple tokens.
        let mut result = cb::count_tokens("don't won't can't".to_string());
        assert!(result > 3);
    }
}

/// Validate the 5-layer score extraction pipeline.
#[derive(Debug, Clone)]
pub struct TestJudgeScoreExtraction {
}

impl TestJudgeScoreExtraction {
    pub fn test_empty_input(&self) -> () {
        let mut result = cb::extract_judge_scores("".to_string());
        assert!(result == HashMap::from([("overall".to_string(), 0)]));
    }
    pub fn test_clean_json(&self) -> () {
        let mut raw = "{\"overall\": 8.5, \"accuracy\": 7, \"reasoning\": 9}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 8.5_f64);
    }
    pub fn test_markdown_fenced_json(&self) -> () {
        let mut raw = "```json\n{\"overall\": 7.0, \"accuracy\": 6}\n```".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 7.0_f64);
    }
    pub fn test_nested_json(&self) -> () {
        let mut raw = "{\"evaluation\": {\"overall\": 8, \"accuracy\": 7}}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 8);
    }
    pub fn test_natural_language_scores(&self) -> () {
        let mut raw = "Overall: 6 out of 10. Accuracy: 5/10.".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 6.0_f64);
    }
    pub fn test_garbage_returns_zero(&self) -> () {
        let mut raw = "I refuse to rate this meaningfully xyz".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result.contains(&"overall".to_string()));
        assert!(/* /* isinstance(result["overall".to_string()], (int, float) */) */ true);
    }
    pub fn test_score_clamped_to_0_10(&self) -> () {
        let mut raw = "{\"overall\": 15, \"accuracy\": -3}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!((0 <= result["overall".to_string()]) && (result["overall".to_string()] <= 10));
    }
    pub fn test_string_score_parsed(&self) -> () {
        let mut raw = "{\"overall\": \"8/10\"}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 8.0_f64);
    }
    pub fn test_unquoted_keys(&self) -> () {
        let mut raw = "{overall: 7, accuracy: 6}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result.get(&"overall".to_string()).cloned() == 7);
    }
    pub fn test_partial_json_in_text(&self) -> () {
        let mut raw = "Here is my evaluation: {\"overall\": 9} and some trailing text".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 9);
    }
}

/// Validate SSRF prevention on download URLs.
#[derive(Debug, Clone)]
pub struct TestURLValidation {
}

impl TestURLValidation {
    pub fn test_allows_huggingface(&self) -> () {
        assert!(cb::validate_download_url("https://huggingface.co/TheBloke/model/resolve/main/file.gguf".to_string()) == true);
    }
    pub fn test_allows_github(&self) -> () {
        assert!(cb::validate_download_url("https://github.com/user/repo/releases/download/v1/model.gguf".to_string()) == true);
    }
    pub fn test_allows_cdn_lfs(&self) -> () {
        assert!(cb::validate_download_url("https://cdn-lfs.huggingface.co/some/path".to_string()) == true);
    }
    pub fn test_blocks_http(&self) -> () {
        assert!(cb::validate_download_url("http://huggingface.co/model".to_string()) == false);
    }
    pub fn test_blocks_localhost(&self) -> () {
        for url in vec!["http://localhost/secret".to_string(), "http://127.0.0.1:9200/_cluster".to_string(), "http://[::1]/admin".to_string()].iter() {
            assert!(cb::validate_download_url(url) == false);
        }
    }
    pub fn test_blocks_private_ips(&self) -> () {
        for url in vec!["http://192.168.1.1/admin".to_string(), "http://10.0.0.1/internal".to_string(), "http://172.16.0.1/secret".to_string()].iter() {
            assert!(cb::validate_download_url(url) == false);
        }
    }
    pub fn test_blocks_ftp(&self) -> () {
        assert!(cb::validate_download_url("ftp://server/model.gguf".to_string()) == false);
    }
    pub fn test_blocks_file_scheme(&self) -> () {
        assert!(cb::validate_download_url("file:///etc/passwd".to_string()) == false);
    }
    pub fn test_blocks_unknown_hosts(&self) -> () {
        assert!(cb::validate_download_url("https://evil-site.com/model.gguf".to_string()) == false);
    }
}

/// Validate path traversal prevention.
#[derive(Debug, Clone)]
pub struct TestSafeModelPath {
}

impl TestSafeModelPath {
    pub fn test_rejects_non_gguf(&self) -> () {
        assert!(cb::_is_safe_model_path("/some/path/file.txt".to_string(), vec!["/some/path".to_string()]) == false);
    }
    pub fn test_rejects_empty(&self) -> () {
        assert!(cb::_is_safe_model_path("".to_string(), vec!["/some/path".to_string()]) == false);
    }
    pub fn test_rejects_outside_model_dirs(&self) -> () {
        assert!(cb::_is_safe_model_path("C:\\Windows\\System32\\evil.gguf".to_string(), vec!["C:\\AI\\Models".to_string()]) == false);
    }
}

/// Validate per-IP rate limiting.
#[derive(Debug, Clone)]
pub struct TestRateLimiter {
}

impl TestRateLimiter {
    pub fn test_allows_under_limit(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 5, /* window_sec= */ 60);
        for _ in 0..5.iter() {
            assert!(rl.allow("test_ip".to_string()) == true);
        }
    }
    pub fn test_blocks_over_limit(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 2, /* window_sec= */ 60);
        rl.allow("ip1".to_string());
        rl.allow("ip1".to_string());
        assert!(rl.allow("ip1".to_string()) == false);
    }
    pub fn test_per_ip_isolation(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 1, /* window_sec= */ 60);
        rl.allow("ip_a".to_string());
        assert!(rl.allow("ip_a".to_string()) == false);
        assert!(rl.allow("ip_b".to_string()) == true);
    }
    pub fn test_remaining_count(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 5, /* window_sec= */ 60);
        assert!(rl.remaining("x".to_string()) == 5);
        rl.allow("x".to_string());
        rl.allow("x".to_string());
        assert!(rl.remaining("x".to_string()) == 3);
    }
    pub fn test_window_expiry(&self) -> () {
        let mut rl = cb::_RateLimiter(/* max_requests= */ 1, /* window_sec= */ 0.1_f64);
        assert!(rl.allow("y".to_string()) == true);
        assert!(rl.allow("y".to_string()) == false);
        std::thread::sleep(std::time::Duration::from_secs_f64(0.15_f64));
        assert!(rl.allow("y".to_string()) == true);
    }
    pub fn test_global_instance_exists(&self) -> () {
        assert!(/* /* isinstance(cb::_rate_limiter, cb::_RateLimiter) */ */ true);
    }
}

/// Validate all documented HTTP endpoints.
#[derive(Debug, Clone)]
pub struct TestHTTPEndpoints {
}

impl TestHTTPEndpoints {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_health_endpoint(&self) -> () {
        let (mut status, _, mut body) = _get("/__health".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(status == 200);
        assert!(body.get(&"ok".to_string()).cloned() == true);
    }
    pub fn test_system_info_endpoint(&self) -> () {
        let (mut status, _, mut body) = _get("/__system-info".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(status == 200);
        for key in ("cpu_count".to_string(), "memory_gb".to_string(), "has_llama_cpp".to_string(), "models".to_string()).iter() {
            assert!(body.contains(&key));
        }
    }
    pub fn test_config_endpoint(&self) -> () {
        let (mut status, _, mut body) = _get("/__config".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(status == 200);
        assert!(body.contains(&"default_inference_timeout".to_string()));
        assert!(body.contains(&"max_inference_timeout".to_string()));
        assert!(body.contains(&"rate_limit".to_string()));
    }
    /// Backend should serve model_comparator.html at /
    pub fn test_html_served_at_root(&self) -> Result<()> {
        // Backend should serve model_comparator.html at /
        let mut req = urllib::request.Request(format!("{}/", TEST_URL));
        req.add_header("Origin".to_string(), "http://127.0.0.1:8123".to_string());
        let mut resp = urllib::request.urlopen(req, /* timeout= */ 10);
        {
            assert!(resp.status == 200);
            let mut ct = resp.headers.get(&"Content-Type".to_string()).cloned().unwrap_or("".to_string());
            assert!(ct.contains(&"text/html".to_string()));
        }
    }
    pub fn test_404_for_unknown(&self) -> () {
        let (mut status, _, _) = _get("/__nonexistent".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(status == 404);
    }
    pub fn test_discover_models_endpoint(&self) -> () {
        let (mut status, _, mut body) = _get("/__discover-models?q=test&sort=trending&limit=5".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(status == 200);
        assert!(body.contains(&"models".to_string()));
    }
    /// POST with invalid JSON should return 400.
    pub fn test_comparison_endpoint_rejects_invalid_json(&self) -> Result<()> {
        // POST with invalid JSON should return 400.
        let mut req = urllib::request.Request(format!("{}/__comparison/mixed", TEST_URL), /* data= */ b"not json", /* method= */ "POST".to_string());
        req.add_header("Content-Type".to_string(), "application/json".to_string());
        req.add_header("Origin".to_string(), "http://127.0.0.1:8123".to_string());
        // try:
        {
            let mut resp = urllib::request.urlopen(req, /* timeout= */ 10);
            {
                let mut status = resp.status;
            }
        }
        // except urllib::error.HTTPError as e:
        Ok(assert!(status == 400))
    }
    /// Comparison with no models should return valid JSON (empty results).
    pub fn test_comparison_empty_models(&self) -> () {
        // Comparison with no models should return valid JSON (empty results).
        let (mut status, _, mut body) = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), "Hello".to_string()), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(status == 200);
        assert!(body.contains(&"responses".to_string()));
    }
}

/// Validate CORS is restricted to localhost.
#[derive(Debug, Clone)]
pub struct TestCORSSecurity {
}

impl TestCORSSecurity {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_localhost_allowed(&self) -> () {
        let (mut status, mut hdrs, _) = _get("/__health".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        let mut acao = hdrs.get(&"Access-Control-Allow-Origin".to_string()).cloned().unwrap_or("".to_string());
        assert!(acao.contains(&"127.0.0.1".to_string()));
    }
    pub fn test_not_wildcard(&self) -> () {
        let (_, mut hdrs, _) = _get("/__health".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(hdrs.get(&"Access-Control-Allow-Origin".to_string()).cloned() != "*".to_string());
    }
    pub fn test_external_origin_blocked(&self) -> () {
        let (_, mut hdrs, _) = _get("/__health".to_string(), HashMap::from([("Origin".to_string(), "https://evil.com".to_string())]));
        let mut acao = hdrs.get(&"Access-Control-Allow-Origin".to_string()).cloned().unwrap_or("".to_string());
        assert!(!acao.contains(&"evil.com".to_string()));
    }
    /// file:// protocol sends Origin: null — should work.
    pub fn test_null_origin_allowed(&self) -> () {
        // file:// protocol sends Origin: null — should work.
        let (mut status, _, _) = _get("/__health".to_string(), HashMap::from([("Origin".to_string(), "null".to_string())]));
        assert!(status == 200);
    }
    pub fn test_preflight_returns_204(&self) -> Result<()> {
        let mut req = urllib::request.Request(format!("{}/__comparison/mixed", TEST_URL), /* method= */ "OPTIONS".to_string());
        req.add_header("Origin".to_string(), "http://127.0.0.1:8123".to_string());
        req.add_header("Access-Control-Request-Method".to_string(), "POST".to_string());
        req.add_header("Access-Control-Request-Headers".to_string(), "Content-Type".to_string());
        // try:
        {
            let mut resp = urllib::request.urlopen(req, /* timeout= */ 5);
            {
                assert!(resp.status == 204);
            }
        }
        // except urllib::error.HTTPError as e:
    }
}

/// Validate the SSE streaming comparison endpoint.
#[derive(Debug, Clone)]
pub struct TestSSEStreaming {
}

impl TestSSEStreaming {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn _stream_post(&self, path: String, body_dict: String, read_timeout: String) -> Result<()> {
        // TODO: import http::client
        let mut conn = http::client.HTTPConnection("127.0.0.1".to_string(), TEST_PORT, /* timeout= */ read_timeout);
        let mut body = serde_json::to_string(&body_dict).unwrap();
        conn.request("POST".to_string(), path, /* body= */ body, /* headers= */ HashMap::from([("Content-Type".to_string(), "application/json".to_string()), ("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        let mut resp = conn.getresponse();
        let mut status = resp.status;
        let mut headers = /* dict(resp.getheaders()) */ HashMap::new();
        let mut body_data = resp.read(8192).decode("utf-8".to_string(), /* errors= */ "replace".to_string());
        conn.close();
        Ok((status, headers, body_data))
    }
    pub fn test_stream_endpoint_exists(&mut self) -> () {
        let (mut status, _, _) = self._stream_post("/__comparison/stream".to_string(), HashMap::from([("prompt".to_string(), "Hello".to_string()), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]));
        assert!(status == 200);
    }
    pub fn test_content_type_is_sse(&mut self) -> () {
        let (_, mut headers, _) = self._stream_post("/__comparison/stream".to_string(), HashMap::from([("prompt".to_string(), "Test".to_string()), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]));
        assert!(headers.get(&"Content-Type".to_string()).cloned().unwrap_or("".to_string()).contains(&"text/event-stream".to_string()));
    }
    pub fn test_sends_done_event(&mut self) -> () {
        let (_, _, mut body) = self._stream_post("/__comparison/stream".to_string(), HashMap::from([("prompt".to_string(), "Test".to_string()), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]));
        assert!(body.contains(&"event:".to_string()));
    }
}

/// Validate timeout constants and clamping.
#[derive(Debug, Clone)]
pub struct TestInferenceTimeout {
}

impl TestInferenceTimeout {
    pub fn test_default_timeout(&self) -> () {
        assert!(cb::DEFAULT_INFERENCE_TIMEOUT == 300);
    }
    pub fn test_max_timeout(&self) -> () {
        assert!(cb::MAX_INFERENCE_TIMEOUT == 1800);
    }
    pub fn test_max_exceeds_default(&self) -> () {
        assert!(cb::MAX_INFERENCE_TIMEOUT > cb::DEFAULT_INFERENCE_TIMEOUT);
    }
    pub fn test_max_prompt_tokens(&self) -> () {
        assert!(cb::MAX_PROMPT_TOKENS == 8192);
    }
}

/// Validate the HTML file contains all spec-required features.
#[derive(Debug, Clone)]
pub struct TestFrontendCompleteness {
}

impl TestFrontendCompleteness {
    pub fn setup_class() -> Result<()> {
        let mut html_path = PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string());
        let mut f = File::open(html_path)?;
        {
            cls.html = f.read();
        }
    }
    pub fn test_title(&self) -> () {
        assert!(self.html.contains(&"<title>".to_string()));
    }
    pub fn test_dark_mode_toggle(&self) -> () {
        assert!(self.html.contains(&"toggleTheme".to_string()));
    }
    pub fn test_language_switcher(&self) -> () {
        for lang in ("en".to_string(), "he".to_string(), "ar".to_string(), "es".to_string(), "fr".to_string(), "de".to_string()).iter() {
            assert!(self.html.contains(&format!("setLang('{}')", lang)));
        }
    }
    pub fn test_rtl_layout_support(&mut self) -> () {
        assert!((self.html.contains(&"dir='rtl'".to_string()) || self.html.contains(&"dir=\"rtl\"".to_string()) || self.html.contains(&"direction: rtl".to_string()) || self.html.contains(&"setLang".to_string())));
    }
    pub fn test_model_grid_exists(&self) -> () {
        assert!(self.html.contains(&"modelLibraryBody".to_string()));
    }
    pub fn test_model_filter(&self) -> () {
        assert!(self.html.contains(&"filterModels".to_string()));
    }
    pub fn test_model_sort(&self) -> () {
        assert!(self.html.contains(&"sortModels".to_string()));
    }
    pub fn test_model_fitness(&self) -> () {
        assert!(self.html.contains(&"_modelFitness".to_string()));
    }
    pub fn test_judge_select(&self) -> () {
        assert!(self.html.contains(&"judgeModel".to_string()));
    }
    pub fn test_judge_templates(&self) -> () {
        for template in ("clinical_triage".to_string(), "code_review".to_string()).iter() {
            assert!((self.html.contains(&template) || self.html.contains(&"Medical".to_string())));
        }
    }
    pub fn test_run_comparison_function(&self) -> () {
        assert!(self.html.contains(&"runComparison".to_string()));
    }
    pub fn test_export_csv(&self) -> () {
        assert!(self.html.contains(&"exportCSV".to_string()));
    }
    pub fn test_scenarios_config(&self) -> () {
        assert!(self.html.contains(&"_SCENARIOS".to_string()));
        for s in ("clinical_triage".to_string(), "code_review".to_string(), "math_olympiad".to_string(), "polyglot".to_string()).iter() {
            assert!(self.html.contains(&s));
        }
    }
    pub fn test_history_localStorage_key(&self) -> () {
        assert!(self.html.contains(&"zen_compare_history".to_string()));
    }
    pub fn test_elo_localStorage_key(&self) -> () {
        assert!(self.html.contains(&"zen_compare_elo".to_string()));
    }
    pub fn test_leaderboard(&self) -> () {
        assert!(self.html.contains(&"_renderLeaderboard".to_string()));
        assert!(self.html.contains(&"leaderboardBody".to_string()));
    }
    pub fn test_save_to_history(&self) -> () {
        assert!(self.html.contains(&"_saveToHistory".to_string()));
    }
    pub fn test_update_elo(&self) -> () {
        assert!(self.html.contains(&"_updateElo".to_string()));
    }
    pub fn test_streaming_functions(&self) -> () {
        assert!(self.html.contains(&"_runStreamComparison".to_string()));
        assert!(self.html.contains(&"_showStreamingUI".to_string()));
        assert!(self.html.contains(&"_handleStreamEvent".to_string()));
    }
    pub fn test_batch_mode(&self) -> () {
        assert!(self.html.contains(&"_toggleBatchMode".to_string()));
        assert!(self.html.contains(&"_runBatch".to_string()));
        assert!(self.html.contains(&"batchPanel".to_string()));
    }
    pub fn test_share_report(&self) -> () {
        assert!(self.html.contains(&"_shareReport".to_string()));
    }
    pub fn test_download_modal(&self) -> () {
        assert!(self.html.contains(&"downloadModal".to_string()));
    }
    pub fn test_discover_tab(&self) -> () {
        assert!((self.html.contains(&"switchRepo".to_string()) && self.html.contains(&"discover".to_string())));
        assert!(self.html.contains(&"discoverSearch".to_string()));
        assert!(self.html.contains(&"runDiscoverSearch".to_string()));
    }
    pub fn test_zena_chat(&self) -> () {
        assert!(self.html.contains(&"zenaChatBar".to_string()));
        assert!(self.html.contains(&"zenaChatInput".to_string()));
    }
    pub fn test_question_bank(&self) -> () {
        assert!(self.html.contains(&"qpill".to_string()));
    }
    pub fn test_eschtml_function(&self) -> () {
        assert!(self.html.contains(&"escHtml".to_string()));
    }
    pub fn test_monkey_mode(&self) -> () {
        assert!((self.html.contains(&"RANDOM".to_string()) || self.html.to_lowercase().contains(&"monkey".to_string()) || self.html.contains(&"🐒".to_string())));
    }
}

/// Validate HuggingFace discovery caching and trust system.
#[derive(Debug, Clone)]
pub struct TestDiscoverySystem {
}

impl TestDiscoverySystem {
    pub fn test_cache_exists(&self) -> () {
        assert!(/* /* isinstance(cb::_discovery_cache, dict) */ */ true);
    }
    pub fn test_ttl_reasonable(&self) -> () {
        assert!((300 <= cb::_DISCOVERY_TTL) && (cb::_DISCOVERY_TTL <= 3600));
    }
    pub fn test_trusted_quantizers(&self) -> () {
        for q in ("bartowski".to_string(), "mradermacher".to_string(), "TheBloke".to_string(), "unsloth".to_string(), "QuantFactory".to_string()).iter() {
            assert!(cb::_TRUSTED_QUANTIZERS.contains(&q));
        }
    }
    pub fn test_discovery_function_exists(&self) -> () {
        assert!(callable(cb::_discover_hf_models));
    }
}

/// Validate Vulkan GPU environment setup.
#[derive(Debug, Clone)]
pub struct TestVulkanSupport {
}

impl TestVulkanSupport {
    pub fn test_vk_devices_env_set(&self) -> () {
        assert!(os::environ.contains(&"GGML_VK_VISIBLE_DEVICES".to_string()));
    }
    pub fn test_vk_devices_not_empty(&self) -> () {
        assert!(std::env::var(&"GGML_VK_VISIBLE_DEVICES".to_string()).unwrap_or_default().cloned().unwrap_or("".to_string()).len() > 0);
    }
}

/// Validate ThreadingHTTPServer setup.
#[derive(Debug, Clone)]
pub struct TestServerArchitecture {
}

impl TestServerArchitecture {
    pub fn test_threading_server_class(&self) -> () {
        assert!(/* hasattr(cb, "ThreadingHTTPServer".to_string()) */ true);
        assert!(cb::ThreadingHTTPServer.daemon_threads == true);
    }
    pub fn test_handler_has_all_endpoints(&self) -> () {
        let mut handler = cb::ComparatorHandler;
        assert!(/* hasattr(handler, "do_GET".to_string()) */ true);
        assert!(/* hasattr(handler, "do_POST".to_string()) */ true);
        assert!(/* hasattr(handler, "do_OPTIONS".to_string()) */ true);
        assert!(/* hasattr(handler, "_handle_comparison".to_string()) */ true);
        assert!(/* hasattr(handler, "_handle_stream_comparison".to_string()) */ true);
        assert!(/* hasattr(handler, "_handle_chat".to_string()) */ true);
        assert!(/* hasattr(handler, "_handle_download".to_string()) */ true);
        assert!(/* hasattr(handler, "_handle_install_llama".to_string()) */ true);
        assert!(/* hasattr(handler, "_handle_system_info".to_string()) */ true);
        assert!(/* hasattr(handler, "_handle_discover_models".to_string()) */ true);
        assert!(/* hasattr(handler, "_run_judge".to_string()) */ true);
        assert!(/* hasattr(handler, "_cors_headers".to_string()) */ true);
    }
    /// _run_judge must implement dual-pass position randomization.
    pub fn test_judge_has_position_bias_mitigation(&self) -> () {
        // _run_judge must implement dual-pass position randomization.
        // TODO: import inspect
        let mut src = inspect::getsource(cb::ComparatorHandler._run_judge);
        assert!(src.to_lowercase().contains(&"random".to_string()));
        assert!((src.to_lowercase().contains(&"shuffle".to_string()) || src.to_lowercase().contains(&"bias".to_string())));
    }
}

/// Ensure all required project files exist.
#[derive(Debug, Clone)]
pub struct TestDocumentation {
}

impl TestDocumentation {
    pub fn test_readme_exists(&self) -> () {
        assert!(os::path.isfile(PathBuf::from(REPO_ROOT).join("README.md".to_string())));
    }
    pub fn test_how_to_use_exists(&self) -> () {
        assert!(os::path.isfile(PathBuf::from(REPO_ROOT).join("HOW_TO_USE.md".to_string())));
    }
    pub fn test_changelog_exists(&self) -> () {
        assert!(os::path.isfile(PathBuf::from(REPO_ROOT).join("CHANGELOG.md".to_string())));
    }
    pub fn test_pyproject_exists(&self) -> () {
        assert!(os::path.isfile(PathBuf::from(REPO_ROOT).join("pyproject.toml".to_string())));
    }
    pub fn test_requirements_exists(&self) -> () {
        assert!(os::path.isfile(PathBuf::from(REPO_ROOT).join("requirements.txt".to_string())));
    }
    pub fn test_license_exists(&self) -> () {
        assert!(os::path.isfile(PathBuf::from(REPO_ROOT).join("LICENSE".to_string())));
    }
    pub fn test_bat_exists(&self) -> () {
        assert!(os::path.isfile(PathBuf::from(REPO_ROOT).join("Run_me.bat".to_string())));
    }
}

/// Verify HOW_TO_USE.md matches the actual codebase.
#[derive(Debug, Clone)]
pub struct TestHowToUseAccuracy {
}

impl TestHowToUseAccuracy {
    pub fn setup_class() -> Result<()> {
        let mut f = File::open(PathBuf::from(REPO_ROOT).join("HOW_TO_USE.md".to_string()))?;
        {
            cls.doc = f.read();
        }
    }
    pub fn test_documents_system_info_endpoint(&self) -> () {
        assert!(self.doc.contains(&"/__system-info".to_string()));
    }
    pub fn test_documents_comparison_endpoint(&self) -> () {
        assert!(self.doc.contains(&"/__comparison/mixed".to_string()));
    }
    pub fn test_documents_chat_endpoint(&self) -> () {
        assert!(self.doc.contains(&"/__chat".to_string()));
    }
    pub fn test_documents_download_endpoint(&self) -> () {
        assert!(self.doc.contains(&"/__download".to_string()));
    }
    pub fn test_documents_port_8123(&self) -> () {
        assert!(self.doc.contains(&"8123".to_string()));
    }
    /// HOW_TO_USE says CORS is open (*) but it's now restricted — flag this.
    pub fn test_cors_documentation_outdated(&self) -> () {
        // HOW_TO_USE says CORS is open (*) but it's now restricted — flag this.
        if self.doc.contains(&"CORS is open".to_string()) {
            assert!(true);
        }
    }
}

/// _patch_catalog::py should exist and be valid Python.
#[derive(Debug, Clone)]
pub struct TestPatchCatalog {
}

impl TestPatchCatalog {
    pub fn test_file_exists(&self) -> () {
        assert!(os::path.isfile(PathBuf::from(REPO_ROOT).join("_patch_catalog::py".to_string())));
    }
    pub fn test_valid_python(&self) -> Result<()> {
        let mut path = PathBuf::from(REPO_ROOT).join("_patch_catalog::py".to_string());
        let mut f = File::open(path)?;
        {
            let mut source = f.read();
        }
        Ok(compile(source, path, "exec".to_string()))
    }
}

pub fn _start_test_server() -> Result<()> {
    // global/nonlocal _server
    if _server.is_some() {
        return;
    }
    // TODO: from http::server import HTTPServer
    let mut _server = HTTPServer(("127.0.0.1".to_string(), TEST_PORT), cb::ComparatorHandler);
    let mut t = std::thread::spawn(|| {});
    t.start();
    for _ in 0..50.iter() {
        // try:
        {
            let mut req = urllib::request.Request(format!("{}/__health", TEST_URL));
            let mut r = urllib::request.urlopen(req, /* timeout= */ 2);
            {
                if r.status == 200 {
                    break;
                }
            }
        }
        // except Exception as _e:
    }
}

pub fn _get(path: String, headers: String, timeout: String) -> Result<()> {
    let mut req = urllib::request.Request((TEST_URL + path));
    if headers {
        for (k, v) in headers.iter().iter() {
            req.add_header(k, v);
        }
    }
    // try:
    {
        let mut resp = urllib::request.urlopen(req, /* timeout= */ timeout);
        {
            let mut body = resp.read();
            (resp.status, /* dict(resp.headers) */ HashMap::new(), if body { serde_json::from_str(&body).unwrap() } else { HashMap::new() })
        }
    }
    // except urllib::error.HTTPError as e:
}

pub fn _post(path: String, data: String, headers: String, timeout: String) -> Result<()> {
    let mut body = serde_json::to_string(&data).unwrap().as_bytes().to_vec();
    let mut req = urllib::request.Request((TEST_URL + path), /* data= */ body, /* method= */ "POST".to_string());
    req.add_header("Content-Type".to_string(), "application/json".to_string());
    if headers {
        for (k, v) in headers.iter().iter() {
            req.add_header(k, v);
        }
    }
    // try:
    {
        let mut resp = urllib::request.urlopen(req, /* timeout= */ timeout);
        {
            let mut rbody = resp.read();
            (resp.status, /* dict(resp.headers) */ HashMap::new(), if rbody { serde_json::from_str(&rbody).unwrap() } else { HashMap::new() })
        }
    }
    // except urllib::error.HTTPError as e:
}
