/// Completeness Audit Tests — Spec vs. Implementation
/// ====================================================
/// Validates every feature listed in LLM_COMPARE_2026.md and HOW_TO_USE.md
/// is actually implemented. No mocks — real function calls and HTTP requests.
/// 
/// Run:
/// pytest tests/test_completeness_audit::py -v --tb=short

use anyhow::{Result, Context};
use crate::comparator_backend as cb;
use regex::Regex;
use std::collections::HashMap;
use std::collections::HashSet;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::PathBuf;

pub static REPO_ROOT: std::sync::LazyLock<String /* os::path.dirname */> = std::sync::LazyLock::new(|| Default::default());

pub const TEST_PORT: i64 = 18126;

pub const TEST_URL: &str = "f'http://127.0.0.1:{TEST_PORT}";

pub static _SERVER: std::sync::LazyLock<Option<serde_json::Value>> = std::sync::LazyLock::new(|| None);

/// Validate comparison accepts multiple models (even if paths don't exist).
#[derive(Debug, Clone)]
pub struct TestParallelComparison {
}

impl TestParallelComparison {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_comparison_accepts_empty_model_list(&self) -> () {
        let (mut status, _, mut body) = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), "test".to_string()), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]));
        assert!(status == 200);
        assert!(body["models_tested".to_string()] == 0);
    }
    pub fn test_comparison_returns_responses_array(&self) -> () {
        let (mut status, _, mut body) = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), "test".to_string()), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]));
        assert!(/* /* isinstance(body.get(&"responses".to_string()).cloned(), list) */ */ true);
    }
    /// Prompts exceeding MAX_PROMPT_TOKENS must be rejected.
    pub fn test_comparison_prompt_too_large_rejected(&self) -> () {
        // Prompts exceeding MAX_PROMPT_TOKENS must be rejected.
        let mut huge_prompt = ("word ".to_string() * 20000);
        let (mut status, _, mut body) = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), huge_prompt), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]));
        assert!(status == 400);
        assert!((body.get(&"error".to_string()).cloned().unwrap_or("".to_string()).to_lowercase().contains(&"too large".to_string()) || body.get(&"error".to_string()).cloned().unwrap_or("".to_string()).to_lowercase().contains(&"token".to_string())));
    }
    pub fn test_comparison_returns_timestamp(&self) -> () {
        let (mut status, _, mut body) = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), "test".to_string()), ("local_models".to_string(), vec![]), ("online_models".to_string(), vec![])]));
        assert!(body.contains(&"timestamp".to_string()));
        assert!(/* /* isinstance(body["timestamp".to_string()], float) */ */ true);
    }
}

/// Additional edge cases for the 5-layer judge score extraction.
#[derive(Debug, Clone)]
pub struct TestJudgeScoreEdgeCases {
}

