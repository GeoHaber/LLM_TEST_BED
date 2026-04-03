/// Headless LLM Integration Test
/// ==============================
/// Tests the full backend pipeline WITHOUT a browser:
/// 1. Backend health + system info
/// 2. Model scanning
/// 3. Actual inference on up to 6 GGUF models
/// 4. Timing + response quality assertions
/// 
/// Run:
/// python tests/test_llm_integration::py
/// python tests/test_llm_integration::py --models-dir "D:\Models" --max 3
/// python tests/test_llm_integration::py --live   # start backend first

use anyhow::{Result, Context};
use crate::comparator_backend as cb;
use std::collections::HashMap;
use std::collections::HashSet;

pub static REPO_ROOT: std::sync::LazyLock<String /* os::path.dirname */> = std::sync::LazyLock::new(|| Default::default());

pub const BACKEND_PORT: i64 = 18123;

pub const BACKEND_URL: &str = "f'http://127.0.0.1:{BACKEND_PORT}";

pub const PASS: &str = "\\x1b[92m[PASS]\\x1b[0m";

pub const FAIL: &str = "\\x1b[91m[FAIL]\\x1b[0m";

pub const WARN: &str = "\\x1b[93m[WARN]\\x1b[0m";

pub const INFO: &str = "\\x1b[94m[INFO]\\x1b[0m";

pub static _TEST_SERVER_STARTED: std::sync::LazyLock<std::sync::Condvar> = std::sync::LazyLock::new(|| Default::default());

pub static _TEST_MODEL_DIRS: std::sync::LazyLock<Vec<String>> = std::sync::LazyLock::new(|| Vec::new());

pub fn _get(path: String, timeout: i64) -> Result<HashMap> {
    let mut url = (BACKEND_URL + path);
    let mut resp = urllib::request.urlopen(url, /* timeout= */ timeout);
    {
        // try:
        {
            serde_json::from_str(&resp.read()).unwrap()
        }
        // except json::JSONDecodeError as _e:
    }
}

pub fn _post(path: String, payload: HashMap<String, serde_json::Value>, timeout: i64) -> Result<HashMap> {
    let mut data = serde_json::to_string(&payload).unwrap().as_bytes().to_vec();
    let mut req = urllib::request.Request((BACKEND_URL + path), /* data= */ data, /* headers= */ HashMap::from([("Content-Type".to_string(), "application/json".to_string())]), /* method= */ "POST".to_string());
    let mut resp = urllib::request.urlopen(req, /* timeout= */ timeout);
    {
        serde_json::from_str(&resp.read()).unwrap()
    }
}

pub fn _wait_backend(retries: i64, delay: f64) -> Result<bool> {
    for _ in 0..retries.iter() {
        // try:
        {
            _get("/__health".to_string(), /* timeout= */ 2);
            true
        }
        // except Exception as _e:
    }
    Ok(false)
}

/// Spin up the HTTP server in a daemon thread for this test run.
pub fn _start_backend_thread(model_dirs: Vec<String>) -> threading::Thread {
    // Spin up the HTTP server in a daemon thread for this test run.
    // TODO: from http::server import HTTPServer
    let mut server = HTTPServer(("127.0.0.1".to_string(), BACKEND_PORT), cb::ComparatorHandler);
    cb::ComparatorHandler.model_dirs = model_dirs;
    let mut t = std::thread::spawn(|| {});
    t.start();
    println!("  → Backend started on {}", BACKEND_URL);
    t
}

pub fn _check(condition: bool, label: String, detail: String) -> bool {
    let mut status = if condition { PASS } else { FAIL };
    let mut msg = format!("  {}  {}", status, label);
    if detail {
        msg += format!("\n         {}", detail);
    }
    println!("{}", msg);
    condition
}

