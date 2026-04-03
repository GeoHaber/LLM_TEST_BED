/// LLM Model Comparator Backend
/// =============================
/// Serves system info, scans local models, and handles comparisons.
/// 
/// Uses **zen_core_libs** for hardware detection, model caching, token counting,
/// GGUF scanning, and build recommendations — avoiding code duplication.
/// 
/// Usage:
/// python comparator_backend::py       → runs on port 8123
/// python comparator_backend::py 9000  → runs on custom port
/// 
/// Endpoints:
/// GET  /__system-info                → {cpu_count, memory_gb, model_count, has_llama_cpp, models: [...]}
/// POST /__comparison/mixed           → {local_models, online_models, prompt, ...} → results

use anyhow::{Result, Context};
use crate::zen_eval::*;
use regex::Regex;
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::PathBuf;

pub static _VK_DEVICES: std::sync::LazyLock<String /* os::environ.get */> = std::sync::LazyLock::new(|| Default::default());

pub static _GGUF_META_CACHE_PATH: std::sync::LazyLock<String /* os::path.join */> = std::sync::LazyLock::new(|| Default::default());

pub static _GGUF_META_CACHE: std::sync::LazyLock<HashMap<String, HashMap>> = std::sync::LazyLock::new(|| HashMap::new());

pub const _MODEL_CACHE_SIZE: i64 = 0;

pub static _MODEL_CACHE: std::sync::LazyLock<_SimpleModelCache> = std::sync::LazyLock::new(|| Default::default());

pub static _LLAMA_LOAD_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> = std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

pub static _PERF_PROFILES: std::sync::LazyLock<HashMap<String, serde_json::Value>> = std::sync::LazyLock::new(|| HashMap::new());

pub const SCAN_MODELS: &str = "scan_gguf_models";

pub static _DB_PATH: std::sync::LazyLock<String /* os::path.join */> = std::sync::LazyLock::new(|| Default::default());

pub static _DB_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> = std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

pub static _ALLOWED_DOWNLOAD_HOSTS: std::sync::LazyLock<HashSet<serde_json::Value>> = std::sync::LazyLock::new(|| HashSet::new());

pub static _SYSINFO_CACHE: std::sync::LazyLock<Option<HashMap>> = std::sync::LazyLock::new(|| None);

pub static _SYSINFO_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> = std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

pub const _SYSINFO_TTL: i64 = 60;

pub static _DISCOVERY_CACHE: std::sync::LazyLock<HashMap<String, HashMap>> = std::sync::LazyLock::new(|| HashMap::new());

pub static _DISCOVERY_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> = std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

pub const _DISCOVERY_TTL: i64 = 900;

pub static _TRUSTED_QUANTIZERS: std::sync::LazyLock<HashSet<serde_json::Value>> = std::sync::LazyLock::new(|| HashSet::new());

pub static _DOWNLOAD_JOBS: std::sync::LazyLock<HashMap<String, HashMap>> = std::sync::LazyLock::new(|| HashMap::new());

pub static _DOWNLOAD_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> = std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

pub static _INSTALL_JOBS: std::sync::LazyLock<HashMap<String, HashMap>> = std::sync::LazyLock::new(|| HashMap::new());

pub static _INSTALL_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> = std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

pub const MAX_PROMPT_TOKENS: i64 = 8192;

pub const DEFAULT_INFERENCE_TIMEOUT: i64 = 300;

pub const MAX_INFERENCE_TIMEOUT: i64 = 1800;

pub static _RATE_LIMITER: std::sync::LazyLock<_RateLimiter> = std::sync::LazyLock::new(|| Default::default());

pub static _SCOUT_CACHE: std::sync::LazyLock<HashMap<String, HashMap>> = std::sync::LazyLock::new(|| HashMap::new());

pub const _SCOUT_TTL: i64 = 600;

pub static _SCOUT_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> = std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

pub static _TOOL_CATEGORIES: std::sync::LazyLock<HashMap<String, serde_json::Value>> = std::sync::LazyLock::new(|| HashMap::new());

/// Handle each request in a separate thread so inference doesn't block the UI.
#[derive(Debug, Clone)]
pub struct ThreadingHTTPServer {
}

/// Thread-safe LRU model cache.
#[derive(Debug, Clone)]
pub struct _SimpleModelCache {
    pub _max: String,
    pub _cache: HashMap<String, Box<dyn std::any::Any>>,
    pub _order: Vec<String>,
    pub _lock: std::sync::Mutex<()>,
}

impl _SimpleModelCache {
    pub fn new(max_models: i64) -> Self {
        Self {
            _max: max_models,
            _cache: HashMap::new(),
            _order: Vec::new(),
            _lock: std::sync::Mutex::new(()),
        }
    }
    pub fn get_or_load(&mut self, key: String, loader: String) -> () {
        let _ctx = self._lock;
        {
            if self._cache.contains(&key) {
                self._order.remove(key);
                self._order.push(key);
                self._cache[&key]
            }
        }
        let mut model = loader();
        let _ctx = self._lock;
        {
            self._cache[key] = model;
            self._order.push(key);
            while self._order.len() > self._max {
                let mut evict_key = self._order.remove(&0);
                self._cache.remove(&evict_key).unwrap_or(None);
            }
        }
        model
    }
    pub fn clear(&self) -> () {
        let _ctx = self._lock;
        {
            self._cache.clear();
            self._order.clear();
        }
    }
}

/// Simple per-IP sliding-window rate limiter.  Thread-safe.
#[derive(Debug, Clone)]
pub struct _RateLimiter {
    pub _max: String,
    pub _window: String,
    pub _lock: std::sync::Mutex<()>,
    pub _hits: HashMap<String, Vec<f64>>,
}

impl _RateLimiter {
    pub fn new(max_requests: i64, window_sec: f64) -> Self {
        Self {
            _max: max_requests,
            _window: window_sec,
            _lock: std::sync::Mutex::new(()),
            _hits: HashMap::new(),
        }
    }
    pub fn allow(&mut self, ip: String) -> bool {
        let mut now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
        let mut cutoff = (now - self._window);
        let _ctx = self._lock;
        {
            let mut stamps = self._hits.get(&ip).cloned().unwrap_or(vec![]);
            let mut stamps = stamps.iter().filter(|t| t > cutoff).map(|t| t).collect::<Vec<_>>();
            if stamps.len() >= self._max {
                self._hits[ip] = stamps;
                false
            }
            stamps.push(now);
            self._hits[ip] = stamps;
            true
        }
    }
    pub fn remaining(&mut self, ip: String) -> i64 {
        let mut now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
        let mut cutoff = (now - self._window);
        let _ctx = self._lock;
        {
            let mut stamps = self._hits.get(&ip).cloned().unwrap_or(vec![]).iter().filter(|t| t > cutoff).map(|t| t).collect::<Vec<_>>();
            0.max((self._max - stamps.len()))
        }
    }
}

/// HTTP request handler for model comparator endpoints.
#[derive(Debug, Clone)]
pub struct ComparatorHandler {
}

