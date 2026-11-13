# Zen LLM Compare vs LangWatch vs MLflow — Feature Gap Analysis

## Platform Overview

| Dimension | Zen LLM Compare | LangWatch | MLflow |
|---|---|---|---|
| **Stars** | — (private) | 3.2k | 25k |
| **Downloads/mo** | — | — | 60M+ |
| **Language** | Python + HTML (single-file SPA) | TypeScript 73%, Python 5.6% | Python 61%, TypeScript 28% |
| **Philosophy** | Zero-cloud, zero-install, local GGUF | SaaS-first, self-host option | Self-host first, vendor-neutral |
| **Install** | `pip install llama-cpp-python psutil huggingface_hub` | Docker Compose (Postgres + OpenSearch) | `uvx mlflow server` |
| **Dependencies** | 3 production | Heavy (Next.js, Postgres, OpenSearch) | Moderate (`mlflow` package) |
| **License** | Private | ELv2 | Apache 2.0 |

---

## Feature Comparison Matrix

### 1. Model Comparison & Inference

| Feature | Zen ✅/❌ | LangWatch | MLflow |
|---|---|---|---|
| Local GGUF model loading | ✅ 64+ catalog | ❌ | ❌ (via Ollama integration) |
| Side-by-side comparison (1-8 models) | ✅ | ❌ | ❌ |
| Hardware detection (RAM/CPU/GPU) | ✅ | ❌ | ❌ |
| SSE streaming | ✅ | — | — |
| Position-bias mitigation in judging | ✅ | ❌ | ❌ |
| Monkey Mode (unattended regression) | ✅ | ❌ | ❌ |
| ELO leaderboard | ✅ | ❌ | ❌ |
| Quantization-aware model selection | ✅ | ❌ | ❌ |

**Verdict:** Zen owns the "local GGUF comparison" niche. Neither LangWatch nor MLflow does this.

---

### 2. Evaluation Framework

| Feature | Zen ✅/❌ | LangWatch | MLflow |
|---|---|---|---|
| LLM-as-Judge | ✅ 5 templates | ✅ boolean/category/score/rubrics | ✅ built-in + custom judges |
| Built-in evaluator library | ❌ ad-hoc | ✅ 30+ evaluators | ✅ 50+ scorers |
| RAG-specific evaluators | ❌ | ✅ RAGAS (context precision/recall, faithfulness, F1) | ✅ RetrievalGroundedness, RetrievalRelevance, RetrievalSufficiency |
| Code-based scorers (BLEU, ROUGE, exact match) | ❌ | ✅ | ✅ |
| Safety evaluators (PII, jailbreak, toxicity) | ❌ | ✅ (Azure Content Safety, Presidio) | ✅ Safety judge |
| Multi-turn conversation evaluation | ❌ | ❌ | ✅ (ConversationCompleteness, UserFrustration, KnowledgeRetention) |
| Custom scorer API | ❌ | ✅ | ✅ `@scorer` decorator |
| Judge alignment with human feedback | ❌ | ❌ | ✅ DSPy-powered auto-alignment |
| Semantic similarity | ❌ | ✅ | ✅ |
| Tool call correctness | ❌ | ❌ | ✅ ToolCallCorrectness, ToolCallEfficiency |
| Judge versioning | ❌ | ❌ | ✅ |

**Key Gaps for Zen:**
- No standardized evaluator library (only 5 ad-hoc judge templates)
- No code-based metrics (BLEU, ROUGE, semantic similarity)
- No RAG or safety evaluators
- No pluggable scorer architecture

---

### 3. Dataset Management

| Feature | Zen ✅/❌ | LangWatch | MLflow |
|---|---|---|---|
| Question bank | ✅ 32 prompts (hardcoded) | ✅ managed datasets | ✅ Evaluation Datasets |
| CSV/JSONL import | ❌ | ✅ | ✅ |
| Version management | ❌ | ✅ | ✅ |
| Expected answers / ground truth | ❌ | ✅ | ✅ `expectations` field |
| Dataset from production traces | ❌ | ✅ | ✅ |
| Programmatic SDK access | ❌ | ✅ | ✅ |
| Excel-like editor UI | ❌ | ✅ | ✅ |

**Key Gap:** Zen's 32 hardcoded prompts cannot compete. Both competitors offer version-managed datasets with ground truth, import/export, and programmatic access.

---

### 4. Experiment Tracking & Comparison

