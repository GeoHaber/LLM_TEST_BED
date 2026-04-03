/// Pytest fixtures for Swarm integration tests.

use anyhow::{Result, Context};
use crate::comparator_backend as cb;

pub static REPO_ROOT: std::sync::LazyLock<String /* os::path.dirname */> = std::sync::LazyLock::new(|| Default::default());

/// Default model directories used by pytest for integration tests.
pub fn model_dirs() -> Vec<String> {
    // Default model directories used by pytest for integration tests.
    vec!["C:\\AI\\Models".to_string(), "C:\\Users\\Public\\AI\\Models".to_string()]
}

/// Keep the backend's process-global limiter from leaking across tests.
pub fn reset_global_rate_limiter() -> () {
    // Keep the backend's process-global limiter from leaking across tests.
    let _ctx = cb::rate_limiter._lock;
    {
        cb::_rate_limiter._hits.clear();
    }
    /* yield */;
    let _ctx = cb::_rate_limiter.lock;
    {
        cb::_rate_limiter._hits.clear();
    }
}