impl TestJudgeScoreEdgeCases {
    pub fn test_multiple_json_blocks_picks_first(&self) -> () {
        let mut raw = "```json\n{\"overall\": 9}\n```\nSome text\n```json\n{\"overall\": 3}\n```".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 9);
    }
    pub fn test_fractional_scores(&self) -> () {
        let mut raw = "{\"overall\": 7.3, \"accuracy\": 6.8}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 7.3_f64);
    }
    pub fn test_zero_is_valid_score(&self) -> () {
        let mut raw = "{\"overall\": 0}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 0);
    }
    pub fn test_ten_is_max_valid(&self) -> () {
        let mut raw = "{\"overall\": 10}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 10.0_f64);
    }
    pub fn test_score_with_trailing_text(&self) -> () {
        let mut raw = "{\"overall\": 8} This is quite good honestly".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result["overall".to_string()] == 8);
    }
    /// Regex patterns match 'accuracy: 7' but not 'accuracy is: 7' (known gap).
    pub fn test_extract_accuracy_from_nl(&self) -> () {
        // Regex patterns match 'accuracy: 7' but not 'accuracy is: 7' (known gap).
        let mut raw = "accuracy: 7 out of 10. reasoning: 8/10.".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result.contains(&"accuracy".to_string()));
        assert!(result["accuracy".to_string()] == 7.0_f64);
    }
    /// When no 'overall' key but other numeric scores exist, average them.
    pub fn test_score_averaging_when_no_overall(&self) -> () {
        // When no 'overall' key but other numeric scores exist, average them.
        let mut raw = "{\"accuracy\": 6, \"reasoning\": 8}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(result.contains(&"overall".to_string()));
        assert!(result["overall".to_string()] == 7.0_f64);
    }
    /// Triple-nested JSON should still extract scores.
    pub fn test_deeply_nested_ignored(&self) -> () {
        // Triple-nested JSON should still extract scores.
        let mut raw = "{\"eval\": {\"scores\": {\"overall\": 5}}}".to_string();
        let mut result = cb::extract_judge_scores(raw);
        assert!(/* /* isinstance(result, dict) */ */ true);
        assert!(result.contains(&"overall".to_string()));
    }
    pub fn test_whitespace_only_returns_zero(&self) -> () {
        let mut result = cb::extract_judge_scores("   \n\t  ".to_string());
        assert!(result == HashMap::from([("overall".to_string(), 0)]));
    }
}

/// Validate that _run_judge implements dual-pass scoring.
#[derive(Debug, Clone)]
pub struct TestJudgeBiasMitigation {
}

impl TestJudgeBiasMitigation {
    pub fn test_run_judge_source_has_two_passes(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler._run_judge);
        assert!(src.contains(&"user_msg_standard".to_string()));
        assert!(src.contains(&"user_msg_shuffled".to_string()));
    }
    pub fn test_run_judge_uses_random_shuffle(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler._run_judge);
        assert!(src.contains(&"random.shuffle".to_string()));
    }
    pub fn test_run_judge_averages_scores(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler._run_judge);
        assert!((src.contains(&"avg_score".to_string()) || src.to_lowercase().contains(&"average".to_string())));
    }
    pub fn test_run_judge_records_bias_passes(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler._run_judge);
        assert!(src.contains(&"bias_passes".to_string()));
    }
    pub fn test_run_judge_records_individual_scores(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler._run_judge);
        assert!(src.contains(&"individual_scores".to_string()));
    }
}

/// Validate the SSE protocol specifics.
#[derive(Debug, Clone)]
pub struct TestSSEStreamProtocol {
}

