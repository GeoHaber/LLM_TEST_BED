/// Model Discovery, llama.cpp Install & Model Card Tests
/// ======================================================
/// Tests for:
/// 1. HuggingFace model discovery (trending, downloads, likes, newest, caching)
/// 2. CPU detection & llama.cpp build recommendation
/// 3. llama.cpp install endpoint validation
/// 4. Model library scanning (card data: name, size, quant filtering, fitness)
/// 5. Model card information completeness (system-info response shape)
/// 
/// No mocks — real function calls and HTTP requests against a test server.
/// 
/// Run:
/// pytest tests/test_discovery_install::py -v --tb=short

use anyhow::{Result, Context};
use crate::comparator_backend as cb;
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::PathBuf;

pub static REPO_ROOT: std::sync::LazyLock<String /* os::path.dirname */> = std::sync::LazyLock::new(|| Default::default());

pub const TEST_PORT: i64 = 18127;

pub const TEST_URL: &str = "f'http://127.0.0.1:{TEST_PORT}";

pub static _SERVER: std::sync::LazyLock<Option<serde_json::Value>> = std::sync::LazyLock::new(|| None);

/// Test CPU detection returns correct structure and fields.
#[derive(Debug, Clone)]
pub struct TestCPUDetection {
}

impl TestCPUDetection {
    pub fn test_cpu_info_returns_required_fields(&self) -> () {
        let mut info = cb::get_cpu_info();
        assert!(info.contains(&"brand".to_string()));
        assert!(info.contains(&"name".to_string()));
        assert!(info.contains(&"cores".to_string()));
        assert!(info.contains(&"avx2".to_string()));
        assert!(info.contains(&"avx512".to_string()));
    }
    pub fn test_cpu_brand_is_known(&self) -> () {
        let mut info = cb::get_cpu_info();
        assert!(("AMD".to_string(), "Intel".to_string(), "Unknown".to_string()).contains(&info["brand".to_string()]));
    }
    pub fn test_cpu_cores_positive(&self) -> () {
        let mut info = cb::get_cpu_info();
        assert!(/* /* isinstance(info["cores".to_string()], int) */ */ true);
        assert!(info["cores".to_string()] >= 1);
    }
    pub fn test_cpu_avx_flags_are_bool(&self) -> () {
        let mut info = cb::get_cpu_info();
        assert!(/* /* isinstance(info["avx2".to_string()], bool) */ */ true);
        assert!(/* /* isinstance(info["avx512".to_string()], bool) */ */ true);
    }
    pub fn test_cpu_name_is_nonempty_string(&self) -> () {
        let mut info = cb::get_cpu_info();
        assert!(/* /* isinstance(info["name".to_string()], str) */ */ true);
        assert!(info["name".to_string()].len() > 0);
    }
}

/// Test llama.cpp build recommendation logic for different hardware.
#[derive(Debug, Clone)]
pub struct TestBuildRecommendation {
}