/// Unit-test pure Python functions in comparator_backend (no HTTP).
pub fn _run_unit_tests() -> i64 {
    // Unit-test pure Python functions in comparator_backend (no HTTP).
    let mut failures = 0;
    println!("{}", "\n[1m── UNIT TESTS (pure functions) ──────────────────────────────[0m".to_string());
    let mut c = cb::get_cpu_count();
    let mut ok = (/* /* isinstance(c, int) */ */ true && c >= 1);
    failures += if _check(ok, format!("get_cpu_count() → {}", c)) { 0 } else { 1 };
    let mut mem = cb::get_memory_gb();
    let mut ok = (/* /* isinstance(mem, float) */ */ true && mem > 0);
    failures += if _check(ok, format!("get_memory_gb() → {:.1} GB", mem)) { 0 } else { 1 };
    let mut cpu = cb::get_cpu_info();
    let mut required = HashSet::from(["brand".to_string(), "name".to_string(), "cores".to_string(), "avx2".to_string(), "avx512".to_string()]);
    let mut ok = required.issubset(cpu.keys());
    failures += if _check(ok, format!("get_cpu_info() keys OK  brand={}  cores={}  avx2={}  avx512={}", cpu["brand".to_string()], cpu["cores".to_string()], cpu["avx2".to_string()], cpu["avx512".to_string()])) { 0 } else { 1 };
    let mut gpus = cb::get_gpu_info();
    let mut ok = /* /* isinstance(gpus, list) */ */ true;
    failures += if _check(ok, format!("get_gpu_info() → {} GPU(s) found", gpus.len())) { 0 } else { 1 };
    for g in gpus.iter() {
        println!("         {} {}  VRAM={} GB  backend={}", g["vendor".to_string()], g["name".to_string()], g.get(&"vram_gb".to_string()).cloned().unwrap_or("?".to_string()), g.get(&"backend".to_string()).cloned().unwrap_or("?".to_string()));
    }
    let mut llama = cb::get_llama_cpp_info();
    let mut ok = llama.contains(&"installed".to_string());
    failures += if _check(ok, format!("get_llama_cpp_info()  installed={}  version={}", llama["installed".to_string()], llama["version".to_string()])) { 0 } else { 1 };
    let mut rec = cb::recommend_llama_build(cpu, gpus);
    let mut ok = (rec.contains(&"pip".to_string()) && rec.contains(&"build".to_string()));
    failures += if _check(ok, format!("recommend_llama_build()  build={}", rec["build".to_string()])) { 0 } else { 1 };
    failures
}

/// Integration tests against the live backend HTTP server.
pub fn _run_endpoint_tests(model_dirs: Vec<String>) -> Result<i64> {
    // Integration tests against the live backend HTTP server.
    let mut failures = 0;
    println!("{}", "\n[1m── HTTP ENDPOINT TESTS ──────────────────────────────────────[0m".to_string());
    // try:
    {
        let mut resp = _get("/__health".to_string());
        failures += if _check(resp.get(&"ok".to_string()).cloned() == true, "/__health → {ok:true}".to_string()) { 0 } else { 1 };
    }
    // except Exception as e:
    // try:
    {
        let mut info = _get("/__system-info".to_string());
        let mut required_keys = HashSet::from(["cpu_count".to_string(), "memory_gb".to_string(), "has_llama_cpp".to_string(), "models".to_string(), "recommended_build".to_string()]);
        let mut ok = required_keys.issubset(info.keys());
        failures += if _check(ok, format!("/__system-info keys present  models={}", info.get(&"model_count".to_string()).cloned().unwrap_or(0))) { 0 } else { 1 };
        if ok {
            println!("         CPU: {}  {} cores", info.get(&"cpu_name".to_string()).cloned().unwrap_or("?".to_string()), info.get(&"cpu_count".to_string()).cloned().unwrap_or("?".to_string()));
            println!("         RAM: {} GB", info.get(&"memory_gb".to_string()).cloned().unwrap_or("?".to_string()));
            println!("         GPUs: {}", info.get(&"gpus".to_string()).cloned().unwrap_or(vec![]).len());
            println!("         llama.cpp: {}", (info.get(&"llama_cpp_version".to_string()).cloned() || "not installed".to_string()));
            println!("         Models found: {}", info.get(&"model_count".to_string()).cloned().unwrap_or(0));
        }
    }
    // except Exception as e:
    Ok(failures)
}

