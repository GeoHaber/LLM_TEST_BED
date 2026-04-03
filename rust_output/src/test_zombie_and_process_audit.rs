/// Process & Resource Hygiene Tests
/// =================================
/// Validates that the backend does not leak processes, threads, or ports.
/// These tests ensure zombie processes cannot be created by normal app/test usage.
/// 
/// Run:
/// pytest tests/test_zombie_and_process_audit::py -v --tb=short

use anyhow::{Result, Context};
use crate::comparator_backend as cb;
use regex::Regex;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::PathBuf;
use tokio;

pub static REPO_ROOT: std::sync::LazyLock<String /* os::path.dirname */> = std::sync::LazyLock::new(|| Default::default());

pub const TEST_PORT: i64 = 18130;

pub const TEST_URL: &str = "f'http://127.0.0.1:{TEST_PORT}";

pub static _SERVER: std::sync::LazyLock<Option<serde_json::Value>> = std::sync::LazyLock::new(|| None);

/// Ensure all server threads are daemon (die with main process).
#[derive(Debug, Clone)]
pub struct TestDaemonThreads {
}

impl TestDaemonThreads {
    pub fn test_threading_server_has_daemon_threads(&self) -> () {
        assert!(cb::ThreadingHTTPServer.daemon_threads == true);
    }
    pub fn test_server_class_uses_threading_mixin(&self) -> () {
        // TODO: from socketserver import ThreadingMixIn
        assert!(issubclass(cb::ThreadingHTTPServer, ThreadingMixIn));
    }
    /// After starting and stopping a test server, all spawned threads are daemon.
    pub fn test_no_non_daemon_threads_created_by_handler(&self) -> () {
        // After starting and stopping a test server, all spawned threads are daemon.
        _start_test_server();
        let mut non_daemon = threading::enumerate().iter().filter(|t| (t.name != "MainThread".to_string() && !t.daemon)).map(|t| t).collect::<Vec<_>>();
        assert!(non_daemon.len() == 0, "Non-daemon threads: {}", non_daemon.iter().map(|t| t.name).collect::<Vec<_>>());
    }
}

/// Download workers must be daemon threads so they die with the process.
#[derive(Debug, Clone)]
pub struct TestDownloadWorkerHygiene {
}

impl TestDownloadWorkerHygiene {
    /// The _handle_download code creates daemon=true threads.
    pub fn test_download_thread_is_daemon(&self) -> Result<()> {
        // The _handle_download code creates daemon=true threads.
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        let mut pattern = "threading\\.Thread\\(target=_run_download.*?daemon=true".to_string();
        Ok(assert!(regex::Regex::new(&pattern).unwrap().is_match(&src), "_run_download thread is not created with daemon=true"))
    }
    /// The _handle_install_llama code creates daemon=true threads.
    pub fn test_install_thread_is_daemon(&self) -> Result<()> {
        // The _handle_install_llama code creates daemon=true threads.
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        let mut pattern = "threading\\.Thread\\(target=_run_install.*?daemon=true".to_string();
        Ok(assert!(regex::Regex::new(&pattern).unwrap().is_match(&src), "_run_install thread is not created with daemon=true"))
    }
}

/// Server must bind to 127.0.0.1, not 0.0.0.0.
#[derive(Debug, Clone)]
pub struct TestPortBinding {
}

impl TestPortBinding {
    pub fn test_server_binds_localhost_only(&self) -> Result<()> {
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        Ok(assert!((regex::Regex::new(&"HTTPServer\\(\\(\"127\\.0\\.0\\.1\"".to_string()).unwrap().is_match(&src) || regex::Regex::new(&"run_server.*127\\.0\\.0\\.1".to_string()).unwrap().is_match(&src)), "Server must bind to 127.0.0.1"))
    }
    pub fn test_run_server_function_uses_localhost(&self) -> Result<()> {
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        Ok(assert!((regex::Regex::new(&"127\\.0\\.0\\.1.*PORT".to_string()).unwrap().is_match(&src) || regex::Regex::new(&"\"127\\.0\\.0\\.1\"".to_string()).unwrap().is_match(&src)), "run_server should explicitly use 127.0.0.1"))
    }
}

/// Backend must silently handle client disconnects.
#[derive(Debug, Clone)]
pub struct TestConnectionErrorHandling {
}

impl TestConnectionErrorHandling {
    pub fn test_send_json_catches_connection_errors(&self) -> Result<()> {
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        let mut idx = src.find(&*"def _send_json".to_string()).map(|i| i as i64).unwrap_or(-1);
        let mut block = src[idx..(idx + 600)];
        assert!(block.contains(&"ConnectionAbortedError".to_string()), "_send_json should catch ConnectionAbortedError");
        Ok(assert!(block.contains(&"BrokenPipeError".to_string()), "_send_json should catch BrokenPipeError"))
    }
    pub fn test_sse_handler_catches_connection_errors(&self) -> Result<()> {
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        let mut idx = src.find(&*"def _sse(event".to_string()).map(|i| i as i64).unwrap_or(-1);
        let mut block = src[idx..(idx + 400)];
        Ok(assert!((block.contains(&"ConnectionAbortedError".to_string()) || block.contains(&"BrokenPipeError".to_string())), "SSE _sse() should catch connection errors"))
    }
    pub fn test_log_message_silences_disconnect_noise(&self) -> Result<()> {
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        let mut idx = src.find(&*"def log_message".to_string()).map(|i| i as i64).unwrap_or(-1);
        let mut block = src[idx..(idx + 300)];
        Ok(assert!(block.contains(&"ConnectionAbortedError".to_string()), "log_message should filter out ConnectionAbortedError"))
    }
    pub fn test_options_handler_catches_connection_errors(&self) -> Result<()> {
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        let mut idx = src.find(&*"def do_OPTIONS".to_string()).map(|i| i as i64).unwrap_or(-1);
        let mut block = src[idx..(idx + 400)];
        Ok(assert!((block.contains(&"ConnectionAbortedError".to_string()) || block.contains(&"BrokenPipeError".to_string())), "do_OPTIONS should catch connection errors"))
    }
}