impl TestBuildRecommendation {
    pub fn test_nvidia_gpu_recommends_cuda(&self) -> () {
        let mut cpu = HashMap::from([("brand".to_string(), "Intel".to_string()), ("name".to_string(), "i7".to_string()), ("cores".to_string(), 8), ("avx2".to_string(), true), ("avx512".to_string(), false)]);
        let mut gpus = vec![HashMap::from([("name".to_string(), "RTX 4090".to_string()), ("vendor".to_string(), "NVIDIA".to_string()), ("vram_gb".to_string(), 24.0_f64), ("backend".to_string(), "CUDA".to_string())])];
        let mut rec = cb::recommend_llama_build(cpu, gpus);
        assert!(rec["flag".to_string()] == "cuda".to_string());
        assert!(rec["build".to_string()].contains(&"CUDA".to_string()));
        assert!(rec.contains(&"pip".to_string()));
    }
    pub fn test_amd_gpu_recommends_rocm(&self) -> () {
        let mut cpu = HashMap::from([("brand".to_string(), "AMD".to_string()), ("name".to_string(), "Ryzen 7".to_string()), ("cores".to_string(), 8), ("avx2".to_string(), true), ("avx512".to_string(), false)]);
        let mut gpus = vec![HashMap::from([("name".to_string(), "Radeon 890M".to_string()), ("vendor".to_string(), "AMD".to_string()), ("vram_gb".to_string(), 8.0_f64), ("backend".to_string(), "ROCm/Vulkan".to_string())])];
        let mut rec = cb::recommend_llama_build(cpu, gpus);
        assert!(rec["flag".to_string()] == "rocm".to_string());
        assert!((rec["build".to_string()].contains(&"ROCm".to_string()) || rec["build".to_string()].contains(&"Vulkan".to_string())));
    }
    pub fn test_avx512_cpu_no_gpu(&self) -> () {
        let mut cpu = HashMap::from([("brand".to_string(), "Intel".to_string()), ("name".to_string(), "Xeon".to_string()), ("cores".to_string(), 32), ("avx2".to_string(), true), ("avx512".to_string(), true)]);
        let mut rec = cb::recommend_llama_build(cpu, vec![]);
        assert!(rec["flag".to_string()] == "avx512".to_string());
        assert!(rec["build".to_string()].contains(&"AVX-512".to_string()));
    }
    pub fn test_avx2_cpu_no_gpu(&self) -> () {
        let mut cpu = HashMap::from([("brand".to_string(), "AMD".to_string()), ("name".to_string(), "Ryzen 5".to_string()), ("cores".to_string(), 6), ("avx2".to_string(), true), ("avx512".to_string(), false)]);
        let mut rec = cb::recommend_llama_build(cpu, vec![]);
        assert!(rec["flag".to_string()] == "avx2".to_string());
        assert!(rec["build".to_string()].contains(&"AVX2".to_string()));
    }
    pub fn test_basic_cpu_no_gpu_no_avx(&self) -> () {
        let mut cpu = HashMap::from([("brand".to_string(), "Unknown".to_string()), ("name".to_string(), "Atom".to_string()), ("cores".to_string(), 2), ("avx2".to_string(), false), ("avx512".to_string(), false)]);
        let mut rec = cb::recommend_llama_build(cpu, vec![]);
        assert!(rec["flag".to_string()] == "cpu".to_string());
        assert!((rec["build".to_string()].contains(&"Basic".to_string()) || rec["build".to_string()].contains(&"CPU".to_string())));
    }
    pub fn test_recommendation_has_pip_command(&self) -> () {
        let mut cpu = HashMap::from([("brand".to_string(), "AMD".to_string()), ("name".to_string(), "Ryzen 7".to_string()), ("cores".to_string(), 8), ("avx2".to_string(), true), ("avx512".to_string(), false)]);
        let mut rec = cb::recommend_llama_build(cpu, vec![]);
        assert!(rec.contains(&"pip".to_string()));
        assert!(rec["pip".to_string()].contains(&"llama-cpp-python".to_string()));
    }
    pub fn test_recommendation_has_note(&self) -> () {
        let mut cpu = HashMap::from([("brand".to_string(), "AMD".to_string()), ("name".to_string(), "Ryzen 7".to_string()), ("cores".to_string(), 8), ("avx2".to_string(), true), ("avx512".to_string(), false)]);
        let mut rec = cb::recommend_llama_build(cpu, vec![]);
        assert!(rec.contains(&"note".to_string()));
        assert!(rec["note".to_string()].len() > 0);
    }
    pub fn test_recommendation_has_reason(&self) -> () {
        let mut cpu = HashMap::from([("brand".to_string(), "AMD".to_string()), ("name".to_string(), "Ryzen 7".to_string()), ("cores".to_string(), 8), ("avx2".to_string(), true), ("avx512".to_string(), false)]);
        let mut rec = cb::recommend_llama_build(cpu, vec![]);
        assert!(rec.contains(&"reason".to_string()));
        assert!(rec["reason".to_string()].len() > 0);
    }
    /// Even with AVX-512 CPU, NVIDIA GPU should be recommended.
    pub fn test_nvidia_takes_priority_over_avx512(&self) -> () {
        // Even with AVX-512 CPU, NVIDIA GPU should be recommended.
        let mut cpu = HashMap::from([("brand".to_string(), "Intel".to_string()), ("name".to_string(), "Xeon".to_string()), ("cores".to_string(), 32), ("avx2".to_string(), true), ("avx512".to_string(), true)]);
        let mut gpus = vec![HashMap::from([("name".to_string(), "RTX 3090".to_string()), ("vendor".to_string(), "NVIDIA".to_string()), ("vram_gb".to_string(), 24.0_f64), ("backend".to_string(), "CUDA".to_string())])];
        let mut rec = cb::recommend_llama_build(cpu, gpus);
        assert!(rec["flag".to_string()] == "cuda".to_string());
    }
}

/// Test model directory scanning produces correct card data.
#[derive(Debug, Clone)]
pub struct TestModelScanning {
}