/// Run actual inference through up to max_models GGUF models.
pub fn _run_inference_tests(model_dirs: Vec<String>, max_models: i64) -> Result<i64> {
    // Run actual inference through up to max_models GGUF models.
    let mut failures = 0;
    println!("{}", "\n[1m── LLM INFERENCE TESTS ─────────────────────────────────────[0m".to_string());
    let mut models = cb::scan_models(model_dirs);
    if !models {
        println!("  {}  No GGUF models found in: {}", WARN, model_dirs);
        println!("{}", "         Cannot run inference tests. Add models to the scan dirs.".to_string());
        0
    }
    let mut picks = models[..max_models];
    println!("  {}  Found {} model(s), testing {}:", INFO, models.len(), picks.len());
    for m in picks.iter() {
        println!("         {}  ({:.1} GB)", m["name".to_string()], m["size_gb".to_string()]);
    }
    let mut TEST_PROMPTS = vec![("What is 2 + 2? Answer with only the number.".to_string(), "You are a concise assistant.".to_string()), ("Say exactly: HELLO".to_string(), "You are a test assistant. Follow instructions exactly.".to_string())];
    let (mut prompt_text, mut sys_text) = TEST_PROMPTS[0];
    println!("\n  Running prompt: \"{}\"", prompt_text);
    let mut model_paths = picks.iter().map(|m| m["path".to_string()]).collect::<Vec<_>>();
    let mut t_total = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    // try:
    {
        let mut resp = _post("/__comparison/mixed".to_string(), HashMap::from([("prompt".to_string(), prompt_text), ("system_prompt".to_string(), sys_text), ("local_models".to_string(), model_paths), ("online_models".to_string(), vec![]), ("max_tokens".to_string(), 64), ("temperature".to_string(), 0.0_f64), ("n_ctx".to_string(), 2048)]), /* timeout= */ 600);
    }
    // except Exception as e:
    let mut wall_s = (std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() - t_total);
    let mut responses = resp.get(&"responses".to_string()).cloned().unwrap_or(vec![]);
    _check(responses.len() == picks.len(), format!("Got {}/{} responses", responses.len(), picks.len()));
    failures += if responses.len() == picks.len() { 0 } else { 1 };
    let mut times = vec![];
    let mut tps_all = vec![];
    println!();
    for r in responses.iter() {
        let mut name = r.get(&"model".to_string()).cloned().unwrap_or("?".to_string());
        let mut text = r.get(&"response".to_string()).cloned().unwrap_or("".to_string());
        let mut err = r.get(&"error".to_string()).cloned().unwrap_or("".to_string());
        let mut t_ms = r.get(&"time_ms".to_string()).cloned().unwrap_or(0);
        let mut tps = r.get(&"tokens_per_sec".to_string()).cloned().unwrap_or(0);
        let mut tok = r.get(&"tokens".to_string()).cloned().unwrap_or(0);
        let mut has_resp = ((text != 0) && !err);
        _check(has_resp, format!("{}", name), if has_resp { format!("{:.0}ms  {}tok  {:.1}t/s  preview: \"{}\"", t_ms, tok, tps, text[..60].trim().to_string()) } else { format!("ERROR: {}", (err || "empty response".to_string())) });
        failures += if has_resp { 0 } else { 1 };
        if (has_resp && t_ms > 0) {
            times.push(t_ms);
            tps_all.push(tps);
        }
    }
    if times {
        println!("\n  Summary  ({} models, wall={:.1}s)", picks.len(), wall_s);
        println!("         Time  min={:.0}ms  avg={:.0}ms  max={:.0}ms", times.iter().min().unwrap(), (times.iter().sum::<i64>() / times.len()), times.iter().max().unwrap());
        if tps_all {
            let mut nonzero = tps_all.iter().filter(|t| t > 0).map(|t| t).collect::<Vec<_>>();
            if nonzero {
                println!("         Tok/s min={:.1}  avg={:.1}  max={:.1}", nonzero.iter().min().unwrap(), (nonzero.iter().sum::<i64>() / nonzero.len()), nonzero.iter().max().unwrap());
            }
        }
    }
    Ok(failures)
}