impl TestSSEStreamProtocol {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn _stream_post(&self, body_dict: String, read_timeout: String) -> Result<()> {
        // TODO: import http::client
        let mut conn = http::client.HTTPConnection("127.0.0.1".to_string(), TEST_PORT, /* timeout= */ read_timeout);
        let mut body = serde_json::to_string(&body_dict).unwrap();
        conn.request("POST".to_string(), "/__comparison/stream".to_string(), /* body= */ body, /* headers= */ HashMap::from([("Content-Type".to_string(), "application/json".to_string()), ("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        let mut resp = conn.getresponse();
        let mut status = resp.status;
        let mut headers = /* dict(resp.getheaders()) */ HashMap::new();
        let mut body_data = resp.read(16384).decode("utf-8".to_string(), /* errors= */ "replace".to_string());
        conn.close();
        Ok((status, headers, body_data))
    }
    pub fn test_stream_returns_200(&mut self) -> () {
        let (mut status, _, _) = self._stream_post(HashMap::from([("prompt".to_string(), "Hello".to_string()), ("local_models".to_string(), vec![])]));
        assert!(status == 200);
    }
    pub fn test_stream_content_type(&mut self) -> () {
        let (_, mut headers, _) = self._stream_post(HashMap::from([("prompt".to_string(), "Hello".to_string()), ("local_models".to_string(), vec![])]));
        assert!(headers.get(&"Content-Type".to_string()).cloned().unwrap_or("".to_string()).contains(&"text/event-stream".to_string()));
    }
    pub fn test_stream_no_cache(&mut self) -> () {
        let (_, mut headers, _) = self._stream_post(HashMap::from([("prompt".to_string(), "Hello".to_string()), ("local_models".to_string(), vec![])]));
        assert!(headers.get(&"Cache-Control".to_string()).cloned().unwrap_or("".to_string()).contains(&"no-cache".to_string()));
    }
    pub fn test_stream_contains_done_event(&mut self) -> () {
        let (_, _, mut body) = self._stream_post(HashMap::from([("prompt".to_string(), "Hello".to_string()), ("local_models".to_string(), vec![])]));
        assert!(body.contains(&"event: done".to_string()));
    }
    pub fn test_stream_done_payload_is_json(&mut self) -> Result<()> {
        let (_, _, mut body) = self._stream_post(HashMap::from([("prompt".to_string(), "Hello".to_string()), ("local_models".to_string(), vec![])]));
        for line in body.split("\n".to_string()).map(|s| s.to_string()).collect::<Vec<String>>().iter() {
            if (line.starts_with(&*"data:".to_string()) && line.contains(&"responses".to_string())) {
                // try:
                {
                    let mut data = serde_json::from_str(&line[5..].trim().to_string()).unwrap();
                }
                // except json::JSONDecodeError as _e:
                assert!(data.contains(&"responses".to_string()));
                break;
            }
        }
    }
    /// SSE should also reject oversized prompts.
    pub fn test_stream_rejects_oversized_prompt(&mut self) -> () {
        // SSE should also reject oversized prompts.
        let mut huge = ("word ".to_string() * 20000);
        let (mut status, _, mut body) = self._stream_post(HashMap::from([("prompt".to_string(), huge), ("local_models".to_string(), vec![])]));
        assert!(status == 400);
    }
}

/// Validate chat endpoint input validation.
#[derive(Debug, Clone)]
pub struct TestZenaChatEndpoint {
}

impl TestZenaChatEndpoint {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_chat_rejects_missing_model(&self) -> () {
        let (mut status, _, mut body) = _post("/__chat".to_string(), HashMap::from([("model_path".to_string(), "".to_string()), ("messages".to_string(), vec![HashMap::from([("role".to_string(), "user".to_string()), ("content".to_string(), "hi".to_string())])])]));
        assert!(status == 400);
    }
    pub fn test_chat_rejects_nonexistent_model(&self) -> () {
        let (mut status, _, mut body) = _post("/__chat".to_string(), HashMap::from([("model_path".to_string(), "C:\\nonexistent\\fake.gguf".to_string()), ("messages".to_string(), vec![HashMap::from([("role".to_string(), "user".to_string()), ("content".to_string(), "hi".to_string())])])]));
        assert!((400, 403).contains(&status));
    }
    pub fn test_chat_rejects_path_outside_model_dirs(&self) -> () {
        let (mut status, _, mut body) = _post("/__chat".to_string(), HashMap::from([("model_path".to_string(), "C:\\Windows\\System32\\cmd.exe".to_string()), ("messages".to_string(), vec![HashMap::from([("role".to_string(), "user".to_string()), ("content".to_string(), "hi".to_string())])])]));
        assert!((400, 403).contains(&status));
    }
}

/// Validate download endpoint input validation.
#[derive(Debug, Clone)]
pub struct TestDownloadEndpoint {
}

impl TestDownloadEndpoint {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_download_rejects_empty_model(&self) -> () {
        let (mut status, _, mut body) = _post("/__download-model".to_string(), HashMap::from([("model".to_string(), "".to_string()), ("dest".to_string(), "C:\\AI\\Models".to_string())]));
        assert!(status == 400);
    }
    /// A valid-looking model string should return a job_id (download itself may fail).
    pub fn test_download_returns_job_id(&self) -> () {
        // A valid-looking model string should return a job_id (download itself may fail).
        let (mut status, _, mut body) = _post("/__download-model".to_string(), HashMap::from([("model".to_string(), "bartowski/test-repo/test.gguf".to_string()), ("dest".to_string(), PathBuf::from(REPO_ROOT).join("test_download_temp".to_string()))]));
        assert!(status == 200);
        assert!(body.contains(&"job_id".to_string()));
    }
    pub fn test_download_status_unknown_job(&self) -> () {
        let (mut status, _, mut body) = _get("/__download-status?job=nonexistent".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(status == 200);
        assert!(body.get(&"state".to_string()).cloned() == "unknown".to_string());
    }
}

/// Validate install endpoint safety.
#[derive(Debug, Clone)]
pub struct TestInstallEndpoint {
}

impl TestInstallEndpoint {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    /// Only pip install llama-cpp-python should be allowed.
    pub fn test_install_rejects_arbitrary_commands(&self) -> () {
        // Only pip install llama-cpp-python should be allowed.
        let (mut status, _, mut body) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install malicious-package".to_string())]));
        assert!(status == 400);
        assert!((body.get(&"error".to_string()).cloned().unwrap_or("".to_string()).to_lowercase().contains(&"only".to_string()) || body.get(&"error".to_string()).cloned().unwrap_or("".to_string()).to_lowercase().contains(&"llama".to_string())));
    }
    pub fn test_install_status_unknown_job(&self) -> () {
        let (mut status, _, mut body) = _get("/__install-status?job=nonexistent".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(status == 200);
        assert!(body.get(&"state".to_string()).cloned() == "unknown".to_string());
    }
}

/// Validate discovery caching and trusted quantizers.
#[derive(Debug, Clone)]
pub struct TestDiscoveryDetails {
}

impl TestDiscoveryDetails {
    pub fn test_trusted_quantizers_complete(&self) -> () {
        let mut expected = HashSet::from(["bartowski".to_string(), "mradermacher".to_string(), "unsloth".to_string(), "TheBloke".to_string(), "QuantFactory".to_string()]);
        assert!(expected.issubset(cb::_TRUSTED_QUANTIZERS));
    }
    pub fn test_discovery_ttl_is_15_minutes(&self) -> () {
        assert!(cb::_DISCOVERY_TTL == 900);
    }
    pub fn test_discovery_function_returns_list(&self) -> () {
        let mut result = cb::_discover_hf_models(/* query= */ "".to_string(), /* sort= */ "trending".to_string(), /* limit= */ 1);
        assert!(/* /* isinstance(result, list) */ */ true);
    }
    pub fn test_discovery_cache_dict_exists(&self) -> () {
        assert!(/* /* isinstance(cb::_discovery_cache, dict) */ */ true);
    }
    pub fn test_discovery_endpoint_params(&self) -> () {
        _start_test_server();
        let (mut status, _, mut body) = _get("/__discover-models?q=test&sort=invalid_sort&limit=5".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(status == 200);
    }
}

/// Validate the HTML question bank has all 6 categories.
#[derive(Debug, Clone)]
pub struct TestQuestionBankCompleteness {
}

impl TestQuestionBankCompleteness {
    pub fn setup_class() -> Result<()> {
        let mut html_path = PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string());
        let mut f = File::open(html_path)?;
        {
            cls.html = f.read();
        }
    }
    pub fn test_ops_category(&self) -> () {
        assert!((self.html.to_lowercase().contains(&"ops".to_string()) || self.html.contains(&"Ops".to_string())));
    }
    pub fn test_emergency_category(&self) -> () {
        assert!((self.html.to_lowercase().contains(&"emergency".to_string()) || self.html.contains(&"Emergency".to_string())));
    }
    pub fn test_cardiology_category(&self) -> () {
        assert!((self.html.to_lowercase().contains(&"cardiology".to_string()) || self.html.contains(&"Cardiology".to_string())));
    }
    pub fn test_coding_category(&self) -> () {
        assert!((self.html.to_lowercase().contains(&"coding".to_string()) || self.html.contains(&"Coding".to_string())));
    }
    pub fn test_reasoning_category(&self) -> () {
        assert!((self.html.to_lowercase().contains(&"reasoning".to_string()) || self.html.contains(&"Reasoning".to_string())));
    }
    pub fn test_multilingual_category(&self) -> () {
        assert!((self.html.to_lowercase().contains(&"multilingual".to_string()) || self.html.contains(&"Multilingual".to_string())));
    }
}

/// Validate internationalization and theme support.
#[derive(Debug, Clone)]
pub struct TestI18nAndTheme {
}

impl TestI18nAndTheme {
    pub fn setup_class() -> Result<()> {
        let mut html_path = PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string());
        let mut f = File::open(html_path)?;
        {
            cls.html = f.read();
        }
    }
    pub fn test_dark_mode_class_toggle(&self) -> () {
        assert!(self.html.contains(&"toggleTheme".to_string()));
    }
    pub fn test_dark_mode_localstorage(&self) -> () {
        assert!((self.html.contains(&"localStorage".to_string()) && (self.html.to_lowercase().contains(&"theme".to_string()) || self.html.to_lowercase().contains(&"dark".to_string()))));
    }
    pub fn test_language_english(&self) -> () {
        assert!(self.html.contains(&"setLang('en')".to_string()));
    }
    pub fn test_language_hebrew(&self) -> () {
        assert!(self.html.contains(&"setLang('he')".to_string()));
    }
    pub fn test_language_arabic(&self) -> () {
        assert!(self.html.contains(&"setLang('ar')".to_string()));
    }
    pub fn test_language_spanish(&self) -> () {
        assert!(self.html.contains(&"setLang('es')".to_string()));
    }
    pub fn test_language_french(&self) -> () {
        assert!(self.html.contains(&"setLang('fr')".to_string()));
    }
    pub fn test_language_german(&self) -> () {
        assert!(self.html.contains(&"setLang('de')".to_string()));
    }
    pub fn test_rtl_support_for_hebrew_arabic(&self) -> () {
        assert!(self.html.to_lowercase().contains(&"rtl".to_string()));
    }
}

/// Validate spec-required advanced features exist in frontend.
#[derive(Debug, Clone)]
pub struct TestAdvancedFeatures {
}

impl TestAdvancedFeatures {
    pub fn setup_class() -> Result<()> {
        let mut html_path = PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string());
        let mut f = File::open(html_path)?;
        {
            cls.html = f.read();
        }
    }
    pub fn test_scenario_clinical_triage(&self) -> () {
        assert!((self.html.contains(&"clinical_triage".to_string()) || self.html.to_lowercase().contains(&"clinical".to_string())));
    }
    pub fn test_scenario_code_review(&self) -> () {
        assert!(self.html.contains(&"code_review".to_string()));
    }
    pub fn test_scenario_math_olympiad(&self) -> () {
        assert!((self.html.contains(&"math_olympiad".to_string()) || self.html.contains(&"logic_duel".to_string())));
    }
    pub fn test_scenario_polyglot(&self) -> () {
        assert!(self.html.contains(&"polyglot".to_string()));
    }
    pub fn test_batch_panel_exists(&self) -> () {
        assert!(self.html.contains(&"batchPanel".to_string()));
    }
    pub fn test_batch_run_function(&self) -> () {
        assert!(self.html.contains(&"_runBatch".to_string()));
    }
    pub fn test_csv_export_function(&self) -> () {
        assert!(self.html.contains(&"exportCSV".to_string()));
    }
    pub fn test_share_report_function(&self) -> () {
        assert!(self.html.contains(&"_shareReport".to_string()));
    }
    pub fn test_elo_update_function(&self) -> () {
        assert!(self.html.contains(&"_updateElo".to_string()));
    }
    pub fn test_leaderboard_render_function(&self) -> () {
        assert!(self.html.contains(&"_renderLeaderboard".to_string()));
    }
    pub fn test_history_save_function(&self) -> () {
        assert!(self.html.contains(&"_saveToHistory".to_string()));
    }
    pub fn test_monkey_mode_button(&self) -> () {
        assert!((self.html.contains(&"RANDOM".to_string()) || self.html.contains(&"🐒".to_string())));
    }
    pub fn test_xss_escape_function(&self) -> () {
        assert!(self.html.contains(&"escHtml".to_string()));
    }
}

/// Validate security constraints.
#[derive(Debug, Clone)]
pub struct TestServerSecurity {
}

impl TestServerSecurity {
    pub fn test_server_binds_localhost(&self) -> () {
        let mut src = inspect::getsource(cb::run_server);
        assert!(src.contains(&"127.0.0.1".to_string()));
    }
    pub fn test_no_wildcard_bind(&self) -> () {
        let mut src = inspect::getsource(cb::run_server);
        assert!(!src.contains(&"0.0.0.0".to_string()));
    }
    pub fn test_install_only_allows_llama_cpp(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler._handle_install_llama);
        assert!(src.contains(&"llama-cpp-python".to_string()));
    }
    pub fn test_model_path_validation_in_comparison(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler._handle_comparison);
        assert!(src.contains(&"_is_safe_model_path".to_string()));
    }
    pub fn test_model_path_validation_in_chat(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler._handle_chat);
        assert!(src.contains(&"_is_safe_model_path".to_string()));
    }
}

/// Validate hardware detection integration via HTTP endpoint.
#[derive(Debug, Clone)]
pub struct TestHardwareDetectionIntegration {
}

impl TestHardwareDetectionIntegration {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_system_info_has_cpu_brand(&self) -> () {
        let (_, _, mut body) = _get("/__system-info".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(body.contains(&"cpu_brand".to_string()));
    }
    pub fn test_system_info_has_gpu_list(&self) -> () {
        let (_, _, mut body) = _get("/__system-info".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(/* /* isinstance(body.get(&"gpus".to_string()).cloned(), list) */ */ true);
    }
    pub fn test_system_info_has_memory(&self) -> () {
        let (_, _, mut body) = _get("/__system-info".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(body.get(&"memory_gb".to_string()).cloned().unwrap_or(0) > 0);
    }
    pub fn test_system_info_has_recommended_build(&self) -> () {
        let (_, _, mut body) = _get("/__system-info".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        let mut rec = body.get(&"recommended_build".to_string()).cloned().unwrap_or(HashMap::new());
        assert!(rec.contains(&"build".to_string()));
        assert!(rec.contains(&"pip".to_string()));
    }
    pub fn test_system_info_has_llama_cpp_status(&self) -> () {
        let (_, _, mut body) = _get("/__system-info".to_string(), HashMap::from([("Origin".to_string(), "http://127.0.0.1:8123".to_string())]));
        assert!(body.contains(&"has_llama_cpp".to_string()));
        assert!(/* /* isinstance(body["has_llama_cpp".to_string()], bool) */ */ true);
    }
}

/// Validate rate limiting is enforced on heavy POST endpoints.
#[derive(Debug, Clone)]
pub struct TestRateLimitingHTTP {
}

impl TestRateLimitingHTTP {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_rate_limit_source_enforced_on_comparison(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler.do_POST);
        assert!(src.contains(&"_rate_limiter".to_string()));
        assert!((src.contains(&"429".to_string()) || src.contains(&"Too many".to_string())));
    }
    pub fn test_rate_limit_applies_to_chat(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler.do_POST);
        assert!(src.contains(&"/__chat".to_string()));
    }
    pub fn test_rate_limit_applies_to_stream(&self) -> () {
        let mut src = inspect::getsource(cb::ComparatorHandler.do_POST);
        assert!(src.contains(&"/__comparison/stream".to_string()));
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
    req.add_header("Origin".to_string(), "http://127.0.0.1:8123".to_string());
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