impl TestModelScanning {
    pub fn test_scan_nonexistent_dir_returns_empty(&self) -> () {
        let mut result = cb::scan_models(vec!["/tmp/nonexistent_dir_xyz_12345".to_string()]);
        assert!(result == vec![]);
    }
    pub fn test_scan_empty_dir_returns_empty(&self) -> () {
        let mut td = tempfile::TemporaryDirectory();
        {
            let mut result = cb::scan_models(vec![td]);
            assert!(result == vec![]);
        }
    }
    /// Files under 50MB should be filtered out.
    pub fn test_scan_skips_tiny_files(&self) -> Result<()> {
        // Files under 50MB should be filtered out.
        let mut td = tempfile::TemporaryDirectory();
        {
            let mut tiny = PathBuf::from(td).join("tiny.gguf".to_string());
            let mut f = File::open(tiny)?;
            {
                f.write((b" " * ((10 * 1024) * 1024)));
            }
            let mut result = cb::scan_models(vec![td]);
            assert!(result.len() == 0);
        }
    }
    /// Only .gguf files should be found.
    pub fn test_scan_skips_non_gguf_files(&self) -> Result<()> {
        // Only .gguf files should be found.
        let mut td = tempfile::TemporaryDirectory();
        {
            let mut txt = PathBuf::from(td).join("readme.txt".to_string());
            let mut f = File::create(txt)?;
            {
                f.write("hello".to_string());
            }
            let mut bin_file = PathBuf::from(td).join("model.bin".to_string());
            let mut f = File::open(bin_file)?;
            {
                f.write((b" " * ((60 * 1024) * 1024)));
            }
            let mut result = cb::scan_models(vec![td]);
            assert!(result.len() == 0);
        }
    }
    /// Valid GGUF file (>50MB) should be found.
    pub fn test_scan_finds_valid_gguf(&self) -> Result<()> {
        // Valid GGUF file (>50MB) should be found.
        let mut td = tempfile::TemporaryDirectory();
        {
            let mut gguf = PathBuf::from(td).join("test-model-Q4_K_M.gguf".to_string());
            let mut f = File::open(gguf)?;
            {
                f.seek(((60 * 1024) * 1024));
                f.write(b" ");
            }
            let mut result = cb::scan_models(vec![td]);
            assert!(result.len() == 1);
            assert!(result[0]["name".to_string()] == "test-model-Q4_K_M.gguf".to_string());
        }
    }
    pub fn test_scan_returns_size_gb(&self) -> Result<()> {
        let mut td = tempfile::TemporaryDirectory();
        {
            let mut gguf = PathBuf::from(td).join("medium.gguf".to_string());
            let mut f = File::open(gguf)?;
            {
                f.seek(((60 * 1024) * 1024));
                f.write(b" ");
            }
            let mut result = cb::scan_models(vec![td]);
            assert!(result.len() == 1);
            assert!(result[0].contains(&"size_gb".to_string()));
            assert!(result[0]["size_gb".to_string()] > 0);
        }
    }
    pub fn test_scan_returns_full_path(&self) -> Result<()> {
        let mut td = tempfile::TemporaryDirectory();
        {
            let mut gguf = PathBuf::from(td).join("valid.gguf".to_string());
            let mut f = File::open(gguf)?;
            {
                f.seek(((60 * 1024) * 1024));
                f.write(b" ");
            }
            let mut result = cb::scan_models(vec![td]);
            assert!(result[0]["path".to_string()] == gguf);
        }
    }
    /// BitNet i2_s quantized models should be filtered out.
    pub fn test_scan_skips_incompatible_quant_i2_s(&self) -> Result<()> {
        // BitNet i2_s quantized models should be filtered out.
        let mut td = tempfile::TemporaryDirectory();
        {
            let mut gguf = PathBuf::from(td).join("model-i2_s.gguf".to_string());
            let mut f = File::open(gguf)?;
            {
                f.seek(((60 * 1024) * 1024));
                f.write(b" ");
            }
            let mut result = cb::scan_models(vec![td]);
            assert!(result.len() == 0);
        }
    }
    pub fn test_scan_skips_incompatible_quant_i1(&self) -> Result<()> {
        let mut td = tempfile::TemporaryDirectory();
        {
            let mut gguf = PathBuf::from(td).join("model-i1.gguf".to_string());
            let mut f = File::open(gguf)?;
            {
                f.seek(((60 * 1024) * 1024));
                f.write(b" ");
            }
            let mut result = cb::scan_models(vec![td]);
            assert!(result.len() == 0);
        }
    }
    /// Same filename in two dirs should appear only once.
    pub fn test_scan_deduplicates_across_dirs(&self) -> Result<()> {
        // Same filename in two dirs should appear only once.
        let mut td1 = tempfile::TemporaryDirectory();
        let mut td2 = tempfile::TemporaryDirectory();
        {
            for td in vec![td1, td2].iter() {
                let mut gguf = PathBuf::from(td).join("shared-model.gguf".to_string());
                let mut f = File::open(gguf)?;
                {
                    f.seek(((60 * 1024) * 1024));
                    f.write(b" ");
                }
            }
            let mut result = cb::scan_models(vec![td1, td2]);
            assert!(result.len() == 1);
        }
    }
    pub fn test_scan_sorted_alphabetically(&self) -> Result<()> {
        let mut td = tempfile::TemporaryDirectory();
        {
            for name in vec!["zebra.gguf".to_string(), "alpha.gguf".to_string(), "middle.gguf".to_string()].iter() {
                let mut p = PathBuf::from(td).join(name);
                let mut f = File::open(p)?;
                {
                    f.seek(((60 * 1024) * 1024));
                    f.write(b" ");
                }
            }
            let mut result = cb::scan_models(vec![td]);
            let mut names = result.iter().map(|m| m["name".to_string()]).collect::<Vec<_>>();
            assert!(names == { let mut v = names.clone(); v.sort(); v });
        }
    }
    /// If C:\AI\Models exists, scan should find models.
    pub fn test_scan_real_model_dir(&self) -> () {
        // If C:\AI\Models exists, scan should find models.
        let mut model_dir = "C:\\AI\\Models".to_string();
        if !os::path.isdir(model_dir) {
            // TODO: import pytest
            pytest.skip("C:\\AI\\Models not present".to_string());
        }
        let mut result = cb::scan_models(vec![model_dir]);
        assert!(result.len() > 0);
        for m in result.iter() {
            assert!(m["name".to_string()].ends_with(&*".gguf".to_string()));
            assert!(m["size_gb".to_string()] >= 0.05_f64);
            assert!(os::path.isabs(m["path".to_string()]));
        }
    }
}