pub fn main() -> i64 {
    let mut ap = argparse.ArgumentParser(/* description= */ "Headless LLM integration test for comparator_backend".to_string());
    ap.add_argument("--models-dir".to_string(), /* default= */ None, /* help= */ "Extra directory to scan for GGUF models (can be repeated)".to_string(), /* action= */ "append".to_string(), /* dest= */ "extra_dirs".to_string());
    ap.add_argument("--max".to_string(), /* type= */ int, /* default= */ 6, /* help= */ "Max number of models to run inference on (default 6)".to_string());
    ap.add_argument("--live".to_string(), /* action= */ "store_true".to_string(), /* help= */ "Use an already-running backend on port 8123 instead of starting one".to_string());
    let mut args = ap.parse_args();
    let mut model_dirs = cb::ComparatorHandler.model_dirs.into_iter().collect::<Vec<_>>();
    if args.extra_dirs {
        let mut model_dirs = (args.extra_dirs + model_dirs);
    }
    println!("{}", ("=".to_string() * 60));
    println!("{}", "   ZEN LLM COMPARE -- Headless Integration Test".to_string());
    println!("{}", ("=".to_string() * 60));
    println!("Model dirs: {}", model_dirs);
    println!("Max models: {}", args.max);
    let mut total_failures = 0;
    total_failures += _run_unit_tests();
    // global/nonlocal BACKEND_PORT, BACKEND_URL
    if args.live {
        let mut BACKEND_PORT = 8123;
        let mut BACKEND_URL = format!("http://127.0.0.1:{}", BACKEND_PORT);
        println!("\nUsing live backend at {}", BACKEND_URL);
    } else {
        println!("\nStarting backend on port {}…", BACKEND_PORT);
        _start_backend_thread(model_dirs);
        if !_wait_backend() {
            println!("{}  Backend did not start within timeout", FAIL);
            std::process::exit(1);
        }
    }
    total_failures += _run_endpoint_tests(model_dirs);
    total_failures += _run_inference_tests(model_dirs, /* max_models= */ args.max);
    println!("{}", ("\n".to_string() + ("─".to_string() * 60)));
    if total_failures == 0 {
        println!("{}", "[92m[1m  ALL TESTS PASSED[0m".to_string());
    } else {
        println!("[91m[1m  {} TEST(S) FAILED[0m", total_failures);
    }
    println!("{}", ("─".to_string() * 60));
    total_failures
}

pub fn _ensure_test_server(dirs: Vec<String>) -> Result<()> {
    if _TEST_SERVER_STARTED.is_set() {
        return Ok(());
    }
    _start_backend_thread(dirs);
    if _wait_backend().is_err() {
        return Err(anyhow::anyhow!("RuntimeError('Integration test server failed to start')"));
    }
    Ok(_TEST_SERVER_STARTED.store(true, Ordering::SeqCst))
}

/// Pytest: unit-level function checks (no server required).
pub fn test_unit_functions() -> () {
    // Pytest: unit-level function checks (no server required).
    let mut failures = _run_unit_tests();
    assert!(failures == 0, "{} unit test(s) failed — see output above", failures);
}

/// Pytest: HTTP API integration checks.
pub fn test_http_endpoints(model_dirs: Vec<String>) -> () {
    // Pytest: HTTP API integration checks.
    _ensure_test_server(model_dirs);
    let mut failures = _run_endpoint_tests(model_dirs);
    assert!(failures.is_ok() && failures.unwrap() == 0, "{} HTTP endpoint test(s) failed — see output above", failures.unwrap());
}

/// Pytest: actual GGUF inference (skipped when no models are present).
pub fn test_llm_inference(model_dirs: Vec<String>) -> () {
    // Pytest: actual GGUF inference (skipped when no models are present).
    _ensure_test_server(model_dirs);
    let mut failures = _run_inference_tests(model_dirs.clone());
    assert!(failures.is_ok() && failures.unwrap() == 0, "{} inference test(s) failed — see output above", failures.unwrap());
}