| Feature | Zen ✅/❌ | LangWatch | MLflow |
|---|---|---|---|
| Run history persistence | ❌ localStorage only | ✅ Postgres | ✅ SQL/file backend |
| Cross-run comparison | ❌ | ✅ experiments UI | ✅ experiment tracking |
| Metrics over time / trend tracking | ❌ | ✅ | ✅ |
| Parameters/hyperparams logging | ❌ | ✅ | ✅ |
| Artifact storage | ❌ | ✅ | ✅ |
| Model registry | ❌ | ❌ | ✅ full lifecycle |
| CSV/HTML export | ✅ | ✅ | ✅ |

**Key Gap:** localStorage is fragile. Enhancement E7 (SQLite) is the minimum; both competitors offer SQL-backed experiment stores with comparison UIs and trend tracking.

---

### 5. Tracing & Observability

| Feature | Zen ✅/❌ | LangWatch | MLflow |
|---|---|---|---|
| Request/response tracing | ❌ | ✅ OpenTelemetry-native | ✅ OpenTelemetry-compatible |
| Trace visualization UI | ❌ | ✅ | ✅ |
| Token cost tracking | ✅ (tokens/sec, prompt/completion) | ✅ | ✅ |
| Latency breakdown | ✅ (total time, TPS) | ✅ per-span | ✅ per-span |
| PII masking | ❌ | ❌ | ✅ |
| Distributed tracing | ❌ | ✅ | ✅ |
| Production monitoring | ❌ | ✅ online evaluators/guardrails | ✅ async trace logging |
| Auto-instrumentation (1-line) | ❌ | ✅ | ✅ 60+ frameworks |

**Key Gap:** Zen captures basic timing/token metrics but has no structured tracing, no trace visualization, and no production monitoring.

---

### 6. Prompt Management

| Feature | Zen ✅/❌ | LangWatch | MLflow |
|---|---|---|---|
| System prompt per run | ❌ (planned E9) | ✅ | ✅ |
| Prompt versioning | ❌ | ✅ Git-integrated | ✅ commit-based versioning |
| Prompt templates (variables) | ❌ | ✅ | ✅ `{{variable}}` + Jinja2 |
| Prompt comparison (diff) | ❌ | ✅ | ✅ side-by-side diff |
| Model config per prompt | ❌ | ❌ | ✅ `PromptModelConfig` |
| Prompt aliases (prod/staging) | ❌ | ❌ | ✅ |
| Prompt caching | ❌ | ❌ | ✅ with configurable TTL |
| Prompt optimization (auto) | ❌ | ❌ | ✅ DSPy-powered |

**Key Gap:** Zen has no prompt management at all. MLflow's Prompt Registry is the gold standard here.

---

### 7. Agent Testing & Simulation

| Feature | Zen ✅/❌ | LangWatch | MLflow |
|---|---|---|---|
| Scenario testing (multi-turn agent sims) | ❌ | ✅ AgentAdapter + UserSimulator + JudgeAgent | ❌ (multi-turn judges only) |
| Scriptable user flows | ❌ | ✅ `scenario.user()` / `scenario.agent()` | ❌ |
| Deterministic replay (`@cache`) | ❌ | ✅ | ❌ |

---

### 8. CI/CD & Integration

| Feature | Zen ✅/❌ | LangWatch | MLflow |
|---|---|---|---|
| CI/CD integration | ❌ | ✅ GitHub Actions | ✅ pytest + `mlflow.evaluate()` |
| MCP server | ❌ (planned E5) | ✅ | ❌ |
| API/SDK for external tools | ❌ | ✅ Python + TS SDK | ✅ Python + TS + Java + R |
| AI Gateway (multi-provider routing) | ❌ | ❌ | ✅ with A/B, fallback, cost tracking |

---

### 9. Deployment

| Feature | Zen ✅/❌ | LangWatch | MLflow |
|---|---|---|---|
| Docker deployment | ❌ (planned E6) | ✅ | ✅ |
| Zero-install local | ✅ | ❌ | ✅ (`uvx mlflow server`) |
| Cloud-hosted option | ❌ | ✅ (SaaS) | ✅ (Databricks, AWS, Azure) |

---

## What Zen LLM Compare Does Better Than Both

1. **Local GGUF inference** — Neither LangWatch nor MLflow loads local GGUF models; Zen is the only tool offering side-by-side comparison of local quantized models
2. **Zero-cloud, zero-install** — 3 pip dependencies, pure stdlib HTTP server, single HTML frontend
3. **Position-bias mitigation** — Randomizes model presentation order to eliminate judging bias
4. **Hardware-aware selection** — Detects RAM/CPU/GPU and recommends viable models
5. **ELO rating system** — Persistent skill rating across comparison runs
6. **Monkey Mode** — Unattended regression testing across the full question bank