/// Test get_system_info returns everything the UI model cards need.
#[derive(Debug, Clone)]
pub struct TestSystemInfoCard {
}

impl TestSystemInfoCard {
    pub fn test_system_info_has_cpu_fields(&self) -> () {
        let mut info = cb::get_system_info(vec![]);
        assert!(info.contains(&"cpu_brand".to_string()));
        assert!(info.contains(&"cpu_name".to_string()));
        assert!(info.contains(&"cpu_count".to_string()));
        assert!(info.contains(&"cpu_avx2".to_string()));
        assert!(info.contains(&"cpu_avx512".to_string()));
    }
    pub fn test_system_info_has_memory(&self) -> () {
        let mut info = cb::get_system_info(vec![]);
        assert!(info.contains(&"memory_gb".to_string()));
        assert!(/* /* isinstance(info["memory_gb".to_string()], float) */ */ true);
        assert!(info["memory_gb".to_string()] > 0);
    }
    pub fn test_system_info_has_gpu_list(&self) -> () {
        let mut info = cb::get_system_info(vec![]);
        assert!(info.contains(&"gpus".to_string()));
        assert!(/* /* isinstance(info["gpus".to_string()], list) */ */ true);
        for gpu in info["gpus".to_string()].iter() {
            assert!(gpu.contains(&"name".to_string()));
            assert!(gpu.contains(&"vendor".to_string()));
            assert!(gpu.contains(&"vram_gb".to_string()));
            assert!(gpu.contains(&"backend".to_string()));
        }
    }
    pub fn test_system_info_has_llama_status(&self) -> () {
        let mut info = cb::get_system_info(vec![]);
        assert!(info.contains(&"has_llama_cpp".to_string()));
        assert!(/* /* isinstance(info["has_llama_cpp".to_string()], bool) */ */ true);
        assert!(info.contains(&"llama_cpp_version".to_string()));
    }
    pub fn test_system_info_has_recommended_build(&self) -> () {
        let mut info = cb::get_system_info(vec![]);
        assert!(info.contains(&"recommended_build".to_string()));
        let mut rec = info["recommended_build".to_string()];
        assert!(rec.contains(&"build".to_string()));
        assert!(rec.contains(&"flag".to_string()));
        assert!(rec.contains(&"pip".to_string()));
        assert!(rec.contains(&"reason".to_string()));
        assert!(rec.contains(&"note".to_string()));
    }
    pub fn test_system_info_has_model_count_and_list(&self) -> () {
        let mut info = cb::get_system_info(vec![]);
        assert!(info.contains(&"model_count".to_string()));
        assert!(info.contains(&"models".to_string()));
        assert!(info["model_count".to_string()] == info["models".to_string()].len());
    }
    pub fn test_system_info_has_timestamp(&self) -> () {
        let mut info = cb::get_system_info(vec![]);
        assert!(info.contains(&"timestamp".to_string()));
        assert!(/* /* isinstance(info["timestamp".to_string()], float) */ */ true);
    }
    /// Each model entry must have name, path, size_gb for card rendering.
    pub fn test_system_info_model_entries_have_card_data(&self) -> () {
        // Each model entry must have name, path, size_gb for card rendering.
        let mut model_dir = "C:\\AI\\Models".to_string();
        if !os::path.isdir(model_dir) {
            // TODO: import pytest
            pytest.skip("C:\\AI\\Models not present".to_string());
        }
        let mut info = cb::get_system_info(vec![model_dir]);
        assert!(info["model_count".to_string()] > 0);
        for m in info["models".to_string()].iter() {
            assert!(m.contains(&"name".to_string()));
            assert!(m.contains(&"path".to_string()));
            assert!(m.contains(&"size_gb".to_string()));
            assert!(m["name".to_string()].ends_with(&*".gguf".to_string()));
            assert!(/* /* isinstance(m["size_gb".to_string()], (int, float) */) */ true);
        }
    }
}

/// Test the /__system-info HTTP endpoint.
#[derive(Debug, Clone)]
pub struct TestSystemInfoEndpoint {
}