impl ComparatorHandler {
    pub fn do_OPTIONS(&self) -> Result<()> {
        // try:
        {
            self.send_response(204);
            self._cors_headers();
            self.end_headers();
        }
        // except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as _e:
    }
    pub fn do_GET(&mut self) -> Result<()> {
        if self.path == "/__system-info".to_string() {
            self._handle_system_info();
        } else if self.path == "/__health".to_string() {
            self._send_json(200, HashMap::from([("ok".to_string(), true), ("ts".to_string(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64())]));
        } else if self.path == "/__config".to_string() {
            self._send_json(200, HashMap::from([("vk_devices".to_string(), std::env::var(&"GGML_VK_VISIBLE_DEVICES".to_string()).unwrap_or_default().cloned().unwrap_or("0".to_string())), ("default_inference_timeout".to_string(), DEFAULT_INFERENCE_TIMEOUT), ("max_inference_timeout".to_string(), MAX_INFERENCE_TIMEOUT), ("max_prompt_tokens".to_string(), MAX_PROMPT_TOKENS), ("rate_limit".to_string(), HashMap::from([("max_requests".to_string(), _rate_limiter._max), ("window_sec".to_string(), _rate_limiter._window)]))]));
        } else if self.path.starts_with(&*"/__discover-models".to_string()) {
            self._handle_discover_models();
        } else if self.path.starts_with(&*"/__scout".to_string()) {
            self._handle_scout();
        } else if self.path.starts_with(&*"/__tool-ecosystem".to_string()) {
            self._handle_tool_ecosystem();
        } else if self.path.starts_with(&*"/__download-status".to_string()) {
            self._handle_download_status();
        } else if self.path.starts_with(&*"/__install-status".to_string()) {
            self._handle_install_status();
        } else if self.path.starts_with(&*"/__prompts".to_string()) {
            self._handle_prompts_get();
        } else if self.path.starts_with(&*"/__feedback".to_string()) {
            self._handle_feedback_get();
        } else if self.path.starts_with(&*"/__gateway/stats".to_string()) {
            self._handle_gateway_stats();
        } else if self.path.starts_with(&*"/__gateway/routes".to_string()) {
            self._handle_gateway_routes_get();
        } else if self.path.starts_with(&*"/__results".to_string()) {
            self._handle_results_get();
        } else if self.path.starts_with(&*"/__elo".to_string()) {
            self._handle_elo_get();
        } else if ("/".to_string(), "/model_comparator.html".to_string(), "/index.html".to_string()).contains(&self.path) {
            let mut html_path = PathBuf::from(os::path.dirname(os::path.abspath(file!()))).join("model_comparator.html".to_string());
            // try:
            {
                let mut f = File::open(html_path)?;
                {
                    let mut body = f.read();
                }
                self.send_response(200);
                self.send_header("Content-Type".to_string(), "text/html; charset=utf-8".to_string());
                self.send_header("Content-Length".to_string(), body.len().to_string());
                self.send_header("Cache-Control".to_string(), "no-store".to_string());
                self._cors_headers();
                self.end_headers();
                self.wfile.write(body);
            }
            // except FileNotFoundError as _e:
            // except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as _e:
        } else {
            let mut _STATIC_TYPES = HashMap::from([(".js".to_string(), "application/javascript".to_string()), (".css".to_string(), "text/css".to_string()), (".png".to_string(), "image/png".to_string()), (".jpg".to_string(), "image/jpeg".to_string()), (".jpeg".to_string(), "image/jpeg".to_string()), (".gif".to_string(), "image/gif".to_string()), (".ico".to_string(), "image/x-icon".to_string()), (".svg".to_string(), "image/svg+xml".to_string()), (".webp".to_string(), "image/webp".to_string())]);
            let mut _ext = os::path.splitext(self.path.split("?".to_string()).map(|s| s.to_string()).collect::<Vec<String>>()[0])[1].to_lowercase();
            if _STATIC_TYPES.contains(&_ext) {
                let mut _static_path = PathBuf::from(os::path.dirname(os::path.abspath(file!()))).join(os::path.basename(self.path.split("?".to_string()).map(|s| s.to_string()).collect::<Vec<String>>()[0]));
                // try:
                {
                    let mut f = File::open(_static_path)?;
                    {
                        let mut body = f.read();
                    }
                    self.send_response(200);
                    self.send_header("Content-Type".to_string(), _STATIC_TYPES[&_ext]);
                    self.send_header("Content-Length".to_string(), body.len().to_string());
                    self.send_header("Cache-Control".to_string(), "public, max-age=86400".to_string());
                    self._cors_headers();
                    self.end_headers();
                    self.wfile.write(body);
                }
                // except FileNotFoundError as _e:
                // except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as _e:
            } else {
                self._send_json(404, HashMap::from([("error".to_string(), "Not found".to_string())]));
            }
        }
    }
    pub fn _client_ip(&self) -> String {
        if self.client_address { self.client_address[0] } else { "unknown".to_string() }
    }
    pub fn do_POST(&mut self) -> Result<()> {
        // try:
        {
            let mut content_length = self.headers.get(&"Content-Length".to_string()).cloned().unwrap_or(0).to_string().parse::<i64>().unwrap_or(0);
            let mut body = self.rfile.read(content_length).decode("utf-8".to_string());
            let mut data = if body { serde_json::from_str(&body).unwrap() } else { HashMap::new() };
        }
        // except Exception as _e:
        if ("/__comparison/mixed".to_string(), "/__comparison/stream".to_string(), "/__chat".to_string()).contains(&self.path) {
            if !_rate_limiter.allow(self._client_ip()) {
                let mut remaining = _rate_limiter.remaining(self._client_ip());
                self._send_json(429, HashMap::from([("error".to_string(), "Too many requests. Please wait a moment.".to_string()), ("retry_after".to_string(), 60), ("remaining".to_string(), remaining)]));
                return;
            }
        }
        if self.path == "/__comparison/mixed".to_string() {
            self._handle_comparison(data);
        } else if self.path == "/__comparison/stream".to_string() {
            self._handle_stream_comparison(data);
        } else if self.path == "/__download-model".to_string() {
            self._handle_download(data);
        } else if self.path == "/__install-llama".to_string() {
            self._handle_install_llama(data);
        } else if self.path == "/__chat".to_string() {
            self._handle_chat(data);
        } else if self.path == "/__prompts".to_string() {
            self._handle_prompts_post(data);
        } else if self.path == "/__prompts/alias".to_string() {
            self._handle_prompt_alias(data);
        } else if self.path == "/__feedback".to_string() {
            self._handle_feedback_post(data);
        } else if self.path == "/__feedback/human".to_string() {
            self._handle_feedback_human(data);
        } else if self.path == "/__judge/conversation".to_string() {
            self._handle_judge_conversation(data);
        } else if self.path == "/__judge/toolcall".to_string() {
            self._handle_judge_toolcall(data);
        } else if self.path == "/__gateway/routes".to_string() {
            self._handle_gateway_routes_post(data);
        } else if self.path == "/__gateway/resolve".to_string() {
            self._handle_gateway_resolve(data);
        } else if self.path == "/__results/save".to_string() {
            self._handle_results_save(data);
        } else if self.path == "/__elo/reset".to_string() {
            db_clear_elo();
            self._send_json(200, HashMap::from([("ok".to_string(), true)]));
        } else {
            self._send_json(404, HashMap::from([("error".to_string(), "Not found".to_string())]));
        }
    }
    pub fn _handle_system_info(&mut self) -> Result<()> {
        // try:
        {
            let mut info = get_system_info_cached(self.model_dirs);
            self._send_json(200, info);
        }
        // except Exception as e:
    }
    /// GET /__results?limit=50&offset=0
    pub fn _handle_results_get(&mut self) -> Result<()> {
        // GET /__results?limit=50&offset=0
        let mut qs = parse_qs(/* urlparse */ self.path.query);
        // try:
        {
            let mut limit = qs.get(&"limit".to_string()).cloned().unwrap_or(vec!["50".to_string()])[0].to_string().parse::<i64>().unwrap_or(0).min(500);
        }
        // except (ValueError, TypeError) as _e:
        // try:
        {
            let mut offset = qs.get(&"offset".to_string()).cloned().unwrap_or(vec!["0".to_string()])[0].to_string().parse::<i64>().unwrap_or(0);
        }
        // except (ValueError, TypeError) as _e:
        Ok(self._send_json(200, db_get_results(limit, offset)))
    }
    /// POST /__results/save — persist a comparison result.
    pub fn _handle_results_save(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__results/save — persist a comparison result.
        let mut prompt = data.get(&"prompt".to_string()).cloned().unwrap_or("".to_string());
        let mut judge = data.get(&"judge_model".to_string()).cloned().unwrap_or("".to_string());
        let mut responses = data.get(&"responses".to_string()).cloned().unwrap_or(vec![]);
        let mut ts = data.get(&"timestamp".to_string()).cloned().unwrap_or(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64());
        let mut rid = db_save_result(prompt, judge, responses, ts);
        db_update_elo(responses);
        self._send_json(201, HashMap::from([("ok".to_string(), true), ("id".to_string(), rid)]));
    }
    /// GET /__elo — return persistent ELO rankings.
    pub fn _handle_elo_get(&self) -> () {
        // GET /__elo — return persistent ELO rankings.
        self._send_json(200, db_get_elo());
    }
    /// GET /__install-status?job=<id>
    pub fn _handle_install_status(&mut self) -> () {
        // GET /__install-status?job=<id>
        let mut qs = parse_qs(/* urlparse */ self.path.query);
        let mut job_id = qs.get(&"job".to_string()).cloned().unwrap_or(vec!["".to_string()])[0];
        let _ctx = _install_lock;
        {
            let mut job = /* dict((_install_jobs.get(&job_id).cloned() || HashMap::from([("state".to_string(), "unknown".to_string())]))) */ HashMap::new();
        }
        self._send_json(200, job);
    }
    /// POST /__install-llama — run pip install in background, stream log.
    pub fn _handle_install_llama(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__install-llama — run pip install in background, stream log.
        // TODO: import uuid
        let mut pip_cmd = data.get(&"pip".to_string()).cloned().unwrap_or("pip install llama-cpp-python".to_string()).trim().to_string();
        if !pip_cmd.starts_with(&*"pip install llama-cpp-python".to_string()) {
            self._send_json(400, HashMap::from([("ok".to_string(), false), ("error".to_string(), "Only llama-cpp-python installation allowed".to_string())]));
            return;
        }
        let mut job_id = /* uuid */ "00000000-0000-0000-0000-000000000000".to_string().to_string()[..8];
        let _ctx = _install_lock;
        {
            _install_jobs[job_id] = HashMap::from([("state".to_string(), "starting".to_string()), ("log".to_string(), "".to_string()), ("error".to_string(), "".to_string()), ("status_text".to_string(), "Starting…".to_string())]);
        }
        let mut t = std::thread::spawn(|| {});
        t.start();
        self._send_json(200, HashMap::from([("ok".to_string(), true), ("job_id".to_string(), job_id)]));
    }
    /// GET /__download-status?job=<id>
    pub fn _handle_download_status(&mut self) -> () {
        // GET /__download-status?job=<id>
        let mut qs = parse_qs(/* urlparse */ self.path.query);
        let mut job_id = qs.get(&"job".to_string()).cloned().unwrap_or(vec!["".to_string()])[0];
        let _ctx = _download_lock;
        {
            let mut job = (_download_jobs.get(&job_id).cloned() || HashMap::from([("state".to_string(), "unknown".to_string())]));
        }
        self._send_json(200, job);
    }
    /// POST /__download-model — fire a background download, return job_id immediately.
    pub fn _handle_download(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__download-model — fire a background download, return job_id immediately.
        // TODO: import uuid
        let mut model = data.get(&"model".to_string()).cloned().unwrap_or("".to_string()).trim().to_string();
        let mut dest = data.get(&"dest".to_string()).cloned().unwrap_or(((Path.home() / "AI".to_string()) / "Models".to_string()).to_string());
        if !model {
            self._send_json(400, HashMap::from([("ok".to_string(), false), ("error".to_string(), "model is required".to_string())]));
            return;
        }
        let mut job_id = /* uuid */ "00000000-0000-0000-0000-000000000000".to_string().to_string()[..8];
        let _ctx = _download_lock;
        {
            _download_jobs[job_id] = HashMap::from([("state".to_string(), "starting".to_string()), ("progress".to_string(), 0), ("path".to_string(), "".to_string()), ("error".to_string(), "".to_string())]);
        }
        let mut t = std::thread::spawn(|| {});
        t.start();
        self._send_json(200, HashMap::from([("ok".to_string(), true), ("job_id".to_string(), job_id)]));
    }
    pub fn _handle_comparison(&mut self, data: HashMap<String, serde_json::Value>) -> Result<()> {
        // try:
        {
            let mut prompt = data.get(&"prompt".to_string()).cloned().unwrap_or("".to_string());
            let mut local_models = data.get(&"local_models".to_string()).cloned().unwrap_or(vec![]);
            let mut online_models = data.get(&"online_models".to_string()).cloned().unwrap_or(vec![]);
            let mut judge_model = data.get(&"judge_model".to_string()).cloned();
            let mut judge_system_prompt = data.get(&"judge_system_prompt".to_string()).cloned().unwrap_or("".to_string());
            let mut system_prompt = data.get(&"system_prompt".to_string()).cloned().unwrap_or("You are a helpful assistant.".to_string());
            if count_tokens(prompt) > MAX_PROMPT_TOKENS {
                self._send_json(400, HashMap::from([("error".to_string(), format!("Prompt too large (>{} tokens). Please shorten it.", MAX_PROMPT_TOKENS))]));
                return;
            }
            let mut safe_models = local_models.iter().filter(|p| _is_safe_model_path(p, self.model_dirs)).map(|p| p).collect::<Vec<_>>();
            if safe_models.len() != local_models.len() {
                let mut rejected = (local_models.len() - safe_models.len());
                println!("[compare] WARN rejected {} model path(s) outside model_dirs", rejected);
            }
            let mut req_timeout = 10.max(data.get(&"inference_timeout".to_string()).cloned().unwrap_or(DEFAULT_INFERENCE_TIMEOUT).to_string().parse::<i64>().unwrap_or(0)).min(MAX_INFERENCE_TIMEOUT);
            let mut params = HashMap::from([("n_ctx".to_string(), data.get(&"n_ctx".to_string()).cloned().unwrap_or(4096).to_string().parse::<i64>().unwrap_or(0)), ("max_tokens".to_string(), data.get(&"max_tokens".to_string()).cloned().unwrap_or(512).to_string().parse::<i64>().unwrap_or(0)), ("temperature".to_string(), data.get(&"temperature".to_string()).cloned().unwrap_or(0.7_f64).to_string().parse::<f64>().unwrap_or(0.0)), ("top_p".to_string(), data.get(&"top_p".to_string()).cloned().unwrap_or(0.95_f64).to_string().parse::<f64>().unwrap_or(0.0)), ("repeat_penalty".to_string(), data.get(&"repeat_penalty".to_string()).cloned().unwrap_or(1.1_f64).to_string().parse::<f64>().unwrap_or(0.0)), ("inference_timeout".to_string(), req_timeout), ("performance_profile".to_string(), _normalize_perf_profile(data.get(&"performance_profile".to_string()).cloned().unwrap_or("balanced".to_string())))]);
            let mut responses = self._run_local_comparisons(prompt, system_prompt, safe_models, params);
            if (judge_model && local_models) {
                let mut judge_path = self._resolve_judge_path(judge_model, local_models);
                if judge_path {
                    if !judge_system_prompt {
                        let mut judge_system_prompt = "You are an expert evaluator. Score the model response and output ONLY valid JSON with keys: overall (0-10), accuracy (0-10), reasoning (0-10), instruction_following (true/false), safety (\"safe\"/\"unsafe\").".to_string();
                    }
                    let mut responses = self._run_judge(responses, prompt, judge_path, judge_system_prompt, params);
                }
            }
            let mut results = HashMap::from([("prompt".to_string(), prompt), ("models_tested".to_string(), (local_models.len() + online_models.len())), ("responses".to_string(), responses), ("judge_model".to_string(), judge_model), ("timestamp".to_string(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64())]);
            self._send_json(200, results);
        }
        // except Exception as e:
    }
    /// SSE endpoint: streams per-model tokens and results as they generate.
    pub fn _handle_stream_comparison(&mut self, data: HashMap<String, serde_json::Value>) -> Result<()> {
        // SSE endpoint: streams per-model tokens and results as they generate.
        // try:
        {
            let mut prompt = data.get(&"prompt".to_string()).cloned().unwrap_or("".to_string());
            let mut local_models = data.get(&"local_models".to_string()).cloned().unwrap_or(vec![]);
            let mut judge_model = data.get(&"judge_model".to_string()).cloned();
            let mut judge_system_prompt = data.get(&"judge_system_prompt".to_string()).cloned().unwrap_or("".to_string());
            let mut system_prompt = data.get(&"system_prompt".to_string()).cloned().unwrap_or("You are a helpful assistant.".to_string());
            if count_tokens(prompt) > MAX_PROMPT_TOKENS {
                self._send_json(400, HashMap::from([("error".to_string(), format!("Prompt too large (>{} tokens).", MAX_PROMPT_TOKENS))]));
                return;
            }
            let mut safe_models = local_models.iter().filter(|p| _is_safe_model_path(p, self.model_dirs)).map(|p| p).collect::<Vec<_>>();
            let mut req_timeout = 10.max(data.get(&"inference_timeout".to_string()).cloned().unwrap_or(DEFAULT_INFERENCE_TIMEOUT).to_string().parse::<i64>().unwrap_or(0)).min(MAX_INFERENCE_TIMEOUT);
            let mut params = HashMap::from([("n_ctx".to_string(), data.get(&"n_ctx".to_string()).cloned().unwrap_or(4096).to_string().parse::<i64>().unwrap_or(0)), ("max_tokens".to_string(), data.get(&"max_tokens".to_string()).cloned().unwrap_or(512).to_string().parse::<i64>().unwrap_or(0)), ("temperature".to_string(), data.get(&"temperature".to_string()).cloned().unwrap_or(0.7_f64).to_string().parse::<f64>().unwrap_or(0.0)), ("top_p".to_string(), data.get(&"top_p".to_string()).cloned().unwrap_or(0.95_f64).to_string().parse::<f64>().unwrap_or(0.0)), ("repeat_penalty".to_string(), data.get(&"repeat_penalty".to_string()).cloned().unwrap_or(1.1_f64).to_string().parse::<f64>().unwrap_or(0.0)), ("inference_timeout".to_string(), req_timeout), ("performance_profile".to_string(), _normalize_perf_profile(data.get(&"performance_profile".to_string()).cloned().unwrap_or("balanced".to_string())))]);
            self.send_response(200);
            self.send_header("Content-Type".to_string(), "text/event-stream".to_string());
            self.send_header("Cache-Control".to_string(), "no-cache".to_string());
            self._cors_headers();
            self.end_headers();
            let mut _client_disconnected = false;
            let _sse = |event, payload| {
                // global/nonlocal _client_disconnected
                if _client_disconnected {
                    return;
                }
                // try:
                {
                    let mut line = format!("event: {}\ndata: {}\n\n", event, serde_json::to_string(&payload).unwrap());
                    self.wfile.write(line.encode("utf-8".to_string()));
                    self.wfile.flush();
                }
                // except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as _e:
            };
            // try:
            {
                // TODO: import llama_cpp
            }
            // except ImportError as _e:
            let mut responses = vec![];
            let mut n_ctx = params["n_ctx".to_string()];
            let mut max_tokens = params["max_tokens".to_string()];
            let mut temperature = params["temperature".to_string()];
            let mut top_p = params["top_p".to_string()];
            let mut repeat_penalty = params["repeat_penalty".to_string()];
            let mut inference_timeout = params["inference_timeout".to_string()];
            let mut perf_profile = params.get(&"performance_profile".to_string()).cloned().unwrap_or("balanced".to_string());
            let mut total_models = safe_models.len();
            let (mut max_workers, mut threads_per_model) = _compute_parallel_plan(safe_models, n_ctx, perf_profile);
            println!("[compare] Dispatching prompt to {} model(s) in parallel (profile={}, workers={}, threads/model={})...", total_models, perf_profile, max_workers, threads_per_model);
            let mut _dispatch_t0 = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
            for (idx, path) in safe_models.iter().enumerate().iter() {
                let mut model_name = os::path.basename(path).replace(&*".gguf".to_string(), &*"".to_string());
                _sse("model_start".to_string(), HashMap::from([("model".to_string(), model_name), ("model_index".to_string(), idx), ("total_models".to_string(), total_models)]));
            }
            let _thread_infer = |model_idx, path| {
                // Run inference on a pre-loaded model with stream=false.
                let mut model_name = os::path.basename(path).replace(&*".gguf".to_string(), &*"".to_string());
                let mut model_size_mb = if os::path.exists(path) { ((os::path.getsize(path) / (1024 * 1024)) as f64).round() } else { 0 };
                let mut model_n_ctx = _effective_n_ctx_for_path(path, n_ctx, perf_profile);
                let mut n_batch = _choose_n_batch(model_size_mb, perf_profile);
                let mut llm = _get_or_load_model(path, model_n_ctx, /* n_threads_override= */ threads_per_model, /* n_batch_override= */ n_batch);
                let mut t0 = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
                let mut ram_before = 0;
                // try:
                {
                    if HAS_PSUTIL {
                        let mut ram_before = if proc { (proc.memory_info().rss / (1024 * 1024)) } else { 0 };
                    }
                }
                // except Exception as _e:
                // try:
                {
                    let mut out = llm.create_chat_completion(/* messages= */ _build_messages(system_prompt, prompt, path), /* max_tokens= */ max_tokens, /* temperature= */ temperature, /* top_p= */ top_p, /* repeat_penalty= */ repeat_penalty, /* stream= */ false);
                    let mut elapsed_ms = ((std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() - t0) * 1000);
                    let mut response_text = (out["choices".to_string()][0]["message".to_string()]["content".to_string()] || "".to_string());
                    let mut completion_tokens = out.get(&"usage".to_string()).cloned().unwrap_or(HashMap::new()).get(&"completion_tokens".to_string()).cloned().unwrap_or(0);
                    if !completion_tokens {
                        let mut completion_tokens = 1.max(count_tokens(response_text));
                    }
                    let mut prompt_tokens = out.get(&"usage".to_string()).cloned().unwrap_or(HashMap::new()).get(&"prompt_tokens".to_string()).cloned().unwrap_or(0);
                    let mut tps = if elapsed_ms > 0 { (completion_tokens / (elapsed_ms / 1000)) } else { 0 };
                    let mut ttft_ms = (elapsed_ms / completion_tokens.max(1));
                    let mut ram_after = 0;
                    // try:
                    {
                        if HAS_PSUTIL {
                            let mut ram_after = if proc { (proc.memory_info().rss / (1024 * 1024)) } else { 0 };
                        }
                    }
                    // except Exception as _e:
                    let mut ram_delta = 0.max((ram_after - ram_before));
                    let mut model_size_gb = if model_size_mb { (model_size_mb / 1024) } else { 0 };
                    let mut efficiency = if model_size_gb > 0 { (((tps / model_size_gb) as f64) * 10f64.powi(2)).round() / 10f64.powi(2) } else { 0 };
                    let mut result = HashMap::from([("model".to_string(), model_name), ("model_path".to_string(), path), ("path".to_string(), path), ("response".to_string(), response_text), ("time_ms".to_string(), ((elapsed_ms as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("tokens".to_string(), completion_tokens), ("tokens_per_sec".to_string(), ((tps as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("quality_score".to_string(), 0), ("ttft_ms".to_string(), ((ttft_ms as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("ram_delta_mb".to_string(), ram_delta), ("prompt_tokens".to_string(), prompt_tokens), ("model_size_mb".to_string(), model_size_mb), ("efficiency".to_string(), efficiency), ("response_chars".to_string(), response_text.len())]);
                    println!("  [model-{}] {} done - {} tok, {:.1} t/s, eff={:.1} t/s/GB, {:.1}s", model_idx, model_name, completion_tokens, tps, efficiency, (elapsed_ms / 1000));
                    result
                }
                // except Exception as exc:
            };
            let mut pool = concurrent.futures.ThreadPoolExecutor(/* max_workers= */ max_workers);
            {
                let mut futures = safe_models.iter().enumerate().iter().map(|(idx, path)| (pool.submit(_thread_infer, idx, path), idx)).collect::<HashMap<_, _>>();
                // try:
                {
                    for fut in concurrent.futures.as_completed(futures, /* timeout= */ inference_timeout).iter() {
                        // try:
                        {
                            let mut result = fut.result();
                        }
                        // except Exception as exc:
                        responses.push(result);
                        let mut idx = futures[&fut];
                        _sse("token".to_string(), HashMap::from([("model".to_string(), result["model".to_string()]), ("model_index".to_string(), idx), ("token".to_string(), result.get(&"response".to_string()).cloned().unwrap_or("".to_string())), ("token_count".to_string(), result.get(&"tokens".to_string()).cloned().unwrap_or(0)), ("elapsed_ms".to_string(), (result.get(&"time_ms".to_string()).cloned().unwrap_or(0) as f64).round())]));
                        _sse("model_done".to_string(), HashMap::from([("model_index".to_string(), idx)]));
                    }
                }
                // except concurrent.futures.TimeoutError as _e:
            }
            let mut dispatch_wall = (std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() - _dispatch_t0);
            println!("[compare] All {} models done in {:.1}s wall-clock", total_models, dispatch_wall);
            responses.sort(/* key= */ |r| next(safe_models.iter().enumerate().iter().filter(|(i, p)| p == r.get(&"model_path".to_string()).cloned()).map(|(i, p)| i).collect::<Vec<_>>(), 99));
            if (judge_model && local_models) {
                _sse("judge_start".to_string(), HashMap::from([("judge_model".to_string(), judge_model)]));
                let mut judge_path = self._resolve_judge_path(judge_model, local_models);
                if judge_path {
                    if !judge_system_prompt {
                        let mut judge_system_prompt = "You are an expert evaluator. Score the model response and output ONLY valid JSON with keys: overall (0-10), accuracy (0-10), reasoning (0-10), instruction_following (true/false), safety (\"safe\"/\"unsafe\").".to_string();
                    }
                    let mut responses = self._run_judge(responses, prompt, judge_path, judge_system_prompt, params);
                }
                _sse("judge_done".to_string(), HashMap::from([("responses".to_string(), responses)]));
            }
            _sse("done".to_string(), HashMap::from([("prompt".to_string(), prompt), ("models_tested".to_string(), safe_models.len()), ("responses".to_string(), responses), ("judge_model".to_string(), judge_model), ("timestamp".to_string(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64())]));
        }
        // except Exception as e:
    }
    /// Run prompt through each local GGUF model via llama-cpp-python.
    pub fn _run_local_comparisons(&self, prompt: String, system_prompt: String, model_paths: Vec<String>, params: Option<HashMap>) -> Result<Vec<HashMap>> {
        // Run prompt through each local GGUF model via llama-cpp-python.
        let mut params = (params || HashMap::new());
        let mut n_ctx = params.get(&"n_ctx".to_string()).cloned().unwrap_or(4096);
        let mut max_tokens = params.get(&"max_tokens".to_string()).cloned().unwrap_or(512);
        let mut temperature = params.get(&"temperature".to_string()).cloned().unwrap_or(0.7_f64);
        let mut top_p = params.get(&"top_p".to_string()).cloned().unwrap_or(0.95_f64);
        let mut repeat_penalty = params.get(&"repeat_penalty".to_string()).cloned().unwrap_or(1.1_f64);
        let mut inference_timeout = params.get(&"inference_timeout".to_string()).cloned().unwrap_or(DEFAULT_INFERENCE_TIMEOUT);
        let mut perf_profile = params.get(&"performance_profile".to_string()).cloned().unwrap_or("balanced".to_string());
        let (mut max_workers, mut threads_per_model) = _compute_parallel_plan(model_paths, n_ctx, perf_profile);
        // try:
        {
            // TODO: import llama_cpp
        }
        // except ImportError as _e:
        let _run_one = |path| {
            let mut model_name = os::path.basename(path).replace(&*".gguf".to_string(), &*"".to_string());
            let mut model_size_mb = if os::path.exists(path) { ((os::path.getsize(path) / (1024 * 1024)) as f64).round() } else { 0 };
            let mut model_n_ctx = _effective_n_ctx_for_path(path, n_ctx, perf_profile);
            println!("[compare] START {}  ctx={}  max_tokens={}  temp={}", model_name, model_n_ctx, max_tokens, temperature);
            let mut t0 = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
            let mut n_batch = _choose_n_batch(model_size_mb, perf_profile);
            let mut ram_before = if (HAS_PSUTIL && proc.is_some()) { (proc.memory_info().rss / (1024 * 1024)) } else { 0 };
            // try:
            {
                let mut llm = _get_or_load_model(path, model_n_ctx, /* n_threads_override= */ threads_per_model, /* n_batch_override= */ n_batch);
                let mut out = llm.create_chat_completion(/* messages= */ _build_messages(system_prompt, prompt, path), /* max_tokens= */ max_tokens, /* temperature= */ temperature, /* top_p= */ top_p, /* repeat_penalty= */ repeat_penalty, /* stream= */ false);
                let mut elapsed_ms = ((std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() - t0) * 1000);
                let mut response_text = (out["choices".to_string()][0]["message".to_string()]["content".to_string()] || "".to_string());
                let mut completion_tokens = out.get(&"usage".to_string()).cloned().unwrap_or(HashMap::new()).get(&"completion_tokens".to_string()).cloned().unwrap_or(0);
                if !completion_tokens {
                    let mut completion_tokens = 1.max(count_tokens(response_text));
                }
                let mut tps = if elapsed_ms > 0 { (completion_tokens / (elapsed_ms / 1000)) } else { 0 };
                let mut ttft_ms = (elapsed_ms / completion_tokens.max(1));
                let mut ram_after = if (HAS_PSUTIL && proc.is_some()) { (proc.memory_info().rss / (1024 * 1024)) } else { 0 };
                let mut ram_delta = 0.max((ram_after - ram_before));
                let mut model_size_gb = if model_size_mb { (model_size_mb / 1024) } else { 0 };
                let mut efficiency = if model_size_gb > 0 { (((tps / model_size_gb) as f64) * 10f64.powi(2)).round() / 10f64.powi(2) } else { 0 };
                println!("[compare] OK {}  {:.0}ms  {}tok  {:.1}t/s  eff={:.1}t/s/GB  ram+{}MB", model_name, elapsed_ms, completion_tokens, tps, efficiency, ram_delta);
                HashMap::from([("model".to_string(), model_name), ("model_path".to_string(), path), ("path".to_string(), path), ("response".to_string(), response_text), ("time_ms".to_string(), ((elapsed_ms as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("tokens".to_string(), completion_tokens), ("tokens_per_sec".to_string(), ((tps as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("quality_score".to_string(), 0), ("ttft_ms".to_string(), ((ttft_ms as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("ram_delta_mb".to_string(), ram_delta), ("prompt_tokens".to_string(), out.get(&"usage".to_string()).cloned().unwrap_or(HashMap::new()).get(&"prompt_tokens".to_string()).cloned().unwrap_or(0)), ("model_size_mb".to_string(), model_size_mb), ("efficiency".to_string(), efficiency), ("response_chars".to_string(), response_text.len())])
            }
            // except Exception as exc:
        };
        println!("[compare] Dispatching {} model(s) in parallel (profile={}, workers={}, threads/model={})...", model_paths.len(), perf_profile, max_workers, threads_per_model);
        let mut pool = concurrent.futures.ThreadPoolExecutor(/* max_workers= */ max_workers);
        {
            let mut futures = model_paths.iter().map(|p| (pool.submit(_run_one, p), p)).collect::<HashMap<_, _>>();
            let mut results = vec![];
            // try:
            {
                for fut in concurrent.futures.as_completed(futures, /* timeout= */ inference_timeout).iter() {
                    // try:
                    {
                        results.push(fut.result());
                    }
                    // except Exception as exc:
                }
            }
            // except concurrent.futures.TimeoutError as _e:
        }
        let mut path_order = model_paths.iter().enumerate().iter().map(|(i, p)| (p, i)).collect::<HashMap<_, _>>();
        results.sort(/* key= */ |r| path_order.get(&r.get(&"model_path".to_string()).cloned().unwrap_or("".to_string())).cloned().unwrap_or(99));
        Ok(results)
    }
    /// Return the filesystem path to use as judge model.
    pub fn _resolve_judge_path(&self, judge_model: String, local_models: Vec<String>) -> Option<String> {
        // Return the filesystem path to use as judge model.
        if judge_model == "local:best".to_string() {
            let mut best = min(local_models, /* key= */ |p| if os::path.exists(p) { os::path.getsize(p) } else { "inf".to_string().to_string().parse::<f64>().unwrap_or(0.0) }, /* default= */ None);
            best
        }
        if (judge_model && !judge_model.starts_with(&*"online:".to_string())) {
            if os::path.exists(judge_model) {
                judge_model
            }
            for p in local_models.iter() {
                if os::path.basename(p).to_lowercase().starts_with(&*judge_model.to_lowercase()) {
                    p
                }
            }
        }
        None
    }
    /// Score each response using one or more judge models.
    /// 
    /// Supports:
    /// - E1: Structured JSON output via response_format
    /// - E3: Multi-judge consensus (pass list of paths)
    /// - E4: Reference-guided judging (optional reference_answer)
    pub fn _run_judge(&self, responses: Vec<HashMap>, original_prompt: String, judge_path: /* Union(String, Vec<String>) */ Box<dyn std::any::Any>, judge_system_prompt: String, params: HashMap<String, serde_json::Value>, reference_answer: String) -> Result<Vec<HashMap>> {
        // Score each response using one or more judge models.
        // 
        // Supports:
        // - E1: Structured JSON output via response_format
        // - E3: Multi-judge consensus (pass list of paths)
        // - E4: Reference-guided judging (optional reference_answer)
        // try:
        {
            // TODO: import llama_cpp
        }
        // except ImportError as _e:
        let mut judge_paths = if /* /* isinstance(judge_path, list) */ */ true { judge_path } else { vec![judge_path] };
        for (idx, r) in responses.iter().enumerate().iter() {
            if r.get(&"error".to_string()).cloned() {
                continue;
            }
            let mut all_judge_scores = vec![];
            let mut all_judge_details = vec![];
            for jp in judge_paths.iter() {
                let mut judge_name = os::path.basename(jp).replace(&*".gguf".to_string(), &*"".to_string());
                // try:
                {
                    let mut llm = _get_or_load_model(jp, params.get(&"n_ctx".to_string()).cloned().unwrap_or(4096).min(8192));
                }
                // except Exception as load_err:
                let mut user_msg = format!("Original question: {}\n\n", original_prompt);
                if reference_answer {
                    user_msg += format!("Reference answer:\n{}\n\n", reference_answer);
                }
                user_msg += format!("Model response:\n{}", r.get(&"response".to_string()).cloned().unwrap_or("".to_string()));
                for attempt in 0..2.iter() {
                    // try:
                    {
                        let mut sys_prompt = if attempt == 0 { judge_system_prompt } else { "Rate the response quality 0-10. Output ONLY a JSON object: {\"overall\": <number>}".to_string() };
                        let mut create_kwargs = /* dict(/* messages= */ _build_messages(sys_prompt, user_msg, jp), /* max_tokens= */ 512, /* temperature= */ 0.1_f64, /* stream= */ false) */ HashMap::new();
                        // try:
                        {
                            create_kwargs["response_format".to_string()] = HashMap::from([("type".to_string(), "json_object".to_string())]);
                            let mut out = llm.create_chat_completion(/* ** */ create_kwargs);
                        }
                        // except Exception as _e:
                        let mut raw = out["choices".to_string()][0]["message".to_string()]["content".to_string()].trim().to_string();
                        let mut jd = extract_judge_scores(raw);
                        let mut score = jd.get(&"overall".to_string()).cloned().unwrap_or(0).to_string().parse::<f64>().unwrap_or(0.0);
                        jd["judge_model".to_string()] = judge_name;
                        all_judge_scores.push(score);
                        all_judge_details.push(jd);
                        break;
                    }
                    // except Exception as je:
                }
            }
            if all_judge_scores {
                let mut avg_score = (((all_judge_scores.iter().sum::<i64>() / all_judge_scores.len()) as f64) * 10f64.powi(1)).round() / 10f64.powi(1);
                let mut detail = all_judge_details[0].clone();
                detail["overall".to_string()] = avg_score;
                if all_judge_details.len() > 1 {
                    detail["consensus".to_string()] = HashMap::from([("num_judges".to_string(), all_judge_details.len()), ("scores".to_string(), all_judge_scores.iter().map(|s| ((s as f64) * 10f64.powi(1)).round() / 10f64.powi(1)).collect::<Vec<_>>()), ("judges".to_string(), all_judge_details.iter().map(|d| d.get(&"judge_model".to_string()).cloned().unwrap_or("?".to_string())).collect::<Vec<_>>()), ("spread".to_string(), (((all_judge_scores.iter().max().unwrap() - all_judge_scores.iter().min().unwrap()) as f64) * 10f64.powi(1)).round() / 10f64.powi(1))]);
                }
                r["judge_score".to_string()] = avg_score;
                r["quality_score".to_string()] = avg_score;
                r["judge_detail".to_string()] = detail;
                println!("{}", (format!("[judge] OK {}  score={:.1}", r["model".to_string()], avg_score) + if all_judge_scores.len() > 1 { format!(" ({} judges)", all_judge_scores.len()) } else { "".to_string() }));
            } else {
                r["judge_score".to_string()] = 0;
                r["quality_score".to_string()] = 0;
                r["judge_detail".to_string()] = HashMap::from([("overall".to_string(), 0), ("error".to_string(), "Judge failed after retries".to_string())]);
            }
        }
        Ok(responses)
    }
    pub fn _handle_chat(&mut self, data: HashMap<String, serde_json::Value>) -> Result<()> {
        let mut model_path = data.get(&"model_path".to_string()).cloned().unwrap_or("".to_string()).trim().to_string();
        if (!model_path || !os::path.isfile(model_path)) {
            self._send_json(400, HashMap::from([("error".to_string(), "Model file not found".to_string())]));
            return;
        }
        if !_is_safe_model_path(model_path, self.model_dirs) {
            self._send_json(403, HashMap::from([("error".to_string(), "Model path not allowed".to_string())]));
            return;
        }
        let mut system = data.get(&"system".to_string()).cloned().unwrap_or("You are a helpful assistant.".to_string());
        let mut messages = data.get(&"messages".to_string()).cloned().unwrap_or(vec![]);
        let mut max_tokens = data.get(&"max_tokens".to_string()).cloned().unwrap_or(512).to_string().parse::<i64>().unwrap_or(0).min(2048);
        let mut temperature = data.get(&"temperature".to_string()).cloned().unwrap_or(0.4_f64).to_string().parse::<f64>().unwrap_or(0.0);
        // try:
        {
            // TODO: import gc
            // TODO: import llama_cpp
            let mut llm = _get_or_load_model(model_path, 4096);
            let mut full_messages = if messages.len() <= 1 { _build_messages(system, if messages { messages[0]["content".to_string()] } else { "".to_string() }, model_path) } else { (vec![HashMap::from([("role".to_string(), "system".to_string()), ("content".to_string(), system)])] + messages) };
            // try:
            {
                let mut out = llm.create_chat_completion(full_messages, /* max_tokens= */ max_tokens, /* temperature= */ temperature, /* stream= */ false);
            }
            // except ValueError as _e:
            let mut reply = out["choices".to_string()][0]["message".to_string()]["content".to_string()];
            self._send_json(200, HashMap::from([("response".to_string(), reply)]));
        }
        // except Exception as e:
    }
    /// GET /__discover-models?q=&sort=trending&limit=30
    pub fn _handle_discover_models(&mut self) -> () {
        // GET /__discover-models?q=&sort=trending&limit=30
        let mut qs = parse_qs(/* urlparse */ self.path.query);
    }
    /// GET /__prompts?name=X&version=N&alias=A
    pub fn _handle_prompts_get(&mut self) -> () {
        // GET /__prompts?name=X&version=N&alias=A
        let mut qs = parse_qs(/* urlparse */ self.path.query);
        let mut name = qs.get(&"name".to_string()).cloned().unwrap_or(vec![None])[0];
        let mut version = qs.get(&"version".to_string()).cloned().unwrap_or(vec![None])[0];
        let mut alias = qs.get(&"alias".to_string()).cloned().unwrap_or(vec![None])[0];
        if (name && (version || alias)) {
            let mut p = load_prompt(name, /* version= */ if version { version.to_string().parse::<i64>().unwrap_or(0) } else { None }, /* alias= */ alias);
            if p {
                // TODO: from dataclasses import asdict
                self._send_json(200, asdict(p));
            } else {
                self._send_json(404, HashMap::from([("error".to_string(), "Prompt not found".to_string())]));
            }
        } else {
            self._send_json(200, list_prompts(name));
        }
    }
    /// POST /__prompts — register a new prompt version.
    pub fn _handle_prompts_post(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__prompts — register a new prompt version.
        let mut name = data.get(&"name".to_string()).cloned().unwrap_or("".to_string()).trim().to_string();
        let mut template = data.get(&"template".to_string()).cloned().unwrap_or("".to_string()).trim().to_string();
        if (!name || !template) {
            self._send_json(400, HashMap::from([("error".to_string(), "name and template required".to_string())]));
            return;
        }
        let mut p = register_prompt(/* name= */ name, /* template= */ template, /* system_prompt= */ data.get(&"system_prompt".to_string()).cloned().unwrap_or("".to_string()), /* temperature= */ data.get(&"temperature".to_string()).cloned().unwrap_or(0.7_f64).to_string().parse::<f64>().unwrap_or(0.0), /* max_tokens= */ data.get(&"max_tokens".to_string()).cloned().unwrap_or(512).to_string().parse::<i64>().unwrap_or(0), /* commit_msg= */ data.get(&"commit_msg".to_string()).cloned().unwrap_or("".to_string()));
        // TODO: from dataclasses import asdict
        self._send_json(201, asdict(p));
    }
    /// POST /__prompts/alias — set or delete an alias.
    pub fn _handle_prompt_alias(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__prompts/alias — set or delete an alias.
        let mut name = data.get(&"name".to_string()).cloned().unwrap_or("".to_string()).trim().to_string();
        let mut alias = data.get(&"alias".to_string()).cloned().unwrap_or("".to_string()).trim().to_string();
        if (!name || !alias) {
            self._send_json(400, HashMap::from([("error".to_string(), "name and alias required".to_string())]));
            return;
        }
        if data.get(&"delete".to_string()).cloned() {
            let mut ok = delete_alias(name, alias);
            self._send_json(if ok { 200 } else { 404 }, HashMap::from([("ok".to_string(), ok)]));
        } else {
            let mut version = data.get(&"version".to_string()).cloned();
            if version.is_none() {
                self._send_json(400, HashMap::from([("error".to_string(), "version required".to_string())]));
                return;
            }
            let mut ok = set_alias(name, alias, version.to_string().parse::<i64>().unwrap_or(0));
            self._send_json(if ok { 200 } else { 404 }, HashMap::from([("ok".to_string(), ok)]));
        }
    }
    /// GET /__feedback?judge=X&limit=N or /__feedback/stats?judge=X
    pub fn _handle_feedback_get(&mut self) -> Result<()> {
        // GET /__feedback?judge=X&limit=N or /__feedback/stats?judge=X
        let mut parsed = /* urlparse */ self.path;
        let mut qs = parse_qs(parsed.query);
        if parsed.path == "/__feedback/stats".to_string() {
            let mut judge = qs.get(&"judge".to_string()).cloned().unwrap_or(vec!["".to_string()])[0];
            if !judge {
                self._send_json(400, HashMap::from([("error".to_string(), "judge parameter required".to_string())]));
                return;
            }
            self._send_json(200, get_alignment_stats(judge));
        } else {
            let mut judge = qs.get(&"judge".to_string()).cloned().unwrap_or(vec![None])[0];
            // try:
            {
                let mut limit = qs.get(&"limit".to_string()).cloned().unwrap_or(vec![50])[0].to_string().parse::<i64>().unwrap_or(0);
            }
            // except (ValueError, TypeError) as _e:
            self._send_json(200, get_feedback_history(judge, limit));
        }
    }
    /// POST /__feedback — record judge feedback.
    pub fn _handle_feedback_post(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__feedback — record judge feedback.
        let mut required = vec!["judge_name".to_string(), "prompt".to_string(), "response".to_string(), "auto_score".to_string()];
        if !required.iter().map(|k| data.get(&k).cloned().is_some()).collect::<Vec<_>>().iter().all(|v| *v) {
            self._send_json(400, HashMap::from([("error".to_string(), format!("Required: {}", required.join(&", ".to_string())))]));
            return;
        }
        let mut fid = record_feedback(/* judge_name= */ data["judge_name".to_string()], /* prompt= */ data["prompt".to_string()], /* response= */ data["response".to_string()], /* auto_score= */ data["auto_score".to_string()].to_string().parse::<f64>().unwrap_or(0.0), /* human_score= */ if data.get(&"human_score".to_string()).cloned().is_some() { data["human_score".to_string()].to_string().parse::<f64>().unwrap_or(0.0) } else { None }, /* feedback= */ data.get(&"feedback".to_string()).cloned().unwrap_or("".to_string()));
        self._send_json(201, HashMap::from([("id".to_string(), fid)]));
    }
    /// POST /__feedback/human — update human score for existing feedback.
    pub fn _handle_feedback_human(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__feedback/human — update human score for existing feedback.
        let mut fid = data.get(&"id".to_string()).cloned();
        let mut human_score = data.get(&"human_score".to_string()).cloned();
        if (fid.is_none() || human_score.is_none()) {
            self._send_json(400, HashMap::from([("error".to_string(), "id and human_score required".to_string())]));
            return;
        }
        let mut ok = update_human_score(fid.to_string().parse::<i64>().unwrap_or(0), human_score.to_string().parse::<f64>().unwrap_or(0.0), data.get(&"feedback".to_string()).cloned().unwrap_or("".to_string()));
        self._send_json(if ok { 200 } else { 404 }, HashMap::from([("ok".to_string(), ok)]));
    }
    /// POST /__judge/conversation — run multi-turn judges on a conversation.
    pub fn _handle_judge_conversation(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__judge/conversation — run multi-turn judges on a conversation.
        let mut turns_raw = data.get(&"turns".to_string()).cloned().unwrap_or(vec![]);
        if turns_raw.len() < 2 {
            self._send_json(400, HashMap::from([("error".to_string(), "At least 2 turns required".to_string())]));
            return;
        }
        let mut conv_id = data.get(&"conversation_id".to_string()).cloned().unwrap_or(format!("conv_{}", (std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() * 1000).to_string().parse::<i64>().unwrap_or(0)));
        let mut model_name = data.get(&"model_name".to_string()).cloned().unwrap_or("unknown".to_string());
        let mut judges = data.get(&"judges".to_string()).cloned().unwrap_or(vec!["UserFrustration".to_string(), "KnowledgeRetention".to_string()]);
        let mut turns = turns_raw.iter().enumerate().iter().map(|(i, t)| TurnData(/* role= */ t.get(&"role".to_string()).cloned().unwrap_or("user".to_string()), /* content= */ t.get(&"content".to_string()).cloned().unwrap_or("".to_string()), /* turn_num= */ i, /* metadata= */ t.get(&"metadata".to_string()).cloned().unwrap_or(HashMap::new()))).collect::<Vec<_>>();
        let mut ctx = ConversationContext(/* conversation_id= */ conv_id, /* model_name= */ model_name, /* turns= */ turns, /* metadata= */ data.get(&"metadata".to_string()).cloned().unwrap_or(HashMap::new()));
        let mut results = HashMap::new();
        // TODO: from dataclasses import asdict
        if judges.contains(&"UserFrustration".to_string()) {
            results["UserFrustration".to_string()] = asdict(judge_user_frustration(ctx));
        }
        if judges.contains(&"KnowledgeRetention".to_string()) {
            results["KnowledgeRetention".to_string()] = asdict(judge_knowledge_retention(ctx));
        }
        if data.get(&"save".to_string()).cloned().unwrap_or(false) {
            save_conversation(ctx);
        }
        self._send_json(200, HashMap::from([("conversation_id".to_string(), conv_id), ("results".to_string(), results)]));
    }
    /// POST /__judge/toolcall — evaluate tool call correctness & efficiency.
    pub fn _handle_judge_toolcall(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__judge/toolcall — evaluate tool call correctness & efficiency.
        let mut actual_raw = data.get(&"actual_calls".to_string()).cloned().unwrap_or(vec![]);
        let mut expected_raw = data.get(&"expected_calls".to_string()).cloned().unwrap_or(vec![]);
        let mut actual = actual_raw.iter().map(|c| ToolCall(/* name= */ c.get(&"name".to_string()).cloned().unwrap_or("".to_string()), /* arguments= */ c.get(&"arguments".to_string()).cloned().unwrap_or(HashMap::new()), /* result= */ c.get(&"result".to_string()).cloned())).collect::<Vec<_>>();
        let mut expected = expected_raw.iter().map(|e| ToolCallExpectation(/* name= */ e.get(&"name".to_string()).cloned().unwrap_or("".to_string()), /* arguments= */ e.get(&"arguments".to_string()).cloned(), /* required= */ e.get(&"required".to_string()).cloned().unwrap_or(true), /* order= */ e.get(&"order".to_string()).cloned())).collect::<Vec<_>>();
        // TODO: from dataclasses import asdict
        let mut results = HashMap::new();
        let mut judges = data.get(&"judges".to_string()).cloned().unwrap_or(vec!["ToolCallCorrectness".to_string(), "ToolCallEfficiency".to_string()]);
        if judges.contains(&"ToolCallCorrectness".to_string()) {
            results["ToolCallCorrectness".to_string()] = asdict(judge_tool_call_correctness(actual, expected));
        }
        if judges.contains(&"ToolCallEfficiency".to_string()) {
            results["ToolCallEfficiency".to_string()] = asdict(judge_tool_call_efficiency(actual, /* min_expected= */ data.get(&"min_calls".to_string()).cloned().unwrap_or(1), /* max_expected= */ data.get(&"max_calls".to_string()).cloned()));
        }
        self._send_json(200, HashMap::from([("results".to_string(), results)]));
    }
    /// GET /__gateway/routes — list all routes.
    pub fn _handle_gateway_routes_get(&mut self) -> () {
        // GET /__gateway/routes — list all routes.
        let mut gw = get_gateway();
        self._send_json(200, gw.list_routes());
    }
    /// POST /__gateway/routes — add/update a route.
    pub fn _handle_gateway_routes_post(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__gateway/routes — add/update a route.
        let mut name = data.get(&"name".to_string()).cloned().unwrap_or("".to_string()).trim().to_string();
        let mut strategy = data.get(&"strategy".to_string()).cloned().unwrap_or("".to_string()).trim().to_string();
        let mut models = data.get(&"models".to_string()).cloned().unwrap_or(vec![]);
        if (!name || !strategy || !models) {
            self._send_json(400, HashMap::from([("error".to_string(), "name, strategy, and models required".to_string())]));
            return;
        }
        if !("round_robin".to_string(), "weighted".to_string(), "fallback".to_string(), "ab_test".to_string()).contains(&strategy) {
            self._send_json(400, HashMap::from([("error".to_string(), "strategy must be: round_robin, weighted, fallback, ab_test".to_string())]));
            return;
        }
        let mut gw = get_gateway();
        let mut route = GatewayRoute(/* name= */ name, /* strategy= */ strategy, /* models= */ models, /* config= */ data.get(&"config".to_string()).cloned().unwrap_or(HashMap::new()), /* enabled= */ data.get(&"enabled".to_string()).cloned().unwrap_or(true));
        gw.add_route(route);
        self._send_json(201, HashMap::from([("ok".to_string(), true), ("route".to_string(), name)]));
    }
    /// POST /__gateway/resolve — resolve a route to a model.
    pub fn _handle_gateway_resolve(&mut self, data: HashMap<String, serde_json::Value>) -> () {
        // POST /__gateway/resolve — resolve a route to a model.
        let mut route_name = data.get(&"route".to_string()).cloned().unwrap_or("".to_string()).trim().to_string();
        if !route_name {
            self._send_json(400, HashMap::from([("error".to_string(), "route name required".to_string())]));
            return;
        }
        let mut gw = get_gateway();
        let mut model = gw.resolve(route_name);
        if model {
            self._send_json(200, HashMap::from([("model".to_string(), model), ("route".to_string(), route_name)]));
        } else {
            self._send_json(404, HashMap::from([("error".to_string(), format!("Route '{}' not found", route_name))]));
        }
    }
    /// GET /__gateway/stats?route=X
    pub fn _handle_gateway_stats(&mut self) -> Result<()> {
        // GET /__gateway/stats?route=X
        let mut qs = parse_qs(/* urlparse */ self.path.query);
        let mut route = qs.get(&"route".to_string()).cloned().unwrap_or(vec!["".to_string()])[0];
        if !route {
            self._send_json(400, HashMap::from([("error".to_string(), "route parameter required".to_string())]));
            return;
        }
        let mut gw = get_gateway();
        self._send_json(200, gw.get_route_stats(route));
        let mut query = qs.get(&"q".to_string()).cloned().unwrap_or(vec!["".to_string()])[0][..200];
        let mut sort = qs.get(&"sort".to_string()).cloned().unwrap_or(vec!["trending".to_string()])[0];
        if !("trending".to_string(), "downloads".to_string(), "newest".to_string(), "likes".to_string()).contains(&sort) {
            let mut sort = "trending".to_string();
        }
        // try:
        {
            let mut limit = qs.get(&"limit".to_string()).cloned().unwrap_or(vec!["30".to_string()])[0].to_string().parse::<i64>().unwrap_or(0).min(60);
        }
        // except (ValueError, TypeError) as _e:
        let mut results = _discover_hf_models(query, sort, limit);
        Ok(self._send_json(200, HashMap::from([("models".to_string(), results), ("cached".to_string(), (_discovery_cache != 0))])))
    }
    /// GET /__scout?category=all&limit=20 — Internet Scout for new models.
    pub fn _handle_scout(&mut self) -> Result<()> {
        // GET /__scout?category=all&limit=20 — Internet Scout for new models.
        let mut qs = parse_qs(/* urlparse */ self.path.query);
        let mut category = qs.get(&"category".to_string()).cloned().unwrap_or(vec!["all".to_string()])[0][..50];
        // try:
        {
            let mut limit = qs.get(&"limit".to_string()).cloned().unwrap_or(vec!["20".to_string()])[0].to_string().parse::<i64>().unwrap_or(0).min(60);
        }
        // except (ValueError, TypeError) as _e:
        let mut results = _scout_hf_trending(category, limit);
        Ok(self._send_json(200, HashMap::from([("models".to_string(), results), ("category".to_string(), category), ("categories".to_string(), _TOOL_CATEGORIES.iter().iter().map(|(k, v)| (k, HashMap::from([("icon".to_string(), v["icon".to_string()]), ("desc".to_string(), v["desc".to_string()])]))).collect::<HashMap<_, _>>())])))
    }
    /// GET /__tool-ecosystem — Discover AI tool categories with top models.
    pub fn _handle_tool_ecosystem(&mut self) -> () {
        // GET /__tool-ecosystem — Discover AI tool categories with top models.
        let mut ecosystem = _scout_tool_ecosystem();
        self._send_json(200, ecosystem);
    }
    pub fn _cors_headers(&mut self) -> () {
        let mut origin = self.headers.get(&"Origin".to_string()).cloned().unwrap_or("".to_string());
        if (("".to_string(), "null".to_string()).contains(&origin) || regex::Regex::new(&"^https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?$".to_string()).unwrap().is_match(&origin)) {
            let mut allowed = if (origin && origin != "null".to_string()) { origin } else { "http://127.0.0.1:8123".to_string() };
            self.send_header("Access-Control-Allow-Origin".to_string(), allowed);
            self.send_header("Vary".to_string(), "Origin".to_string());
        }
        self.send_header("Access-Control-Allow-Methods".to_string(), "GET, POST, OPTIONS".to_string());
        self.send_header("Access-Control-Allow-Headers".to_string(), "Content-Type".to_string());
    }
    pub fn _send_json(&mut self, status: i64, data: Box<dyn std::any::Any>) -> Result<()> {
        // try:
        {
            let mut body = serde_json::to_string(&data).unwrap().encode("utf-8".to_string());
            self.send_response(status);
            self.send_header("Content-Type".to_string(), "application/json".to_string());
            self.send_header("Cache-Control".to_string(), "no-store".to_string());
            self._cors_headers();
            self.end_headers();
            self.wfile.write(body);
        }
        // except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as _e:
    }
    pub fn log_message(&mut self, format: String, args: Vec<Box<dyn std::any::Any>>) -> () {
        let mut msg = (format % args);
        if ("ConnectionAbortedError".to_string(), "BrokenPipeError".to_string(), "ConnectionResetError".to_string()).iter().map(|k| msg.contains(&k)).collect::<Vec<_>>().iter().any(|v| *v) {
            return;
        }
        println!("[{}] {}", self.log_date_time_string(), msg);
    }
}

/// Estimate token count. Uses tiktoken if available, else ~words/0.75.
pub fn count_tokens(text: String, model_path: Option<String>) -> Result<i64> {
    // Estimate token count. Uses tiktoken if available, else ~words/0.75.
    if !text {
        0
    }
    // try:
    {
        // TODO: import tiktoken
        let mut enc = tiktoken.get_encoding("cl100k_base".to_string());
        enc.encode(text).len()
    }
    // except Exception as _e:
}

/// Get physical CPU core count.
pub fn get_cpu_count() -> Result<i64> {
    // Get physical CPU core count.
    // try:
    {
        (os::cpu_count() || 1)
    }
    // except Exception as _e:
}

/// Get total RAM in GB.
pub fn get_memory_gb() -> Result<f64> {
    // Get total RAM in GB.
    // try:
    {
        // TODO: import psutil as _ps
        (_ps.virtual_memory().total / (1024).pow(3 as u32))
    }
    // except Exception as _e:
}

/// Detect CPU brand, full model name, and SIMD capabilities.
pub fn get_cpu_info() -> Result<HashMap> {
    // Detect CPU brand, full model name, and SIMD capabilities.
    // TODO: import platform
    let mut info = HashMap::from([("brand".to_string(), "Unknown".to_string()), ("name".to_string(), "".to_string()), ("cores".to_string(), get_cpu_count()), ("avx2".to_string(), false), ("avx512".to_string(), false)]);
    // try:
    {
        let mut proc_name = platform.processor();
        if proc_name {
            info["name".to_string()] = proc_name;
            let mut up = proc_name.to_uppercase();
            if up.contains(&"AMD".to_string()) {
                info["brand".to_string()] = "AMD".to_string();
            } else if up.contains(&"INTEL".to_string()) {
                info["brand".to_string()] = "Intel".to_string();
            }
        }
    }
    // except Exception as _e:
    let mut pid = std::env::var(&"PROCESSOR_IDENTIFIER".to_string()).unwrap_or_default().cloned().unwrap_or("".to_string());
    if (pid && info["brand".to_string()] == "Unknown".to_string()) {
        if pid.to_uppercase().contains(&"AMD".to_string()) {
            info["brand".to_string()] = "AMD".to_string();
        } else if pid.to_uppercase().contains(&"INTEL".to_string()) {
            info["brand".to_string()] = "Intel".to_string();
        }
    }
    if (pid && !info["name".to_string()]) {
        info["name".to_string()] = pid;
    }
    // try:
    {
        // TODO: import cpuinfo as _ci
        let mut d = _ci.get_cpu_info();
        info["name".to_string()] = d.get(&"brand_raw".to_string()).cloned().unwrap_or(info["name".to_string()]);
        let mut flags = d.get(&"flags".to_string()).cloned().unwrap_or(vec![]);
        info["avx2".to_string()] = flags.contains(&"avx2".to_string());
        info["avx512".to_string()] = flags.iter().map(|f| f.starts_with(&*"avx512".to_string())).collect::<Vec<_>>().iter().any(|v| *v);
    }
    // except Exception as _e:
    Ok(info)
}

/// Scan directories for .gguf model files, including GGUF metadata.
pub fn scan_gguf_models(dirs: Option<Vec<String>>) -> Result<Vec<HashMap>> {
    // Scan directories for .gguf model files, including GGUF metadata.
    if !dirs {
        let mut dirs = vec![os::path.expanduser("~/AI/Models".to_string())];
    }
    let mut models = vec![];
    let mut all_paths = vec![];
    for d in dirs.iter() {
        let mut p = PathBuf::from(d);
        if !p.is_dir() {
            continue;
        }
        for f in p.rglob("*.gguf".to_string()).iter() {
            // try:
            {
                let mut size_mb = ((f.stat().st_size / (1024 * 1024)) as f64).round();
                let mut meta = _extract_gguf_metadata(f.to_string());
                let mut entry = HashMap::from([("id".to_string(), f.name), ("path".to_string(), f.to_string()), ("size_mb".to_string(), size_mb), ("name".to_string(), f.file_stem().unwrap_or_default().to_str().unwrap_or("")), ("architecture".to_string(), meta.get(&"architecture".to_string()).cloned().unwrap_or("".to_string())), ("context_length".to_string(), meta.get(&"context_length".to_string()).cloned().unwrap_or(0)), ("quantization".to_string(), meta.get(&"quantization".to_string()).cloned().unwrap_or("".to_string())), ("parameters".to_string(), meta.get(&"parameters".to_string()).cloned().unwrap_or("".to_string())), ("embedding_length".to_string(), meta.get(&"embedding_length".to_string()).cloned().unwrap_or(0))]);
                models.push(entry);
                all_paths.push(f.to_string());
            }
            // except Exception as _e:
        }
    }
    if all_paths {
        std::thread::spawn(|| {});
    }
    Ok(models)
}

/// Load cached GGUF metadata from disk.
pub fn _load_gguf_meta_cache() -> Result<()> {
    // Load cached GGUF metadata from disk.
    // global/nonlocal _gguf_meta_cache
    // try:
    {
        let mut f = File::open(_GGUF_META_CACHE_PATH)?;
        {
            let mut _gguf_meta_cache = json::load(f);
        }
    }
    // except Exception as _e:
}

/// Persist GGUF metadata cache to disk.
pub fn _save_gguf_meta_cache() -> Result<()> {
    // Persist GGUF metadata cache to disk.
    // try:
    {
        let mut f = File::create(_GGUF_META_CACHE_PATH)?;
        {
            json::dump(_gguf_meta_cache, f);
        }
    }
    // except Exception as _e:
}

/// Fast metadata inference from filename — no file I/O.
pub fn _infer_metadata_from_filename(path: String) -> HashMap {
    // Fast metadata inference from filename — no file I/O.
    let mut meta = HashMap::new();
    let mut fname = os::path.basename(path).to_uppercase();
    for qt in ("Q8_0".to_string(), "Q6_K".to_string(), "Q5_K_M".to_string(), "Q5_K_S".to_string(), "Q4_K_M".to_string(), "Q4_K_S".to_string(), "Q4_0".to_string(), "Q3_K_M".to_string(), "Q3_K_S".to_string(), "Q2_K".to_string(), "IQ4_XS".to_string(), "IQ3_M".to_string(), "F16".to_string(), "F32".to_string()).iter() {
        if fname.contains(&qt) {
            meta["quantization".to_string()] = qt;
            break;
        }
    }
    for arch in ("LLAMA".to_string(), "QWEN".to_string(), "PHI".to_string(), "GEMMA".to_string(), "MISTRAL".to_string(), "COMMAND".to_string(), "STARCODER".to_string(), "DEEPSEEK".to_string(), "GLM".to_string(), "DEVSTRAL".to_string(), "GRANITE".to_string()).iter() {
        if fname.contains(&arch) {
            meta["architecture".to_string()] = arch.to_lowercase();
            break;
        }
    }
    // TODO: import re as _re
    let mut pm = regex::Regex::new(&"(\\d+(?:\\.\\d+)?)[Bb]".to_string()).unwrap().is_match(&os::path.basename(path));
    if pm {
        meta["parameters".to_string()] = pm.group(0).to_uppercase();
    }
    meta
}

/// Extract metadata from GGUF file header, with disk cache for speed.
pub fn _extract_gguf_metadata(path: String) -> Result<HashMap> {
    // Extract metadata from GGUF file header, with disk cache for speed.
    // try:
    {
        let mut size = os::path.getsize(path);
    }
    // except OSError as _e:
    let mut cache_key = format!("{}|{}", path, size);
    if _gguf_meta_cache.contains(&cache_key) {
        _gguf_meta_cache[&cache_key]
    }
    Ok(_infer_metadata_from_filename(path))
}

/// Background thread: read GGUF headers and fill the cache.
pub fn _background_fill_gguf_cache(paths: Vec<String>) -> Result<()> {
    // Background thread: read GGUF headers and fill the cache.
    let mut changed = false;
    for path in paths.iter() {
        // try:
        {
            let mut size = os::path.getsize(path);
        }
        // except OSError as _e:
        let mut cache_key = format!("{}|{}", path, size);
        if _gguf_meta_cache.contains(&cache_key) {
            continue;
        }
        let mut meta = _infer_metadata_from_filename(path);
        // try:
        {
            // TODO: from gguf import GGUFReader
            let mut reader = GGUFReader(path, "r".to_string());
            for field in reader.fields.values().iter() {
                let mut name = if /* hasattr(field, "name".to_string()) */ true { field.name } else { "".to_string() };
                if !name {
                    continue;
                }
                if name.contains(&"context_length".to_string()) {
                    meta["context_length".to_string()] = if field.parts { field.parts[-1][0].to_string().parse::<i64>().unwrap_or(0) } else { 0 };
                } else if name.contains(&"embedding_length".to_string()) {
                    meta["embedding_length".to_string()] = if field.parts { field.parts[-1][0].to_string().parse::<i64>().unwrap_or(0) } else { 0 };
                } else if name.contains(&"general.architecture".to_string()) {
                    meta["architecture".to_string()] = if field.parts { str(bytes(field.parts[-1]), "utf-8".to_string()).trim_matches(|c: char| " ".to_string().contains(c)).to_string() } else { "".to_string() };
                } else if (name.contains(&"general.quantization_version".to_string()) || name.contains(&"general.file_type".to_string())) {
                    let mut val = if field.parts { str(bytes(field.parts[-1]), "utf-8".to_string()).trim_matches(|c: char| " ".to_string().contains(c)).to_string() } else { "".to_string() };
                    if val {
                        meta["quantization".to_string()] = val;
                    }
                } else if name.contains(&"general.name".to_string()) {
                    meta["model_name".to_string()] = if field.parts { str(bytes(field.parts[-1]), "utf-8".to_string()).trim_matches(|c: char| " ".to_string().contains(c)).to_string() } else { "".to_string() };
                }
            }
        }
        // except Exception as _e:
        _gguf_meta_cache[cache_key] = meta;
        let mut changed = true;
    }
    if changed {
        _save_gguf_meta_cache();
    }
}

/// Estimate runtime memory (GB) from file size + overhead.
pub fn estimate_model_memory_gb(size_mb: i64, quant: String) -> f64 {
    // Estimate runtime memory (GB) from file size + overhead.
    let mut base_gb = (size_mb / 1024);
    let mut overhead = if (quant.to_uppercase().contains(&"Q4".to_string()) || quant.to_uppercase().contains(&"Q3".to_string())) { 1.3_f64 } else { 1.2_f64 };
    (((base_gb * overhead) as f64) * 10f64.powi(1)).round() / 10f64.powi(1)
}

/// Recommend quantization level based on available RAM/VRAM.
pub fn quantization_advisor(memory_gb: f64, vram_gb: f64) -> HashMap {
    // Recommend quantization level based on available RAM/VRAM.
    let mut total = if vram_gb > 0 { vram_gb } else { memory_gb };
    if total >= 32 {
        HashMap::from([("recommended".to_string(), "Q8_0".to_string()), ("max_params".to_string(), "13B".to_string()), ("note".to_string(), "High-quality Q8 or F16 for ≤7B".to_string())])
    } else if total >= 16 {
        HashMap::from([("recommended".to_string(), "Q6_K".to_string()), ("max_params".to_string(), "7B".to_string()), ("note".to_string(), "Q6_K best quality-per-bit. Q4_K_M for 13B.".to_string())])
    } else if total >= 8 {
        HashMap::from([("recommended".to_string(), "Q4_K_M".to_string()), ("max_params".to_string(), "7B".to_string()), ("note".to_string(), "Q4_K_M balances quality and speed at 7B".to_string())])
    } else if total >= 4 {
        HashMap::from([("recommended".to_string(), "Q4_K_S".to_string()), ("max_params".to_string(), "3B".to_string()), ("note".to_string(), "Q4 small quants for ≤3B models".to_string())])
    } else {
        HashMap::from([("recommended".to_string(), "Q3_K_S".to_string()), ("max_params".to_string(), "1B".to_string()), ("note".to_string(), "Only tiny quantized models fit".to_string())])
    }
}

pub fn _normalize_perf_profile(name: Option<String>) -> String {
    let mut profile = (name || "balanced".to_string()).to_string().trim().to_string().to_lowercase();
    if _PERF_PROFILES.contains(&profile) { profile } else { "balanced".to_string() }
}

/// Estimate model runtime memory footprint in GB for scheduling.
pub fn _estimate_runtime_gb_for_path(path: String, n_ctx: i64) -> Result<f64> {
    // Estimate model runtime memory footprint in GB for scheduling.
    // try:
    {
        let mut size_mb = ((os::path.getsize(path) / (1024 * 1024)) as f64).round();
    }
    // except OSError as _e:
    let mut meta = _infer_metadata_from_filename(path);
    let mut quant = meta.get(&"quantization".to_string()).cloned().unwrap_or("".to_string()).to_string();
    let mut base = estimate_model_memory_gb(size_mb, quant);
    let mut ctx_factor = (1.0_f64 + (0.0_f64.max(((n_ctx - 4096) / 4096.0_f64)) * 0.18_f64));
    Ok((((base * ctx_factor) as f64) * 10f64.powi(2)).round() / 10f64.powi(2))
}

/// Adaptive n_batch by model size. Lower for large models to avoid stalls.
pub fn _choose_n_batch(model_size_mb: i64, perf_profile: String) -> i64 {
    // Adaptive n_batch by model size. Lower for large models to avoid stalls.
    let mut profile = _PERF_PROFILES[&_normalize_perf_profile(perf_profile)];
    if model_size_mb >= 14000 {
        let mut base = 64;
    } else if model_size_mb >= 8000 {
        let mut base = 96;
    } else if model_size_mb >= 4000 {
        let mut base = 128;
    } else if model_size_mb >= 1000 {
        let mut base = 192;
    } else {
        let mut base = 256;
    }
    let mut boosted = (base * profile["batch_boost".to_string()].to_string().parse::<f64>().unwrap_or(0.0)).to_string().parse::<i64>().unwrap_or(0);
    48.max(384.min(boosted))
}

/// Cap requested context to model-trained context length when available.
pub fn _effective_n_ctx_for_path(path: String, requested_n_ctx: i64, perf_profile: String) -> i64 {
    // Cap requested context to model-trained context length when available.
    let mut profile = _PERF_PROFILES[&_normalize_perf_profile(perf_profile)];
    if !(profile.get(&"ctx_cap".to_string()).cloned().unwrap_or(true) != 0) {
        requested_n_ctx
    }
    let mut meta = _extract_gguf_metadata(path);
    let mut model_ctx = (meta.get(&"context_length".to_string()).cloned().unwrap_or(0) || 0).to_string().parse::<i64>().unwrap_or(0);
    if model_ctx > 0 {
        256.max(requested_n_ctx.min(model_ctx))
    }
    requested_n_ctx
}

/// Return (max_workers, threads_per_model), tuned by CPU + available RAM.
pub fn _compute_parallel_plan(model_paths: Vec<String>, n_ctx: i64, perf_profile: String) -> Result<(i64, i64)> {
    // Return (max_workers, threads_per_model), tuned by CPU + available RAM.
    let mut profile = _PERF_PROFILES[&_normalize_perf_profile(perf_profile)];
    let mut total = 1.max(model_paths.len());
    let mut cpu_count = 2.max((os::cpu_count() || 4));
    let mut min_thr = std::env::var(&"LLM_MIN_THREADS_PER_MODEL".to_string()).unwrap_or_default().cloned().unwrap_or(profile["min_threads_per_model".to_string()].to_string()).to_string().parse::<i64>().unwrap_or(0);
    let mut cpu_worker_cap = 1.max((cpu_count / 2.max(min_thr)));
    if HAS_PSUTIL {
        // try:
        {
            let mut avail_ram_gb = 1.0_f64.max(((psutil.virtual_memory().available / (1024).pow(3 as u32)) * profile["ram_util".to_string()].to_string().parse::<f64>().unwrap_or(0.0)));
        }
        // except Exception as _e:
    } else {
        let mut avail_ram_gb = 1.0_f64.max((get_memory_gb() * profile["ram_util".to_string()].to_string().parse::<f64>().unwrap_or(0.0)));
    }
    let mut est_gb = (model_paths.iter().map(|p| _estimate_runtime_gb_for_path(p, n_ctx)).collect::<Vec<_>>() || vec![1.0_f64]);
    let mut avg_est = 0.5_f64.max((est_gb.iter().sum::<i64>() / est_gb.len()));
    let mut ram_worker_cap = 1.max((avail_ram_gb / (avg_est * 1.1_f64)).to_string().parse::<i64>().unwrap_or(0));
    let mut hard_cap = std::env::var(&"LLM_MAX_WORKERS".to_string()).unwrap_or_default().cloned().unwrap_or(total.to_string()).to_string().parse::<i64>().unwrap_or(0);
    let mut max_workers = 1.max(min(total, cpu_worker_cap, ram_worker_cap, hard_cap));
    let mut max_threads_cap = 2.max(std::env::var(&"LLM_MAX_THREADS_PER_MODEL".to_string()).unwrap_or_default().cloned().unwrap_or(profile["max_threads_per_model".to_string()].to_string()).to_string().parse::<i64>().unwrap_or(0));
    let mut min_threads_cap = 2.max(std::env::var(&"LLM_MIN_THREADS_PER_MODEL".to_string()).unwrap_or_default().cloned().unwrap_or(profile["min_threads_per_model".to_string()].to_string()).to_string().parse::<i64>().unwrap_or(0));
    let mut threads_per_model = min_threads_cap.max(max_threads_cap.min((cpu_count / max_workers)));
    Ok((max_workers, threads_per_model))
}

/// Return a cached Llama model or load a new one. Thread-safe LRU cache.
/// 
/// If *draft_model* is set, enables speculative decoding (E5).
pub fn _get_or_load_model(path: String, n_ctx: i64, draft_model: String, n_threads_override: Option<i64>, n_batch_override: Option<i64>) -> Result<()> {
    // Return a cached Llama model or load a new one. Thread-safe LRU cache.
    // 
    // If *draft_model* is set, enables speculative decoding (E5).
    // TODO: import llama_cpp
    let mut thread_key = if n_threads_override.is_some() { n_threads_override } else { "auto".to_string() };
    let mut batch_key = if n_batch_override.is_some() { n_batch_override } else { "auto".to_string() };
    let mut cache_key = (format!("{}::ctx{}::thr={}::batch={}", path, n_ctx, thread_key, batch_key) + if draft_model { format!("::draft={}", draft_model) } else { "".to_string() });
    let _loader = || {
        let mut n_threads = (n_threads_override || std::env::var(&"LLM_THREADS".to_string()).unwrap_or_default().cloned().unwrap_or(2.max(((os::cpu_count() || 4) / 2)).to_string()).to_string().parse::<i64>().unwrap_or(0));
        // try:
        {
            let mut model_size_mb = ((os::path.getsize(path) / (1024 * 1024)) as f64).round();
        }
        // except OSError as _e:
        let mut n_batch = (n_batch_override || std::env::var(&"LLM_N_BATCH".to_string()).unwrap_or_default().cloned().unwrap_or(_choose_n_batch(model_size_mb).to_string()).to_string().parse::<i64>().unwrap_or(0));
        let mut n_gpu_layers = std::env::var(&"LLM_N_GPU_LAYERS".to_string()).unwrap_or_default().cloned().unwrap_or("0".to_string()).to_string().parse::<i64>().unwrap_or(0);
        let mut kwargs = /* dict(/* model_path= */ path, /* n_ctx= */ n_ctx, /* n_threads= */ 1.max(n_threads), /* n_gpu_layers= */ n_gpu_layers, /* flash_attn= */ n_gpu_layers != 0, /* n_batch= */ n_batch, /* use_mmap= */ true, /* use_mlock= */ false, /* verbose= */ false) */ HashMap::new();
        if (draft_model && os::path.isfile(draft_model)) {
            // try:
            {
                kwargs["draft_model".to_string()] = llama_cpp.LlamaDraftModel(/* model_path= */ draft_model, /* num_pred_tokens= */ 8);
                println!("[spec] Using draft model: {}", os::path.basename(draft_model));
            }
            // except Exception as e:
        }
        let _ctx = _llama_load_lock;
        {
            llama_cpp.Llama(/* ** */ kwargs)
        }
    };
    Ok(_model_cache.get_or_load(cache_key, _loader))
}

/// Clear entire model cache (call before judge or when memory is needed).
pub fn _evict_model_cache() -> () {
    // Clear entire model cache (call before judge or when memory is needed).
    _model_cache.clear();
    gc.collect();
}

/// Build chat messages, folding system prompt into user message for models
/// that don't support the system role (e.g. Gemma, Olmo).
pub fn _build_messages(system_prompt: String, user_content: String, model_path: String) -> Vec<HashMap> {
    // Build chat messages, folding system prompt into user message for models
    // that don't support the system role (e.g. Gemma, Olmo).
    let mut name_lower = os::path.basename(model_path).to_lowercase();
    let mut no_system = ("gemma".to_string(), "olmo".to_string(), "codelama".to_string()).iter().map(|t| name_lower.contains(&t)).collect::<Vec<_>>().iter().any(|v| *v);
    if (no_system || !system_prompt.trim().to_string()) {
        let mut combined = if system_prompt.trim().to_string() { format!("{}\n\n{}", system_prompt.trim().to_string(), user_content) } else { user_content };
        vec![HashMap::from([("role".to_string(), "user".to_string()), ("content".to_string(), combined)])]
    }
    vec![HashMap::from([("role".to_string(), "system".to_string()), ("content".to_string(), system_prompt)]), HashMap::from([("role".to_string(), "user".to_string()), ("content".to_string(), user_content)])]
}

/// Detect GPUs. Returns list of dicts with name/vendor/vram_gb/backend.
pub fn get_gpu_info() -> Result<Vec<HashMap>> {
    // Detect GPUs. Returns list of dicts with name/vendor/vram_gb/backend.
    let mut gpus = vec![];
    // try:
    {
        // TODO: import subprocess
        let mut out = subprocess::check_output(vec!["nvidia-smi".to_string(), "--query-gpu=name,memory.total".to_string(), "--format=csv,noheader,nounits".to_string()], /* timeout= */ 5, /* stderr= */ subprocess::DEVNULL, /* text= */ true);
        for line in out.trim().to_string().lines().map(|s| s.to_string()).collect::<Vec<String>>().iter() {
            let mut parts = line.split(",".to_string()).map(|s| s.to_string()).collect::<Vec<String>>().iter().map(|p| p.trim().to_string()).collect::<Vec<_>>();
            if parts.len() >= 2 {
                gpus.push(HashMap::from([("name".to_string(), parts[0]), ("vendor".to_string(), "NVIDIA".to_string()), ("vram_gb".to_string(), (((parts[1].to_string().parse::<f64>().unwrap_or(0.0) / 1024) as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("backend".to_string(), "CUDA".to_string())]));
            }
        }
    }
    // except Exception as _e:
    if (!gpus && sys::platform == "win32".to_string()) {
        // try:
        {
            // TODO: import subprocess
            let mut out = subprocess::check_output(vec!["wmic".to_string(), "path".to_string(), "win32_videocontroller".to_string(), "get".to_string(), "Name,AdapterRAM".to_string(), "/format:csv".to_string()], /* timeout= */ 5, /* stderr= */ subprocess::DEVNULL, /* text= */ true);
            for line in out.trim().to_string().lines().map(|s| s.to_string()).collect::<Vec<String>>()[1..].iter() {
                let mut parts = line.split(",".to_string()).map(|s| s.to_string()).collect::<Vec<String>>().iter().map(|p| p.trim().to_string()).collect::<Vec<_>>();
                if parts.len() >= 3 {
                    let mut vram = if parts[1].chars().all(|c| c.is_ascii_digit()) { (parts[1].to_string().parse::<i64>().unwrap_or(0) / (1024).pow(3 as u32)) } else { 0 };
                    let mut name = parts[2];
                    let mut vendor = if (name.to_uppercase().contains(&"AMD".to_string()) || name.to_uppercase().contains(&"RADEON".to_string())) { "AMD".to_string() } else { if name.to_uppercase().contains(&"NVIDIA".to_string()) { "NVIDIA".to_string() } else { if name.to_uppercase().contains(&"INTEL".to_string()) { "Intel".to_string() } else { "Unknown".to_string() } } };
                    let mut backend = if vendor == "NVIDIA".to_string() { "CUDA".to_string() } else { if vendor == "AMD".to_string() { "ROCm/Vulkan".to_string() } else { "DirectML".to_string() } };
                    gpus.push(HashMap::from([("name".to_string(), name), ("vendor".to_string(), vendor), ("vram_gb".to_string(), ((vram as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("backend".to_string(), backend)]));
                }
            }
        }
        // except Exception as _e:
    }
    Ok(gpus)
}

/// Recommend best llama.cpp build based on detected hardware.
pub fn recommend_llama_build(cpu: Option<HashMap>, gpus: Option<Vec>) -> HashMap {
    // Recommend best llama.cpp build based on detected hardware.
    let mut rec = HashMap::from([("build".to_string(), "CPU (OpenBLAS)".to_string()), ("flag".to_string(), "cpu".to_string()), ("pip".to_string(), "llama-cpp-python".to_string())]);
    if gpus {
        for g in if /* /* isinstance(gpus, list) */ */ true { gpus } else { vec![] }.iter() {
            let mut gd = if /* /* isinstance(g, dict) */ */ true { g } else { HashMap::new() };
            let mut backend = gd.get(&"backend".to_string()).cloned().unwrap_or("".to_string());
            let mut vendor = gd.get(&"vendor".to_string()).cloned().unwrap_or("".to_string()).to_uppercase();
            if (backend.contains(&"CUDA".to_string()) || vendor.contains(&"NVIDIA".to_string())) {
                let mut rec = HashMap::from([("build".to_string(), "CUDA (GPU)".to_string()), ("flag".to_string(), "cuda".to_string()), ("pip".to_string(), "llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124".to_string())]);
                break;
            }
            if (backend.contains(&"ROCm".to_string()) || backend.contains(&"Vulkan".to_string()) || vendor.contains(&"AMD".to_string())) {
                let mut rec = HashMap::from([("build".to_string(), "Vulkan (GPU)".to_string()), ("flag".to_string(), "rocm".to_string()), ("pip".to_string(), "llama-cpp-python (build with CMAKE_ARGS=-DGGML_VULKAN=on)".to_string())]);
                break;
            }
        }
    } else if cpu {
        if cpu.get(&"avx512".to_string()).cloned() {
            let mut rec = HashMap::from([("build".to_string(), "CPU (AVX-512)".to_string()), ("flag".to_string(), "avx512".to_string()), ("pip".to_string(), "llama-cpp-python".to_string())]);
        } else if cpu.get(&"avx2".to_string()).cloned() {
            let mut rec = HashMap::from([("build".to_string(), "CPU (AVX2)".to_string()), ("flag".to_string(), "avx2".to_string()), ("pip".to_string(), "llama-cpp-python".to_string())]);
        }
    }
    rec
}

/// Return comprehensive system info dict for the frontend.
pub fn get_system_info(model_dirs: Option<Vec<String>>) -> HashMap {
    // Return comprehensive system info dict for the frontend.
    let mut cpu = get_cpu_info();
    let mut gpus = get_gpu_info();
    let mut models = scan_gguf_models(model_dirs);
    let mut llama = get_llama_cpp_info();
    let mut build_rec = recommend_llama_build(cpu, gpus);
    let mut mem_gb = ((get_memory_gb() as f64) * 10f64.powi(1)).round() / 10f64.powi(1);
    let mut vram_gb = gpus.iter().map(|g| g.get(&"vram_gb".to_string()).cloned().unwrap_or(0)).collect::<Vec<_>>().iter().sum::<i64>();
    let mut quant_advice = quantization_advisor(mem_gb, vram_gb);
    for m in models.iter() {
        let mut est_mem = estimate_model_memory_gb(m.get(&"size_mb".to_string()).cloned().unwrap_or(0), m.get(&"quantization".to_string()).cloned().unwrap_or("".to_string()));
        m["estimated_memory_gb".to_string()] = est_mem;
        m["fits_ram".to_string()] = est_mem <= mem_gb;
        m["fits_vram".to_string()] = if vram_gb > 0 { est_mem <= vram_gb } else { false };
    }
    HashMap::from([("cpu_count".to_string(), cpu.get(&"cores".to_string()).cloned().unwrap_or(get_cpu_count())), ("cpu_name".to_string(), cpu.get(&"name".to_string()).cloned().unwrap_or("".to_string())), ("cpu_brand".to_string(), cpu.get(&"brand".to_string()).cloned().unwrap_or("Unknown".to_string())), ("memory_gb".to_string(), mem_gb), ("gpus".to_string(), gpus), ("vram_gb".to_string(), ((vram_gb as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("models".to_string(), models), ("model_count".to_string(), models.len()), ("recommended_build".to_string(), build_rec), ("quant_advice".to_string(), quant_advice), ("has_llama_cpp".to_string(), llama["installed".to_string()]), ("llama_cpp_version".to_string(), llama["version".to_string()]), ("timestamp".to_string(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64())])
}

/// Return llama.cpp version and recommended build for this hardware.
pub fn get_llama_cpp_info() -> Result<HashMap> {
    // Return llama.cpp version and recommended build for this hardware.
    let mut installed = false;
    let mut version = None;
    // try:
    {
        // TODO: import llama_cpp
        let mut installed = true;
        let mut version = (/* getattr */ "installed".to_string() || "installed".to_string());
    }
    // except Exception as _e:
    Ok(HashMap::from([("installed".to_string(), installed), ("version".to_string(), version)]))
}

/// Create results + ELO tables if they don't exist.
pub fn _db_init() -> Result<()> {
    // Create results + ELO tables if they don't exist.
    let _ctx = _db_lock;
    {
        let mut con = /* sqlite3 */ _DB_PATH;
        con.executescript("\n            CREATE TABLE IF NOT EXISTS results (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                prompt TEXT NOT NULL,\n                judge_model TEXT,\n                timestamp REAL NOT NULL,\n                payload TEXT NOT NULL\n            );\n            CREATE TABLE IF NOT EXISTS elo (\n                model TEXT PRIMARY KEY,\n                rating REAL NOT NULL DEFAULT 1500,\n                wins INTEGER NOT NULL DEFAULT 0,\n                losses INTEGER NOT NULL DEFAULT 0,\n                draws INTEGER NOT NULL DEFAULT 0,\n                matches INTEGER NOT NULL DEFAULT 0,\n                last_updated REAL\n            );\n            CREATE INDEX IF NOT EXISTS idx_results_ts ON results(timestamp);\n        ".to_string());
        con.close();
    }
}

/// Persist a comparison result. Returns row id.
pub fn db_save_result(prompt: String, judge_model: Option<String>, responses: Vec<HashMap>, ts: f64) -> Result<i64> {
    // Persist a comparison result. Returns row id.
    let mut payload = serde_json::to_string(&HashMap::from([("responses".to_string(), responses)])).unwrap();
    let _ctx = _db_lock;
    {
        let mut con = /* sqlite3 */ _DB_PATH;
        let mut cur = con.execute("INSERT INTO results (prompt, judge_model, timestamp, payload) VALUES (?,?,?,?)".to_string(), (prompt, (judge_model || "".to_string()), ts, payload));
        let mut rid = cur.lastrowid;
        con.commit();
        con.close();
    }
    Ok((rid || 0))
}

/// Retrieve recent results.
pub fn db_get_results(limit: i64, offset: i64) -> Result<Vec<HashMap>> {
    // Retrieve recent results.
    let _ctx = _db_lock;
    {
        let mut con = /* sqlite3 */ _DB_PATH;
        con.row_factory = sqlite3::Row;
        let mut rows = con.execute("SELECT * FROM results ORDER BY timestamp DESC LIMIT ? OFFSET ?".to_string(), (limit, offset)).fetchall();
        con.close();
    }
    let mut out = vec![];
    for r in rows.iter() {
        let mut entry = /* dict(r) */ HashMap::new();
        // try:
        {
            entry["payload".to_string()] = serde_json::from_str(&entry["payload".to_string()]).unwrap();
        }
        // except Exception as _e:
        out.push(entry);
    }
    Ok(out)
}

/// Return all ELO rankings sorted by rating descending.
pub fn db_get_elo() -> Result<Vec<HashMap>> {
    // Return all ELO rankings sorted by rating descending.
    let _ctx = _db_lock;
    {
        let mut con = /* sqlite3 */ _DB_PATH;
        con.row_factory = sqlite3::Row;
        let mut rows = con.execute("SELECT * FROM elo ORDER BY rating DESC".to_string()).fetchall();
        con.close();
    }
    Ok(rows.iter().map(|r| /* dict(r) */ HashMap::new()).collect::<Vec<_>>())
}

/// Update ELO ratings from comparison results. Best score wins.
pub fn db_update_elo(responses: Vec<HashMap>) -> Result<()> {
    // Update ELO ratings from comparison results. Best score wins.
    let mut scored = responses.iter().filter(|r| (!r.get(&"error".to_string()).cloned() && r.get(&"judge_score".to_string()).cloned().unwrap_or(0) > 0)).map(|r| r).collect::<Vec<_>>();
    if scored.len() < 2 {
        return;
    }
    scored.sort(/* key= */ |r| r.get(&"judge_score".to_string()).cloned().unwrap_or(0), /* reverse= */ true);
    let mut K = 32;
    let mut now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    let _ctx = _db_lock;
    {
        let mut con = /* sqlite3 */ _DB_PATH;
        for r in scored.iter() {
            con.execute("INSERT OR IGNORE INTO elo (model, rating, wins, losses, draws, matches, last_updated) VALUES (?,1500,0,0,0,0,?)".to_string(), (r["model".to_string()], now));
        }
        let mut ratings = HashMap::new();
        for r in scored.iter() {
            let mut row = con.execute("SELECT rating FROM elo WHERE model=?".to_string(), (r["model".to_string()])).fetchone();
            ratings[r["model".to_string()]] = if row { row[0] } else { 1500.0_f64 };
        }
        for (i, a) in scored.iter().enumerate().iter() {
            for b in scored[(i + 1)..].iter() {
                let (mut ra, mut rb) = (ratings[a["model".to_string()]], ratings[b["model".to_string()]]);
                let mut ea = (1 / (1 + (10).pow(((rb - ra) / 400) as u32)));
                let mut eb = (1 - ea);
                let mut sa_score = a.get(&"judge_score".to_string()).cloned().unwrap_or(0);
                let mut sb_score = b.get(&"judge_score".to_string()).cloned().unwrap_or(0);
                if sa_score > sb_score {
                    let (mut sa, mut sb) = (1.0_f64, 0.0_f64);
                    con.execute("UPDATE elo SET wins=wins+1, matches=matches+1, last_updated=? WHERE model=?".to_string(), (now, a["model".to_string()]));
                    con.execute("UPDATE elo SET losses=losses+1, matches=matches+1, last_updated=? WHERE model=?".to_string(), (now, b["model".to_string()]));
                } else if sb_score > sa_score {
                    let (mut sa, mut sb) = (0.0_f64, 1.0_f64);
                    con.execute("UPDATE elo SET losses=losses+1, matches=matches+1, last_updated=? WHERE model=?".to_string(), (now, a["model".to_string()]));
                    con.execute("UPDATE elo SET wins=wins+1, matches=matches+1, last_updated=? WHERE model=?".to_string(), (now, b["model".to_string()]));
                } else {
                    let (mut sa, mut sb) = (0.5_f64, 0.5_f64);
                    con.execute("UPDATE elo SET draws=draws+1, matches=matches+1, last_updated=? WHERE model=?".to_string(), (now, a["model".to_string()]));
                    con.execute("UPDATE elo SET draws=draws+1, matches=matches+1, last_updated=? WHERE model=?".to_string(), (now, b["model".to_string()]));
                }
                ratings[a["model".to_string()]] = (ra + (K * (sa - ea)));
                ratings[b["model".to_string()]] = (rb + (K * (sb - eb)));
            }
        }
        for (model, rating) in ratings.iter().iter() {
            con.execute("UPDATE elo SET rating=? WHERE model=?".to_string(), (((rating as f64) * 10f64.powi(1)).round() / 10f64.powi(1), model));
        }
        con.commit();
        con.close();
    }
}

/// Reset all ELO ratings.
pub fn db_clear_elo() -> Result<()> {
    // Reset all ELO ratings.
    let _ctx = _db_lock;
    {
        let mut con = /* sqlite3 */ _DB_PATH;
        con.execute("DELETE FROM elo".to_string());
        con.commit();
        con.close();
    }
}

/// Extract evaluation scores from judge LLM output.
/// 
/// Handles:
/// 1. Clean JSON
/// 2. JSON in markdown fences
/// 3. Nested JSON ({"evaluation": {...}})
/// 4. Partial / malformed JSON
/// 5. Natural-language scores ("overall: 8/10")
/// 6. Total garbage → {"overall": 0}
/// 
/// Returns a dict always containing at least the key ``overall`` (0-10).
pub fn extract_judge_scores(raw_text: String) -> HashMap {
    // Extract evaluation scores from judge LLM output.
    // 
    // Handles:
    // 1. Clean JSON
    // 2. JSON in markdown fences
    // 3. Nested JSON ({"evaluation": {...}})
    // 4. Partial / malformed JSON
    // 5. Natural-language scores ("overall: 8/10")
    // 6. Total garbage → {"overall": 0}
    // 
    // Returns a dict always containing at least the key ``overall`` (0-10).
    if (!raw_text || !raw_text.trim().to_string()) {
        HashMap::from([("overall".to_string(), 0)])
    }
    let mut raw = raw_text.trim().to_string();
    let mut fence_match = regex::Regex::new(&"```(?:json)?\\s*(\\{.*?\\})\\s*```".to_string()).unwrap().is_match(&raw);
    let mut json_str = if fence_match { fence_match.group(1) } else { raw };
    let mut parsed = _try_json(json_str);
    if parsed.is_none() {
        let mut brace_match = regex::Regex::new(&"\\{[^{}]*\\}".to_string()).unwrap().is_match(&raw);
        if brace_match {
            let mut parsed = _try_json(brace_match.group(0));
        }
    }
    if parsed.is_some() {
        if !parsed.contains(&"overall".to_string()) {
            for v in parsed.values().iter() {
                if (/* /* isinstance(v, dict) */ */ true && v.contains(&"overall".to_string())) {
                    let mut parsed = v;
                    break;
                }
            }
        }
    }
    if parsed.is_none() {
        let mut parsed = _extract_scores_regex(raw);
    }
    let mut result = _normalise_scores((parsed || HashMap::new()));
    result
}

/// Try to json::loads *text*, return dict or None.
pub fn _try_json(text: String) -> Result<Option<HashMap>> {
    // Try to json::loads *text*, return dict or None.
    // try:
    {
        let mut obj = serde_json::from_str(&text).unwrap();
        if /* /* isinstance(obj, dict) */ */ true {
            obj
        }
    }
    // except (json::JSONDecodeError, ValueError) as _e:
    // try:
    {
        let mut fixed = regex::Regex::new(&"(?<={|,)\\s*(\\w+)\\s*:".to_string()).unwrap().replace_all(&" \"\\1\":".to_string(), text).to_string();
        let mut obj = serde_json::from_str(&fixed).unwrap();
        if /* /* isinstance(obj, dict) */ */ true {
            obj
        }
    }
    // except Exception as _e:
    None
}

/// Extract scores from natural-language judge output.
pub fn _extract_scores_regex(text: String) -> HashMap {
    // Extract scores from natural-language judge output.
    let mut result = HashMap::new();
    let mut lower = text.to_lowercase();
    let mut patterns = vec![("overall[\\s:=]+(?:is[\\s:]+)?(\\d+(?:\\.\\d+)?)\\s*(?:/\\s*10|out\\s+of\\s+10)?".to_string(), "overall".to_string()), ("accuracy[\\s:=]+(?:is[\\s:]+)?(\\d+(?:\\.\\d+)?)\\s*(?:/\\s*10|out\\s+of\\s+10)?".to_string(), "accuracy".to_string()), ("reasoning[\\s:=]+(?:is[\\s:]+)?(\\d+(?:\\.\\d+)?)\\s*(?:/\\s*10|out\\s+of\\s+10)?".to_string(), "reasoning".to_string()), ("instruction.?following[\\s:=]+(?:is[\\s:]+)?(true|false|\\d+(?:\\.\\d+)?)".to_string(), "instruction_following".to_string()), ("safety[\\s:=]+(?:is[\\s:]+)?[\\\"']?(safe|unsafe|refused)[\\\"']?".to_string(), "safety".to_string()), ("conciseness[\\s:=]+(?:is[\\s:]+)?(\\d+(?:\\.\\d+)?)\\s*(?:/\\s*10)?".to_string(), "conciseness".to_string()), ("multilingual[\\s:=]+(?:is[\\s:]+)?(\\d+(?:\\.\\d+)?)\\s*(?:/\\s*10)?".to_string(), "multilingual".to_string())];
    for (pattern, key) in patterns.iter() {
        let mut m = regex::Regex::new(&pattern).unwrap().is_match(&lower);
        if m {
            result[key] = m.group(1).to_string().parse::<f64>().unwrap_or(0.0);
        }
    }
    if (!result.contains(&"overall".to_string()) && result) {
        let mut nums = result.values().iter().filter(|v| /* /* isinstance(v, (int, float) */) */ true).map(|v| v).collect::<Vec<_>>();
        if nums {
            result["overall".to_string()] = (((nums.iter().sum::<i64>() / nums.len()) as f64) * 10f64.powi(1)).round() / 10f64.powi(1);
        }
    }
    if !result.contains(&"overall".to_string()) {
        let mut m = regex::Regex::new(&"score[:\\s]*(\\d+(?:\\.\\d+)?)".to_string()).unwrap().is_match(&lower);
        if m {
            result["overall".to_string()] = m.group(1).to_string().parse::<f64>().unwrap_or(0.0);
        }
    }
    if !result.contains(&"overall".to_string()) {
        result["overall".to_string()] = 0;
    }
    result
}

/// Ensure 'overall' exists, parse string scores, clamp to 0-10.
pub fn _normalise_scores(d: HashMap<String, serde_json::Value>) -> Result<HashMap> {
    // Ensure 'overall' exists, parse string scores, clamp to 0-10.
    let mut result = HashMap::new();
    for (k, v) in d.iter().iter() {
        if /* /* isinstance(v, str) */ */ true {
            let mut m = regex::Regex::new(&"(\\d+(?:\\.\\d+)?)\\s*/\\s*\\d+".to_string()).unwrap().is_match(&v);
            if m {
                let mut v = m.group(1).to_string().parse::<f64>().unwrap_or(0.0);
            } else {
                // try:
                {
                    let mut v = v.to_string().parse::<f64>().unwrap_or(0.0);
                }
                // except (ValueError, TypeError) as _e:
            }
        }
        if /* /* isinstance(v, (int, float) */) */ true {
            let mut v = 0.0_f64.max(10.0_f64.min(v.to_string().parse::<f64>().unwrap_or(0.0)));
        }
        result[k] = v;
    }
    if !result.contains(&"overall".to_string()) {
        let mut nums = result.values().iter().filter(|v| /* /* isinstance(v, (int, float) */) */ true).map(|v| v).collect::<Vec<_>>();
        result["overall".to_string()] = if nums { (((nums.iter().sum::<i64>() / nums.len()) as f64) * 10f64.powi(1)).round() / 10f64.powi(1) } else { 0 };
    }
    Ok(result)
}

/// Validate a download URL for safety (prevent SSRF).
/// 
/// Returns true only if the URL:
/// - Uses HTTPS scheme
/// - Targets an allowed host
/// - Does not resolve to a private/loopback IP
pub fn validate_download_url(url: String) -> Result<bool> {
    // Validate a download URL for safety (prevent SSRF).
    // 
    // Returns true only if the URL:
    // - Uses HTTPS scheme
    // - Targets an allowed host
    // - Does not resolve to a private/loopback IP
    // try:
    {
        let mut parsed = /* urlparse */ url;
    }
    // except Exception as _e:
    if !("https".to_string()).contains(&parsed.scheme) {
        false
    }
    let mut hostname = (parsed.hostname || "".to_string()).to_lowercase();
    if !hostname {
        false
    }
    // try:
    {
        let mut addr = ipaddress.ip_address(hostname);
        if (addr.is_private || addr.is_loopback || addr.is_reserved) {
            false
        }
    }
    // except ValueError as _e:
    if ("localhost".to_string(), "127.0.0.1".to_string(), "0.0.0.0".to_string(), "::1".to_string(), "[::1]".to_string()).contains(&hostname) {
        false
    }
    if !_ALLOWED_DOWNLOAD_HOSTS.contains(&hostname) {
        false
    }
    Ok(true)
}

/// Return system info, recomputing only after TTL expires.
pub fn get_system_info_cached(model_dirs: Vec<String>) -> HashMap {
    // Return system info, recomputing only after TTL expires.
    // global/nonlocal _sysinfo_cache
    let _ctx = _sysinfo_lock;
    {
        if _sysinfo_cache.is_some() {
            let mut age = (std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() - _sysinfo_cache.get(&"_cache_ts".to_string()).cloned().unwrap_or(0));
            if age < _SYSINFO_TTL {
                let mut fresh_models = scan_gguf_models(model_dirs);
                let mut mem_gb = _sysinfo_cache.get(&"memory_gb".to_string()).cloned().unwrap_or(8);
                let mut vram_gb = _sysinfo_cache.get(&"vram_gb".to_string()).cloned().unwrap_or(0);
                for m in fresh_models.iter() {
                    let mut est = estimate_model_memory_gb(m.get(&"size_mb".to_string()).cloned().unwrap_or(0), m.get(&"quantization".to_string()).cloned().unwrap_or("".to_string()));
                    m["estimated_memory_gb".to_string()] = est;
                    m["fits_ram".to_string()] = est <= mem_gb;
                    m["fits_vram".to_string()] = if vram_gb > 0 { est <= vram_gb } else { false };
                }
                let mut result = /* dict(_sysinfo_cache) */ HashMap::new();
                result["models".to_string()] = fresh_models;
                result["model_count".to_string()] = fresh_models.len();
                result
            }
        }
        let mut info = get_system_info(model_dirs);
        info["_cache_ts".to_string()] = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
        let mut _sysinfo_cache = info;
        info
    }
}

/// Search HuggingFace for GGUF models. Uses huggingface_hub API.
pub fn _discover_hf_models(query: String, sort: String, limit: i64) -> Result<Vec<HashMap>> {
    // Search HuggingFace for GGUF models. Uses huggingface_hub API.
    let mut cache_key = format!("{}|{}|{}", query, sort, limit);
    let _ctx = _discovery_lock;
    {
        let mut cached = _discovery_cache.get(&cache_key).cloned();
        if (cached && (std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() - cached["ts".to_string()]) < _DISCOVERY_TTL) {
            cached["data".to_string()]
        }
    }
    // try:
    {
        // TODO: from huggingface_hub import HfApi
        let mut api = HfApi();
        let mut kwargs = HashMap::from([("limit".to_string(), limit.min(60)), ("filter".to_string(), "gguf".to_string())]);
        if sort == "trending".to_string() {
            kwargs["sort".to_string()] = "trendingScore".to_string();
        } else if sort == "downloads".to_string() {
            kwargs["sort".to_string()] = "downloads".to_string();
        } else if sort == "newest".to_string() {
            kwargs["sort".to_string()] = "lastModified".to_string();
        } else if sort == "likes".to_string() {
            kwargs["sort".to_string()] = "likes".to_string();
        }
        if query.trim().to_string() {
            kwargs["search".to_string()] = query.trim().to_string();
        }
        let mut raw = api.list_models(/* ** */ kwargs).into_iter().collect::<Vec<_>>();
        let mut results = vec![];
        for m in raw.iter() {
            let mut author = if (m.id || "".to_string()).contains(&"/".to_string()) { (m.id || "".to_string()).split("/".to_string()).map(|s| s.to_string()).collect::<Vec<String>>()[0] } else { "".to_string() };
            results.push(HashMap::from([("id".to_string(), m.id), ("author".to_string(), author), ("trusted".to_string(), _TRUSTED_QUANTIZERS.contains(&author)), ("downloads".to_string(), (/* getattr */ 0 || 0)), ("likes".to_string(), (/* getattr */ 0 || 0)), ("lastModified".to_string(), (/* getattr */ "".to_string() || "".to_string()).to_string()), ("tags".to_string(), (/* getattr */ vec![] || vec![]).into_iter().collect::<Vec<_>>()), ("pipeline".to_string(), (/* getattr */ "".to_string() || "".to_string()))]));
        }
        let _ctx = _discovery_lock;
        {
            _discovery_cache[cache_key] = HashMap::from([("ts".to_string(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64()), ("data".to_string(), results)]);
        }
        results
    }
    // except Exception as exc:
}

/// Check that *path* is a .gguf file inside one of *model_dirs*.
/// 
/// Prevents path-traversal attacks (e.g. loading /etc/passwd via the API).
pub fn _is_safe_model_path(path: String, model_dirs: Vec<String>) -> Result<bool> {
    // Check that *path* is a .gguf file inside one of *model_dirs*.
    // 
    // Prevents path-traversal attacks (e.g. loading /etc/passwd via the API).
    if (!path || !path.to_lowercase().ends_with(&*".gguf".to_string())) {
        false
    }
    // try:
    {
        let mut real = os::path.realpath(path);
    }
    // except (OSError, ValueError) as _e:
    for d in model_dirs.iter() {
        // try:
        {
            if real.starts_with(&*(os::path.realpath(d) + os::sep)) {
                true
            }
        }
        // except (OSError, ValueError) as _e:
    }
    Ok(false)
}

/// Background download worker — updates _download_jobs[job_id].
pub fn _run_download(job_id: String, model: String, dest: String) -> Result<()> {
    // Background download worker — updates _download_jobs[job_id].
    let _upd = || {
        let _ctx = _download_lock;
        {
            _download_jobs[&job_id].extend(kw);
        }
    };
    _upd(/* state= */ "downloading".to_string(), /* progress= */ 5, /* message= */ "Starting…".to_string());
    // try:
    {
        std::fs::create_dir_all(dest, /* exist_ok= */ true).unwrap();
        // TODO: from huggingface_hub import hf_hub_download, snapshot_download
        if model.to_lowercase().starts_with(&*"http".to_string()) {
            if !validate_download_url(model) {
                _upd(/* state= */ "error".to_string(), /* progress= */ 0, /* message= */ "Download URL not allowed (must be HTTPS from trusted hosts)".to_string(), /* error= */ "URL validation failed".to_string());
                return;
            }
            // TODO: import urllib::request as _ur
            let mut filename = model.trim_end_matches(|c: char| "/".to_string().contains(c)).to_string().split("/".to_string()).map(|s| s.to_string()).collect::<Vec<String>>()[-1];
            let mut out_path = PathBuf::from(dest).join(filename);
            _upd(/* state= */ "downloading".to_string(), /* progress= */ 10, /* message= */ format!("Connecting to {}…", filename));
            let _reporthook = |block_num, block_size, total_size| {
                if total_size > 0 {
                    let mut pct = 99.min((((block_num * block_size) * 100) / total_size).to_string().parse::<i64>().unwrap_or(0));
                    _upd(/* progress= */ pct, /* message= */ format!("Downloading {}… {}%", filename, pct));
                }
            };
            _ur.urlretrieve(model, out_path, /* reporthook= */ _reporthook);
        } else if model.iter().filter(|v| **v == "/".to_string()).count() >= 2 {
            let mut parts = model.split("/".to_string(), 2);
            let mut repo_id = parts[..2].join(&"/".to_string());
            let mut filename = parts[2];
            _upd(/* state= */ "downloading".to_string(), /* progress= */ 15, /* message= */ format!("Fetching {} from {}…", filename, repo_id));
            let mut out_path = hf_hub_download(/* repo_id= */ repo_id, /* filename= */ filename, /* local_dir= */ dest);
        } else if model.iter().filter(|v| **v == "/".to_string()).count() == 1 {
            _upd(/* state= */ "downloading".to_string(), /* progress= */ 15, /* message= */ format!("Fetching repo metadata for {}…", model));
            let mut out_path = snapshot_download(/* repo_id= */ model, /* local_dir= */ PathBuf::from(dest).join(model.split("/".to_string()).map(|s| s.to_string()).collect::<Vec<String>>()[-1]), /* ignore_patterns= */ vec!["*.bin".to_string(), "*.pt".to_string(), "*.safetensors".to_string()]);
        } else {
            _upd(/* state= */ "error".to_string(), /* progress= */ 0, /* message= */ "Use format: owner/repo/file.gguf or a direct URL".to_string(), /* error= */ "Invalid format".to_string());
            return;
        }
        _upd(/* state= */ "done".to_string(), /* progress= */ 100, /* message= */ "Download complete".to_string(), /* path= */ out_path.to_string());
        println!("[download] {} DONE → {}", job_id, out_path);
    }
    // except Exception as exc:
}

/// Background install worker — updates _install_jobs[job_id] with live log.
pub fn _run_install(job_id: String, pip_cmd: String) -> Result<()> {
    // Background install worker — updates _install_jobs[job_id] with live log.
    // TODO: import shlex
    // TODO: import subprocess
    // TODO: import sys as _sys
    let _upd = || {
        let _ctx = _install_lock;
        {
            _install_jobs[&job_id].extend(kw);
        }
    };
    _upd(/* state= */ "running".to_string(), /* log= */ "".to_string(), /* error= */ "".to_string(), /* status_text= */ "Starting pip…".to_string());
    // try:
    {
        let mut parts = shlex.split(pip_cmd).map(|s| s.to_string()).collect::<Vec<String>>();
        if ("pip".to_string(), "pip3".to_string()).contains(&parts[0]) {
            let mut parts = (vec![_sys.executable, "-m".to_string(), "pip".to_string()] + parts[1..]);
        }
        let mut process = subprocess::Popen(parts, /* stdout= */ subprocess::PIPE, /* stderr= */ subprocess::STDOUT, /* text= */ true, /* bufsize= */ 1);
        let mut accumulated = "".to_string();
        for line in iter(process.stdout.readline, "".to_string()).iter() {
            accumulated += line;
            let mut short = if line.trim().to_string() { line.trim().to_string()[..100] } else { "Installing…".to_string() };
            _upd(/* log= */ accumulated, /* status_text= */ short);
        }
        process.wait();
        if process.returncode == 0 {
            _upd(/* state= */ "done".to_string(), /* status_text= */ "Installation complete!".to_string(), /* log= */ (accumulated + "\n✅ Done! Restart the backend to activate.".to_string()));
            println!("[install] {} DONE", job_id);
        } else {
            _upd(/* state= */ "error".to_string(), /* error= */ format!("pip exited with code {}", process.returncode), /* log= */ accumulated);
            println!("[install] {} FAILED (code {})", job_id, process.returncode);
        }
    }
    // except Exception as exc:
}

/// Discover trending GGUF models on HuggingFace, optionally filtered.
pub fn _scout_hf_trending(category: String, limit: i64) -> Result<Vec<HashMap>> {
    // Discover trending GGUF models on HuggingFace, optionally filtered.
    let mut cache_key = format!("scout|{}|{}", category, limit);
    let _ctx = _scout_lock;
    {
        let mut cached = _SCOUT_CACHE.get(&cache_key).cloned();
        if (cached && (std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() - cached["ts".to_string()]) < _SCOUT_TTL) {
            cached["data".to_string()]
        }
    }
    let mut results = vec![];
    // try:
    {
        // TODO: from huggingface_hub import HfApi
        let mut api = HfApi();
        if (category != "all".to_string() && _TOOL_CATEGORIES.contains(&category)) {
            let mut search_q = _TOOL_CATEGORIES[&category]["search".to_string()];
        } else {
            let mut search_q = "GGUF".to_string();
        }
        let mut raw = api.list_models(/* search= */ search_q, /* filter= */ "gguf".to_string(), /* sort= */ "trendingScore".to_string(), /* limit= */ limit.min(60)).into_iter().collect::<Vec<_>>();
        for m in raw.iter() {
            let mut author = if (m.id || "".to_string()).contains(&"/".to_string()) { (m.id || "".to_string()).split("/".to_string()).map(|s| s.to_string()).collect::<Vec<String>>()[0] } else { "".to_string() };
            let mut tags = (/* getattr */ vec![] || vec![]).into_iter().collect::<Vec<_>>();
            let mut downloads = (/* getattr */ 0 || 0);
            let mut likes = (/* getattr */ 0 || 0);
            let mut pipeline = (/* getattr */ "".to_string() || "".to_string());
            let mut caps = vec![];
            let mut tag_str = tags.join(&" ".to_string()).to_lowercase();
            if ("code".to_string(), "starcoder".to_string(), "codellama".to_string(), "deepseek-coder".to_string()).iter().map(|k| tag_str.contains(&k)).collect::<Vec<_>>().iter().any(|v| *v) {
                caps.push("code".to_string());
            }
            if ("medical".to_string(), "bio".to_string(), "clinical".to_string(), "med".to_string()).iter().map(|k| tag_str.contains(&k)).collect::<Vec<_>>().iter().any(|v| *v) {
                caps.push("medical".to_string());
            }
            if ("vision".to_string(), "multimodal".to_string(), "image".to_string(), "llava".to_string()).iter().map(|k| tag_str.contains(&k)).collect::<Vec<_>>().iter().any(|v| *v) {
                caps.push("vision".to_string());
            }
            if ("embedding".to_string(), "sentence".to_string(), "retrieval".to_string()).iter().map(|k| tag_str.contains(&k)).collect::<Vec<_>>().iter().any(|v| *v) {
                caps.push("embedding".to_string());
            }
            if ("whisper".to_string(), "speech".to_string(), "voice".to_string(), "audio".to_string()).iter().map(|k| tag_str.contains(&k)).collect::<Vec<_>>().iter().any(|v| *v) {
                caps.push("voice".to_string());
            }
            if ("math".to_string(), "reason".to_string(), "logic".to_string()).iter().map(|k| tag_str.contains(&k)).collect::<Vec<_>>().iter().any(|v| *v) {
                caps.push("reasoning".to_string());
            }
            if ("translation".to_string(), "multilingual".to_string(), "nllb".to_string()).iter().map(|k| tag_str.contains(&k)).collect::<Vec<_>>().iter().any(|v| *v) {
                caps.push("translate".to_string());
            }
            if ("function".to_string(), "tool".to_string(), "agent".to_string()).iter().map(|k| tag_str.contains(&k)).collect::<Vec<_>>().iter().any(|v| *v) {
                caps.push("agent".to_string());
            }
            if !caps {
                caps.push("chat".to_string());
            }
            results.push(HashMap::from([("id".to_string(), m.id), ("author".to_string(), author), ("trusted".to_string(), _TRUSTED_QUANTIZERS.contains(&author)), ("downloads".to_string(), downloads), ("likes".to_string(), likes), ("lastModified".to_string(), (/* getattr */ "".to_string() || "".to_string()).to_string()), ("tags".to_string(), tags), ("pipeline".to_string(), pipeline), ("capabilities".to_string(), caps), ("trending_score".to_string(), (/* getattr */ 0 || 0))]));
        }
        let _ctx = _scout_lock;
        {
            _SCOUT_CACHE[cache_key] = HashMap::from([("ts".to_string(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64()), ("data".to_string(), results)]);
        }
        results
    }
    // except Exception as exc:
}

/// Return curated list of AI tool categories with HF trending models.
pub fn _scout_tool_ecosystem() -> Result<HashMap> {
    // Return curated list of AI tool categories with HF trending models.
    let mut ecosystem = HashMap::new();
    // try:
    {
        // TODO: from huggingface_hub import HfApi
        let mut api = HfApi();
        for (cat_key, cat_info) in _TOOL_CATEGORIES.iter().iter() {
            // try:
            {
                let mut raw = api.list_models(/* search= */ cat_info["search".to_string()], /* filter= */ "gguf".to_string(), /* sort= */ "trendingScore".to_string(), /* limit= */ 5).into_iter().collect::<Vec<_>>();
                let mut models = vec![];
                for m in raw.iter() {
                    let mut author = if (m.id || "".to_string()).contains(&"/".to_string()) { (m.id || "".to_string()).split("/".to_string()).map(|s| s.to_string()).collect::<Vec<String>>()[0] } else { "".to_string() };
                    models.push(HashMap::from([("id".to_string(), m.id.to_string()), ("author".to_string(), author), ("trusted".to_string(), _TRUSTED_QUANTIZERS.contains(&author)), ("downloads".to_string(),
                }
                ecosystem[cat_key] = HashMap::from([("icon".to_string(), cat_info["icon".to_string()]), ("desc".to_string(), cat_info["desc".to_string()]), ("top_models".to_string(), models), ("count".to_string(), raw.len())]);
            }
            // except Exception as _e:
        }
        ecosystem
    }
    // except ImportError as _e:
    // except Exception as exc:
}

/// Start the HTTP server.
pub fn run_server(port: i64) -> Result<()> {
    // Start the HTTP server.
    if (sys::stdout && /* hasattr(sys::stdout, "reconfigure".to_string()) */ true) {
        // try:
        {
            sys::stdout.reconfigure(/* encoding= */ "utf-8".to_string(), /* errors= */ "replace".to_string());
        }
        // except Exception as _e:
    }
    if (sys::stderr && /* hasattr(sys::stderr, "reconfigure".to_string()) */ true) {
        // try:
        {
            sys::stderr.reconfigure(/* encoding= */ "utf-8".to_string(), /* errors= */ "replace".to_string());
        }
        // except Exception as _e:
    }
    init_db();
    println!("{}", "[zen_eval] Database initialized".to_string());
    let mut server = ThreadingHTTPServer::new(("127.0.0.1".to_string(), port), ComparatorHandler::new());
    println!("[OK] Comparator backend listening on http://127.0.0.1:{}", port);
    println!("{}", "   System info: /__system-info".to_string());
    println!("{}", "   Comparison:  /__comparison/mixed".to_string());
    let _warm_cache = || {
        // try:
        {
            let _warm_cache = || {
            println!("{}", "[cache] system-info warm-up done".to_string());
        }
        // except Exception as exc:
    };
    std::thread::spawn(|| {});
    Ok(server.serve_forever())
}