---

## Actionable Recommendations: What to Implement

### Tier 1 — High Value, Fits Zero-Cloud Philosophy (Implement Now)

#### R1. Pluggable Scorer Architecture
**Learn from:** Both MLflow and LangWatch  
**Effort:** 3 days · **Impact:** Transformational  
**What:** Replace the 5 ad-hoc judge templates with a pluggable scorer system:
```python
class Scorer:
    name: str
    def score(self, prompt: str, response: str, expected: str = None) -> dict:
        """Returns {"score": float, "rationale": str, "pass": bool}"""

class LLMJudgeScorer(Scorer):       # Existing 5 templates, refactored
class ExactMatchScorer(Scorer):      # From LangWatch/MLflow
class BLEUScorer(Scorer):            # pip install nltk (or sacrebleu)
class ROUGEScorer(Scorer):           # pip install rouge-score
class SemanticSimilarityScorer(Scorer):  # sentence-transformers
class FormatValidationScorer(Scorer):    # Regex/schema check
```
The 5 existing judge templates become `LLMJudgeScorer` instances. New code-based scorers add deterministic metrics that don't require an external LLM.

**Aligns with:** Enhancement_plan.md concept but goes further with a formal interface.

---

#### R2. Dataset Manager with Ground Truth
**Learn from:** Both (MLflow's `data=[{inputs, expectations}]` pattern is cleanest)  
**Effort:** 2 days · **Impact:** High  
**What:**
- Upgrade the 32-prompt question bank from a hardcoded JS array to a JSON/CSV file on disk
- Each entry: `{id, category, prompt, expected_answer?, metadata?}`
- Add import/export endpoints: `GET/POST /__datasets`
- Support multiple named datasets (e.g., "emergency_v2", "coding_hard")
- Version with simple file naming: `datasets/emergency_v2.json`

This also completes E3 (question bank expansion) in a structured way.

---

#### R3. SQLite Experiment Store (Upgrade E7)
**Learn from:** MLflow's experiment tracking  
**Effort:** 3 days (expanded from E7's 2 days) · **Impact:** High  
**What:** Go beyond just ELO persistence. Store:
```sql
CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    timestamp TEXT, prompt TEXT, dataset_id TEXT,
    system_prompt TEXT, params JSON  -- temperature, max_tokens, etc.
);
CREATE TABLE results (
    run_id INTEGER, model_name TEXT,
    response TEXT, tokens_prompt INT, tokens_completion INT,
    time_seconds REAL, tps REAL, ram_delta_mb REAL,
    scores JSON,  -- {"judge": 8.2, "bleu": 0.45, "rouge_l": 0.62}
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE elo_ratings (
    model_name TEXT PRIMARY KEY, rating REAL, games INT
);
```
Add endpoints: `GET /__runs?last=50`, `GET /__runs/{id}`.
Frontend: "History" tab showing past runs with filtering and comparison.

**Benefit:** Unlocks trend tracking, regression detection, and cross-run comparison — the core value of MLflow's experiment tracking, with zero cloud.

---

#### R4. System Prompt Editor (Upgrade E9)
**Learn from:** MLflow's Prompt Registry  
**Effort:** 1.5 days · **Impact:** Medium  
**What:**
- Named prompt presets stored in SQLite: `{name, system_prompt, temperature, max_tokens}`
- Quick-switch dropdown in the UI
- Each run records which preset was used → full reproducibility

---

### Tier 2 — Strategic Value, Medium Effort

#### R5. CI/CD Quality Gate (pytest integration)
**Learn from:** Both (MLflow's `mlflow.genai.evaluate()` pattern)  
**Effort:** 2 days · **Impact:** High (strategic)  
**What:** A Python test you run in CI:
```python
# test_model_quality.py
from zen_compare import ZenCompare, ExactMatchScorer, LLMJudgeScorer

def test_medical_model_quality():
    zc = ZenCompare(backend_url="http://localhost:8123")
    results = zc.evaluate(
        dataset="datasets/emergency_v2.json",
        models=["BioMistral-7B-Q4_K_M.gguf"],
        scorers=[LLMJudgeScorer("medical_accuracy"), ExactMatchScorer()],
    )
    assert results.avg_score("medical_accuracy") >= 7.0
    assert results.pass_rate("exact_match") >= 0.6
```
Publish as a Python package (`zen-compare`) so CI pipelines can `pip install` it.

**Aligns with:** E5 (MCP) conceptually — both expose programmatic access.

---

#### R6. Structured Tracing
**Learn from:** MLflow Tracing (OpenTelemetry)  
**Effort:** 3 days · **Impact:** Medium  
**What:** Add a lightweight tracing layer without requiring OpenTelemetry infra:
```python
@dataclass
class Span:
    name: str  # "model_load", "inference", "judge"
    start_ms: int
    end_ms: int
    metadata: dict  # tokens, model_path, error, etc.

@dataclass
class Trace:
    id: str
    prompt: str
    spans: list[Span]
```
- Instrument `_run_one_model()` to emit spans: model_load → inference → judge
- Store traces in SQLite (from R3)
- Frontend: expandable trace view showing time breakdown per span

This is a **minimal** version of MLflow's tracing. Not full OpenTelemetry — just structured timing with stored traces.

---

#### R7. MCP Server (Enhance E5)
**Learn from:** LangWatch MCP tools  
**Effort:** 3 days · **Impact:** High (strategic)  
**What:** Same as E5 but expanded with dataset and scorer tools:
- `compare_models(prompt, models, scorers)` → scored results
- `list_models()` → model inventory
- `list_datasets()` → available datasets
- `run_evaluation(dataset, models, scorers)` → full eval report
- `get_run_history(last_n)` → recent runs

---

### Tier 3 — Nice to Have (Borrow Ideas, Don't Over-Engineer)

#### R8. Batch Evaluation Mode (enhanced Monkey Mode)
**Learn from:** MLflow's `mlflow.genai.evaluate(data=dataset, predict_fn=...)` + LangWatch Experiments  
**Effort:** 2 days · **Impact:** Medium  
**What:** Upgrade Monkey Mode from "cycle through questions" to "run a full dataset through N models with M scorers, store all results, show comparison matrix."

This is the convergence of R1 (scorers) + R2 (datasets) + R3 (SQLite store).

---

#### R9. Human Feedback Collection
**Learn from:** MLflow's feedback annotations  
**Effort:** 1 day · **Impact:** Low-Medium  
**What:** Add thumbs-up/down + free-text annotation on each model response. Store in SQLite alongside auto-scores. Use to calibrate LLM judges over time.

---

#### R10. Prompt Diff Comparison
**Learn from:** MLflow Prompt Registry  
**Effort:** 1 day · **Impact:** Low  
**What:** When using named prompt presets (R4), show a side-by-side diff when switching versions.

---

## Implementation Roadmap

```
Week 1:  R1 (Scorer Architecture) + R2 (Dataset Manager)
Week 2:  R3 (SQLite Experiment Store) + R4 (System Prompt Editor)  
Week 3:  R5 (CI/CD Quality Gate) + R8 (Enhanced Batch Eval)
Week 4:  R6 (Structured Tracing) + R7 (MCP Server)
         R9, R10 as time permits
```

**Total: ~20 engineering-days for Tier 1 + Tier 2**

---

## What NOT to Implement

| LangWatch/MLflow Feature | Why Skip |
|---|---|
| Full OpenTelemetry infra | Violates zero-cloud; R6's lightweight tracing is sufficient |
| Postgres/OpenSearch backend | Overkill; SQLite is perfect for single-user local tool |
| AI Gateway / multi-provider routing | Zen is local GGUF only; not relevant |
| Model Registry (MLflow) | No model lifecycle; users just download GGUFs |
| Prompt optimization (DSPy auto-tuning) | Requires cloud LLM; breaks zero-install |
| Agent simulation framework | Not Zen's use case (model comparison, not agent testing) |
| Production monitoring / guardrails | Zen is a dev tool, not a production inference server |
| Annotations/collaboration features | Single-user tool; R9 (simple feedback) is sufficient |

---

## Summary

Zen LLM Compare's unique value is **local, zero-cloud, hardware-aware GGUF model comparison**. Neither LangWatch nor MLflow competes in this niche.

The biggest learnings from both platforms:
1. **Structured evaluation** (scorer interface + deterministic metrics) — not just LLM-as-judge
2. **Dataset management** (versioned, with ground truth) — not just hardcoded prompts  
3. **Experiment persistence** (SQL-backed, with cross-run comparison) — not just localStorage
4. **CI/CD integration** (programmatic SDK, pytest-friendly) — not just browser UI
5. **Prompt versioning** (named presets with full reproducibility) — not just raw text input

These five gaps can be closed in ~20 engineering-days while preserving the zero-cloud, zero-install philosophy.