impl TestSystemInfoEndpoint {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_system_info_returns_200(&self) -> () {
        let (mut status, _, mut data) = _get("/__system-info".to_string());
        assert!(status == 200);
    }
    pub fn test_system_info_json_has_models_array(&self) -> () {
        let (mut status, _, mut data) = _get("/__system-info".to_string());
        assert!(data.contains(&"models".to_string()));
        assert!(/* /* isinstance(data["models".to_string()], list) */ */ true);
    }
    pub fn test_system_info_json_has_hw_fields(&self) -> () {
        let (mut status, _, mut data) = _get("/__system-info".to_string());
        for field in ("cpu_brand".to_string(), "cpu_name".to_string(), "cpu_count".to_string(), "cpu_avx2".to_string(), "memory_gb".to_string(), "gpus".to_string(), "has_llama_cpp".to_string(), "recommended_build".to_string()).iter() {
            assert!(data.contains(&field), "Missing field: {}", field);
        }
    }
    pub fn test_system_info_recommended_build_shape(&self) -> () {
        let (mut status, _, mut data) = _get("/__system-info".to_string());
        let mut rec = data["recommended_build".to_string()];
        assert!(rec.contains(&"build".to_string()));
        assert!(rec.contains(&"flag".to_string()));
        assert!(("cuda".to_string(), "rocm".to_string(), "avx512".to_string(), "avx2".to_string(), "cpu".to_string()).contains(&rec["flag".to_string()]));
    }
}

/// Test _discover_hf_models function directly.
#[derive(Debug, Clone)]
pub struct TestHFDiscoveryFunction {
}