/// Server should release port on shutdown.
#[derive(Debug, Clone)]
pub struct TestServerLifecycle {
}

impl TestServerLifecycle {
    /// The test port (18131) should be free for a new server.
    pub fn test_port_is_free_before_bind(&self) -> Result<()> {
        // The test port (18131) should be free for a new server.
        let mut sock = socket::socket(socket::AF_INET, socket::SOCK_STREAM);
        // try:
        {
            let mut result = sock.connect_ex(("127.0.0.1".to_string(), 18131));
            assert!(result != 0, "Port 18131 should be free");
        }
        // finally:
            Ok(sock.close())
    }
    /// The _warm_cache background thread must be daemon.
    pub fn test_cache_thread_is_daemon(&self) -> Result<()> {
        // The _warm_cache background thread must be daemon.
        let mut src = File::open(PathBuf::from(REPO_ROOT).join("comparator_backend::py".to_string()))?.read();
        Ok(assert!(regex::Regex::new(&"Thread\\(target=_warm_cache.*daemon=true".to_string()).unwrap().is_match(&src), "_warm_cache thread should be daemon"))
    }
}

/// Only sources with actual GGUF catalog APIs should be in the model browser.
#[derive(Debug, Clone)]
pub struct TestModelBrowserSources {
}

impl TestModelBrowserSources {
    pub fn test_no_github_tab_in_download_modal(&self) -> Result<()> {
        let mut html = File::open(PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string()))?.read();
        Ok(assert!(!html.contains(&"switchRepo('gh'".to_string()), "GitHub tab should be removed (no GGUF catalog API)"))
    }
    pub fn test_no_github_section_in_download_modal(&self) -> Result<()> {
        let mut html = File::open(PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string()))?.read();
        Ok(assert!(!html.contains(&"id=\"repo-gh\"".to_string()), "GitHub section (repo-gh) should be removed"))
    }
    pub fn test_huggingface_tab_exists(&self) -> Result<()> {
        let mut html = File::open(PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string()))?.read();
        Ok(assert!(html.contains(&"switchRepo('hf'".to_string())))
    }
    pub fn test_discover_tab_exists(&self) -> Result<()> {
        let mut html = File::open(PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string()))?.read();
        Ok(assert!(html.contains(&"switchRepo('discover'".to_string())))
    }
    pub fn test_modelscope_tab_exists(&self) -> Result<()> {
        let mut html = File::open(PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string()))?.read();
        Ok(assert!(html.contains(&"switchRepo('ms'".to_string())))
    }
}

/// UI should show spinning indicators during async operations.
#[derive(Debug, Clone)]
pub struct TestLoadingSpinners {
}

impl TestLoadingSpinners {
    pub fn test_discover_grid_has_spinner(&self) -> Result<()> {
        let mut html = File::open(PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string()))?.read();
        let mut idx = html.find(&*"discoverGrid".to_string()).map(|i| i as i64).unwrap_or(-1);
        let mut block = html[idx..(idx + 500)];
        Ok(assert!(block.contains(&"animate-spin".to_string()), "Discover grid should have a loading spinner"))
    }
    pub fn test_catalog_grid_has_spinner(&self) -> Result<()> {
        let mut html = File::open(PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string()))?.read();
        let mut idx = html.find(&*"catalogGrid".to_string()).map(|i| i as i64).unwrap_or(-1);
        let mut block = html[idx..(idx + 500)];
        Ok(assert!(block.contains(&"animate-spin".to_string()), "Catalog grid should have a loading spinner"))
    }
    pub fn test_download_button_shows_spinner(&self) -> Result<()> {
        let mut html = File::open(PathBuf::from(REPO_ROOT).join("model_comparator.html".to_string()))?.read();
        Ok(assert!(html.contains(&"animate-spin".to_string()) && html.contains(&"Starting".to_string())), "Download button should show spinner while starting")
    }
}

pub fn _start_test_server() -> Result<()> {
    // global/nonlocal _server
    if let Some(_) = _server {
        return;
    }
    // TODO: from http::server import HTTPServer
    let mut _server = http::server::HTTPServer(("127.0.0.1".to_string(), TEST_PORT), cb::ComparatorHandler::new());
    let mut t = std::thread::spawn(|| {});
    t.join();
    for _ in 0..50 {
        // try:
        {
            let mut req = urllib::request.Request(format!("{}/__health", TEST_URL));
            let _ctx = urllib::request.urlopen(req, /* timeout= */ 2);
            {
                break;
            }
        }
        // except Exception as _e:
    }
}