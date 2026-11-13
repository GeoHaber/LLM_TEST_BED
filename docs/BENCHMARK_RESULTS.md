# LLM Test Bed - Golden Benchmark Results

**Date:** 2026-04-03
**Machine:** AMD Ryzen AI 9 HX 370 (24 cores, 92 GB RAM)
**Judge:** google_gemma-4-26B-A4B-it-Q8_0 (25 GB) - SEPARATE from test models
**Questions:** 5 (math, reasoning, code review, general knowledge, formal logic)
**Inference:** In-process llama-cpp-python, 14 models parallel, n_ctx=2048

## Final Rankings

| Rank | Model | Avg Score | Correct | Avg Time | Tok/s | Size | Efficiency |
|-----:|-------|----------:|--------:|---------:|------:|-----:|-----------:|
| 1 | gemma-4-E4B-it-Q4_K_M | **10.0** | **100%** | 32.6s | 2.6 | 4.6G | 2.16 |
| 2 | Qwen3.5-4B-Q4_K_M | **9.8** | **100%** | 24.0s | 4.2 | 2.6G | 3.84 |
| 3 | Phi-3.5-mini-instruct-Q4_K_M | 8.0 | 80% | 13.9s | 7.6 | 2.2G | 3.59 |
| 4 | nerdsking-python-coder-7B | 8.0 | 80% | 19.0s | 3.2 | 5.1G | 1.58 |
| 5 | qwen2.5-coder-7b-instruct | 8.0 | 80% | 32.3s | 2.7 | 4.4G | 1.83 |
| 6 | Llama-3.2-3B-Instruct | 6.4 | 80% | 12.2s | 8.0 | 1.9G | 3.40 |
| 7 | LFM2.5-1.2B-Instruct-Q8_0 | 6.2 | 40% | 4.9s | 16.2 | 1.2G | 5.34 |
| 8 | qwen2.5-0.5b-instruct | 6.0 | 40% | 2.9s | 27.8 | 0.5G | 12.34 |
| 9 | Mistral-7B-Instruct-v0.3 | 5.8 | 40% | 27.2s | 4.0 | 4.1G | 1.42 |
| 10 | Mistral-7B (base) | 3.4 | 40% | 26.6s | 3.3 | 4.1G | 0.84 |
| 11 | deepseek-coder-6.7b | 2.2 | 20% | 37.7s | 6.5 | 3.8G | 0.58 |
| 12 | phi-2-2.7b | 2.0 | 40% | 11.9s | 9.2 | 1.7G | 1.20 |
| 13 | SmolLM2-135M | 0.8 | 20% | 1.2s | 84.5 | 0.1G | 8.11 |
| 14 | tinyllama-1.1b | 0.4 | 20% | 5.3s | 23.2 | 0.6G | 0.64 |

## Key Insights

- **Quality King:** Gemma-4-E4B (4.6 GB) - perfect 10.0 score, 100% correct
- **Best Value:** Qwen3.5-4B (2.6 GB) - near-perfect 9.8 at half the size, 60% faster
- **Speed King:** SmolLM2-135M at 84.5 tok/s (but only 20% correct)
- **Best Efficiency:** qwen2.5-0.5b (score/GB = 12.34) - decent answers at 500 MB
- **Instruct matters:** Mistral-7B base (3.4) vs Instruct (5.8) = 70% improvement

## Parallel Stress Test (same session)

All 25 GGUF models loaded and ran parallel inference with 0 failures.

| Batch | Wall Time | Avg/Model | RAM Usage |
|------:|----------:|----------:|----------:|
| 8 | 4.5s | 2.3s | 65% |
| 12 | 10.2s | 4.7s | 91% |
| 14 | 13.7s | 6.8s | 99% |
| 16 | 30.3s | 14.8s | 99% |
| 25 | 184.1s | 94.9s | 100% |

**Sweet spot:** 12-14 models parallel (fast, no swap thrashing).