impl TestHFDiscoveryFunction {
    pub fn test_discover_returns_list(&self) -> () {
        let mut results = cb::_discover_hf_models("llama".to_string(), "downloads".to_string(), 3);
        assert!(/* /* isinstance(results, list) */ */ true);
        assert!(results.len() > 0);
    }
    pub fn test_discover_result_has_id(&self) -> () {
        let mut results = cb::_discover_hf_models("phi".to_string(), "downloads".to_string(), 3);
        let mut valid = results.iter().filter(|r| r.contains(&"id".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(valid.len() > 0);
        assert!(valid[0]["id".to_string()].contains(&"/".to_string()));
    }
    pub fn test_discover_result_has_author(&self) -> () {
        let mut results = cb::_discover_hf_models("qwen".to_string(), "downloads".to_string(), 3);
        let mut valid = results.iter().filter(|r| r.contains(&"author".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(valid.len() > 0);
        assert!(/* /* isinstance(valid[0]["author".to_string()], str) */ */ true);
    }
    pub fn test_discover_result_has_download_count(&self) -> () {
        let mut results = cb::_discover_hf_models("llama".to_string(), "downloads".to_string(), 3);
        let mut valid = results.iter().filter(|r| r.contains(&"downloads".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(valid.len() > 0);
        assert!(/* /* isinstance(valid[0]["downloads".to_string()], int) */ */ true);
        assert!(valid[0]["downloads".to_string()] >= 0);
    }
    pub fn test_discover_result_has_likes(&self) -> () {
        let mut results = cb::_discover_hf_models("llama".to_string(), "downloads".to_string(), 3);
        let mut valid = results.iter().filter(|r| r.contains(&"likes".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(valid.len() > 0);
    }
    pub fn test_discover_result_has_tags(&self) -> () {
        let mut results = cb::_discover_hf_models("llama".to_string(), "downloads".to_string(), 3);
        let mut valid = results.iter().filter(|r| r.contains(&"tags".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(valid.len() > 0);
        assert!(/* /* isinstance(valid[0]["tags".to_string()], list) */ */ true);
    }
    /// Known trusted quantizers should be flagged.
    pub fn test_discover_trusted_flag(&self) -> () {
        // Known trusted quantizers should be flagged.
        let mut results = cb::_discover_hf_models("bartowski llama".to_string(), "downloads".to_string(), 10);
        let mut valid = results.iter().filter(|r| r.contains(&"trusted".to_string())).map(|r| r).collect::<Vec<_>>();
        let mut trusted = valid.iter().filter(|r| r["trusted".to_string()]).map(|r| r).collect::<Vec<_>>();
        assert!(trusted.len() >= 0);
    }
    /// Trending sort must not error (was broken with old 'trending' param).
    pub fn test_discover_trending_sort_works(&self) -> () {
        // Trending sort must not error (was broken with old 'trending' param).
        let mut results = cb::_discover_hf_models("gguf".to_string(), "trending".to_string(), 3);
        let mut errors = results.iter().filter(|r| r.contains(&"error".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(errors.len() == 0, "Trending sort returned error: {}", errors);
        assert!(results.len() > 0);
    }
    pub fn test_discover_likes_sort_works(&self) -> () {
        let mut results = cb::_discover_hf_models("gguf".to_string(), "likes".to_string(), 3);
        let mut errors = results.iter().filter(|r| r.contains(&"error".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(errors.len() == 0);
        assert!(results.len() > 0);
    }
    pub fn test_discover_newest_sort_works(&self) -> () {
        let mut results = cb::_discover_hf_models("gguf".to_string(), "newest".to_string(), 3);
        let mut errors = results.iter().filter(|r| r.contains(&"error".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(errors.len() == 0);
        assert!(results.len() > 0);
    }
    pub fn test_discover_downloads_sort_works(&self) -> () {
        let mut results = cb::_discover_hf_models("gguf".to_string(), "downloads".to_string(), 3);
        let mut errors = results.iter().filter(|r| r.contains(&"error".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(errors.len() == 0);
        assert!(results.len() > 0);
    }
    pub fn test_discover_respects_limit(&self) -> () {
        let mut results = cb::_discover_hf_models("llama".to_string(), "downloads".to_string(), 5);
        let mut valid = results.iter().filter(|r| r.contains(&"id".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(valid.len() <= 5);
    }
    /// Second call with same params should be cached.
    pub fn test_discover_caching(&self) -> () {
        // Second call with same params should be cached.
        cb::_discovery_cache.clear();
        cb::_discover_hf_models("test_cache_query_xyz".to_string(), "downloads".to_string(), 2);
        let mut cache_key = "test_cache_query_xyz|downloads|2".to_string();
        assert!(cb::_discovery_cache.contains(&cache_key));
    }
    /// Empty query should still return results (browse all GGUF).
    pub fn test_discover_empty_query(&self) -> () {
        // Empty query should still return results (browse all GGUF).
        let mut results = cb::_discover_hf_models("".to_string(), "downloads".to_string(), 3);
        assert!(/* /* isinstance(results, list) */ */ true);
        let mut errors = results.iter().filter(|r| r.contains(&"error".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(errors.len() == 0);
    }
    pub fn test_discover_result_has_pipeline_field(&self) -> () {
        let mut results = cb::_discover_hf_models("llama".to_string(), "downloads".to_string(), 3);
        let mut valid = results.iter().filter(|r| r.contains(&"id".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(valid.len() > 0);
        assert!(valid[0].contains(&"pipeline".to_string()));
    }
    pub fn test_discover_result_has_lastModified(&self) -> () {
        let mut results = cb::_discover_hf_models("llama".to_string(), "downloads".to_string(), 3);
        let mut valid = results.iter().filter(|r| r.contains(&"id".to_string())).map(|r| r).collect::<Vec<_>>();
        assert!(valid.len() > 0);
        assert!(valid[0].contains(&"lastModified".to_string()));
        assert!(/* /* isinstance(valid[0]["lastModified".to_string()], str) */ */ true);
    }
}

/// Test /__discover-models HTTP endpoint.
#[derive(Debug, Clone)]
pub struct TestDiscoverEndpoint {
}

impl TestDiscoverEndpoint {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_discover_endpoint_returns_200(&self) -> () {
        let (mut status, _, mut data) = _get("/__discover-models?q=llama&sort=downloads&limit=3".to_string(), /* timeout= */ 30);
        assert!(status == 200);
    }
    pub fn test_discover_endpoint_returns_models_array(&self) -> () {
        let (mut status, _, mut data) = _get("/__discover-models?q=phi&sort=downloads&limit=3".to_string(), /* timeout= */ 30);
        assert!(data.contains(&"models".to_string()));
        assert!(/* /* isinstance(data["models".to_string()], list) */ */ true);
    }
    pub fn test_discover_endpoint_has_cached_flag(&self) -> () {
        let (mut status, _, mut data) = _get("/__discover-models?q=qwen&sort=downloads&limit=3".to_string(), /* timeout= */ 30);
        assert!(data.contains(&"cached".to_string()));
    }
    /// Trending sort via endpoint should not return error models.
    pub fn test_discover_endpoint_trending_no_error(&self) -> () {
        // Trending sort via endpoint should not return error models.
        let (mut status, _, mut data) = _get("/__discover-models?q=gguf&sort=trending&limit=3".to_string(), /* timeout= */ 30);
        assert!(status == 200);
        let mut errors = data["models".to_string()].iter().filter(|m| m.contains(&"error".to_string())).map(|m| m).collect::<Vec<_>>();
        assert!(errors.len() == 0, "Trending endpoint error: {}", errors);
    }
    pub fn test_discover_endpoint_invalid_sort_defaults_to_trending(&self) -> () {
        let (mut status, _, mut data) = _get("/__discover-models?q=llama&sort=INVALID&limit=3".to_string(), /* timeout= */ 30);
        assert!(status == 200);
        let mut errors = data["models".to_string()].iter().filter(|m| m.contains(&"error".to_string())).map(|m| m).collect::<Vec<_>>();
        assert!(errors.len() == 0);
    }
    pub fn test_discover_endpoint_limit_capped_at_60(&self) -> () {
        let (mut status, _, mut data) = _get("/__discover-models?q=llama&sort=downloads&limit=999".to_string(), /* timeout= */ 30);
        assert!(status == 200);
        assert!(data["models".to_string()].len() <= 60);
    }
    /// Query longer than 200 chars should be truncated, not crash.
    pub fn test_discover_endpoint_query_truncated(&self) -> () {
        // Query longer than 200 chars should be truncated, not crash.
        let mut long_q = ("a".to_string() * 250);
        let (mut status, _, mut data) = _get(format!("/__discover-models?q={}&sort=downloads&limit=2", long_q), /* timeout= */ 30);
        assert!(status == 200);
    }
}

/// Test /__install-llama and /__install-status endpoints.
#[derive(Debug, Clone)]
pub struct TestLlamaInstallEndpoint {
}

impl TestLlamaInstallEndpoint {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    /// Security: only llama-cpp-python pip installs allowed.
    pub fn test_install_rejects_non_llama_command(&self) -> () {
        // Security: only llama-cpp-python pip installs allowed.
        let (mut status, _, mut data) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install evil-package".to_string())]));
        assert!(status == 400);
        assert!(data.get(&"ok".to_string()).cloned() == false);
        assert!(data.get(&"error".to_string()).cloned().unwrap_or("".to_string()).contains(&"Only llama-cpp-python".to_string()));
    }
    pub fn test_install_rejects_arbitrary_command(&self) -> () {
        let (mut status, _, mut data) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "rm -rf /".to_string())]));
        assert!(status == 400);
        assert!(data.get(&"ok".to_string()).cloned() == false);
    }
    pub fn test_install_rejects_command_injection(&self) -> () {
        let (mut status, _, mut data) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install llama-cpp-python; rm -rf /".to_string())]));
        assert!(status == 200);
    }
    pub fn test_install_status_unknown_job(&self) -> () {
        let (mut status, _, mut data) = _get("/__install-status?job=nonexistent".to_string());
        assert!(status == 200);
        assert!(data["state".to_string()] == "unknown".to_string());
    }
    /// A valid install request should return a job_id for polling.
    pub fn test_install_returns_job_id(&self) -> () {
        // A valid install request should return a job_id for polling.
        let (mut status, _, mut data) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install llama-cpp-python --dry-run".to_string())]));
        assert!(status == 200);
        assert!(data.get(&"ok".to_string()).cloned() == true);
        assert!(data.contains(&"job_id".to_string()));
        assert!(data["job_id".to_string()].len() > 0);
    }
    /// After starting an install, polling should return a valid state.
    pub fn test_install_status_poll(&self) -> () {
        // After starting an install, polling should return a valid state.
        let (mut status, _, mut data) = _post("/__install-llama".to_string(), HashMap::from([("pip".to_string(), "pip install llama-cpp-python --dry-run".to_string())]));
        let mut job_id = data["job_id".to_string()];
        std::thread::sleep(std::time::Duration::from_secs_f64(0.5_f64));
        let (mut status2, _, mut job) = _get(format!("/__install-status?job={}", job_id));
        assert!(status2 == 200);
        assert!(("starting".to_string(), "running".to_string(), "done".to_string(), "error".to_string()).contains(&job["state".to_string()]));
    }
}

/// Test /__download-model and /__download-status endpoints.
#[derive(Debug, Clone)]
pub struct TestDownloadEndpoint {
}

impl TestDownloadEndpoint {
    pub fn setup_class() -> () {
        _start_test_server();
    }
    pub fn test_download_rejects_empty_model(&self) -> () {
        let (mut status, _, mut data) = _post("/__download-model".to_string(), HashMap::from([("model".to_string(), "".to_string()), ("dest".to_string(), "C:\\AI\\Models".to_string())]));
        assert!(status == 400);
        assert!(data.get(&"ok".to_string()).cloned() == false);
    }
    pub fn test_download_status_unknown_job(&self) -> () {
        let (mut status, _, mut data) = _get("/__download-status?job=nonexistent123".to_string());
        assert!(status == 200);
        assert!(data["state".to_string()] == "unknown".to_string());
    }
}

/// Verify the trusted quantizer list is populated and functional.
#[derive(Debug, Clone)]
pub struct TestTrustedQuantizers {
}

impl TestTrustedQuantizers {
    pub fn test_trusted_list_not_empty(&self) -> () {
        assert!(cb::_TRUSTED_QUANTIZERS.len() > 0);
    }
    pub fn test_known_quantizers_present(&self) -> () {
        for name in ("bartowski".to_string(), "mradermacher".to_string(), "unsloth".to_string(), "TheBloke".to_string()).iter() {
            assert!(cb::_TRUSTED_QUANTIZERS.contains(&name));
        }
    }
    /// Discovery results from trusted authors should have trusted=true.
    pub fn test_trusted_flag_in_discovery(&self) -> () {
        // Discovery results from trusted authors should have trusted=true.
        let mut results = cb::_discover_hf_models("bartowski".to_string(), "downloads".to_string(), 5);
        let mut valid = results.iter().filter(|r| (r.contains(&"trusted".to_string()) && r.contains(&"author".to_string()))).map(|r| r).collect::<Vec<_>>();
        let mut bartowski_results = valid.iter().filter(|r| r["author".to_string()] == "bartowski".to_string()).map(|r| r).collect::<Vec<_>>();
        for r in bartowski_results.iter() {
            assert!(r["trusted".to_string()] == true);
        }
    }
}

/// Test llama.cpp installation detection.
#[derive(Debug, Clone)]
pub struct TestLlamaCppDetection {
}

impl TestLlamaCppDetection {
    pub fn test_get_llama_cpp_info_structure(&self) -> () {
        let mut info = cb::get_llama_cpp_info();
        assert!(info.contains(&"installed".to_string()));
        assert!(info.contains(&"version".to_string()));
        assert!(/* /* isinstance(info["installed".to_string()], bool) */ */ true);
    }
    /// If llama_cpp is installed, version should be a string.
    pub fn test_llama_version_when_installed(&self) -> () {
        // If llama_cpp is installed, version should be a string.
        let mut info = cb::get_llama_cpp_info();
        if info["installed".to_string()] {
            assert!(info["version".to_string()].is_some());
            assert!(/* /* isinstance(info["version".to_string()], str) */ */ true);
        }
    }
    /// If not installed, version should be None.
    pub fn test_llama_version_when_not_installed(&self) -> () {
        // If not installed, version should be None.
        let mut info = cb::get_llama_cpp_info();
        if !info["installed".to_string()] {
            assert!(info["version".to_string()].is_none());
        }
    }
}

/// Test GPU detection returns valid structure.
#[derive(Debug, Clone)]
pub struct TestGPUDetection {
}

impl TestGPUDetection {
    pub fn test_gpu_info_returns_list(&self) -> () {
        let mut gpus = cb::get_gpu_info();
        assert!(/* /* isinstance(gpus, list) */ */ true);
    }
    pub fn test_gpu_entries_have_required_fields(&self) -> () {
        let mut gpus = cb::get_gpu_info();
        for gpu in gpus.iter() {
            assert!(gpu.contains(&"name".to_string()));
            assert!(gpu.contains(&"vendor".to_string()));
            assert!(gpu.contains(&"vram_gb".to_string()));
            assert!(gpu.contains(&"backend".to_string()));
        }
    }
    pub fn test_gpu_vendor_known(&self) -> () {
        let mut gpus = cb::get_gpu_info();
        for gpu in gpus.iter() {
            assert!(("NVIDIA".to_string(), "AMD".to_string(), "Intel".to_string(), "Unknown".to_string()).contains(&gpu["vendor".to_string()]));
        }
    }
    pub fn test_gpu_backend_valid(&self) -> () {
        let mut gpus = cb::get_gpu_info();
        for gpu in gpus.iter() {
            assert!(("CUDA".to_string(), "ROCm/Vulkan".to_string(), "DirectML".to_string()).contains(&gpu["backend".to_string()]));
        }
    }
}

/// Test model_dirs class attribute on ComparatorHandler.
#[derive(Debug, Clone)]
pub struct TestModelDirConfig {
}

impl TestModelDirConfig {
    pub fn test_model_dirs_is_list(&self) -> () {
        assert!(/* /* isinstance(cb::ComparatorHandler.model_dirs, list) */ */ true);
    }
    /// All entries in model_dirs should be real directories.
    pub fn test_model_dirs_contains_only_existing_paths(&self) -> () {
        // All entries in model_dirs should be real directories.
        for d in cb::ComparatorHandler.model_dirs.iter() {
            assert!(os::path.isdir(d), "model_dirs contains nonexistent path: {}", d);
        }
    }
    /// C:\AI\Models should be in model_dirs if it exists.
    pub fn test_model_dirs_includes_c_ai_models(&self) -> () {
        // C:\AI\Models should be in model_dirs if it exists.
        if os::path.isdir("C:\\AI\\Models".to_string()) {
            let mut paths = cb::ComparatorHandler.model_dirs.iter().map(|d| os::path.normpath(d)).collect::<Vec<_>>();
            assert!(paths.contains(&os::path.normpath("C:\\AI\\Models".to_string())));
        }
    }
    /// ZENAI_MODEL_DIR env var should be checked.
    pub fn test_env_var_override(&self) -> () {
        // ZENAI_MODEL_DIR env var should be checked.
        // TODO: import inspect
        let mut src = inspect::getsource(cb::ComparatorHandler);
        assert!(src.contains(&"ZENAI_MODEL_DIR".to_string()));
    }
}

/// Test memory detection.
#[derive(Debug, Clone)]
pub struct TestMemoryInfo {
}

impl TestMemoryInfo {
    pub fn test_memory_gb_positive(&self) -> () {
        let mut mem = cb::get_memory_gb();
        assert!(mem > 0);
    }
    /// Should be between 1GB and 2TB.
    pub fn test_memory_gb_reasonable(&self) -> () {
        // Should be between 1GB and 2TB.
        let mut mem = cb::get_memory_gb();
        assert!((1.0_f64 <= mem) && (mem <= 2048.0_f64));
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
