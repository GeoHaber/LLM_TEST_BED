# Skill Lab — Priority TODO

> Updated: 2026-04-14 | Status: Engine v2 + Card UI shipped, training data pipeline live

---

## What's Done

| Feature | Status |
|---------|--------|
| 3-agent optimizer (Executor → Analyst → Mutator) | ✅ Shipped |
| OPRO score history injected into Analyst prompt | ✅ Shipped |
| GEPA trajectory reflection (full execution traces) | ✅ Shipped |
| DPO pair generation (chosen/rejected/margin) | ✅ Shipped |
| SFT example collection from passing traces | ✅ Shipped |
| Convergence detection with patience parameter | ✅ Shipped |
| `export_training_data()` JSONL export | ✅ Shipped |
| ZenAIos Display Card UI (12-col grid, category tints) | ✅ Shipped |
| Training Data card with DPO/SFT counts + export buttons | ✅ Shipped |
| Backend patience pass-through + dpo/sft in final_result | ✅ Shipped |
| 58 unit tests (100% green, TDD-driven) | ✅ Shipped |

---

## Priority 1 — Critical (Model Retraining Pipeline)

These directly enable the goal: **retrain our models to be better at the tasks we have.**

### P1.1 · End-to-End Training Data → Fine-Tune Pipeline
**Why:** DPO/SFT JSONL export exists but there's no automated path from export → actual fine-tuning.
- [ ] Write a `train_from_skill_data.py` script that:
  - Takes exported DPO/SFT JSONL files
  - Formats them for LlamaFactory / Unsloth / TorchTune
  - Launches a fine-tune run with auto-detected hardware (ZenForge principle)
- [ ] Support both SFT (supervised) and DPO (preference) training modes
- [ ] Auto-convert output to GGUF via `llama.cpp convert_hf_to_gguf.py`
- **Effort:** 3–4 days | **Impact:** Highest — closes the loop from eval → better model

### P1.2 · Multi-Skill Data Aggregation
**Why:** One optimization run produces ~5–20 DPO pairs. Need hundreds for meaningful fine-tuning.
- [ ] Add `merge_training_data.py` that combines JSONL from multiple skill sessions
- [ ] Dedup by input hash, keep highest-margin DPO pairs
- [ ] Track provenance (which skill session produced each example)
- **Effort:** 1 day | **Impact:** High — makes the data volume practical

### P1.3 · Batch Skill Optimization
**Why:** Running skills one-at-a-time through the UI is slow. Need to optimize a whole skill library.
- [ ] CLI mode: `python skill_optimizer.py --batch skills_dir/ --model path.gguf --rounds 5`
- [ ] Aggregate all DPO/SFT data across skills into one training set
- [ ] Summary report: which skills improved, which plateaued
- **Effort:** 2 days | **Impact:** High — generates training data at scale

---

## Priority 2 — High Value (Engine Quality)

### P2.1 · EvoPrompt Population Diversity
**Why:** Current optimizer mutates one skill copy. EvoPrompt (ACL 2024) shows maintaining a population of N variants and crossing over the best ones finds better optima.
- [ ] Maintain a population of 3–5 skill variants per round
- [ ] Crossover: merge best sections from top-2 variants
- [ ] Selection: keep top-K by score, discard rest
- **Effort:** 2 days | **Impact:** Medium-High — better exploration of solution space

### P2.2 · TextGrad-Style Gradient Feedback (70B+ models)
**Why:** With large enough models, we can generate "text gradients" — natural language descriptions of exactly how to improve. Current Analyst gives high-level diagnosis; TextGrad gives token-level edit suggestions.
- [ ] Detect when model supports long context (>4K) and enable TextGrad mode
- [ ] Analyst prompt includes: "For each failing eval, describe the minimum edit to the skill text that would fix it"
- [ ] Mutator receives these micro-edits instead of broad strategies
- **Effort:** 2 days | **Impact:** Medium — better for capable models, marginal for small GGUF

### P2.3 · Eval Quality Bootstrapping
**Why:** The evals themselves might be weak. If an eval always passes or always fails, it provides zero signal.
- [ ] After baseline, flag degenerate evals (100% pass or 0% pass across all scenarios)
- [ ] Auto-suggest tighter/looser criteria based on baseline outputs
- [ ] Track eval discrimination power (how much does it differentiate good vs bad skills)
- **Effort:** 1 day | **Impact:** Medium — prevents wasted optimization rounds

---

## Priority 3 — Medium Value (UX & Observability)

### P3.1 · Score Trend Visualization
**Why:** Current bar chart shows per-round scores but no trendline or convergence indicator.
- [ ] Add a Canvas/SVG line chart with smoothed trendline
- [ ] Mark convergence point (where patience kicked in) with a vertical line
- [ ] Show DPO pair generation rate per round
- **Effort:** 1 day | **Impact:** Medium

### P3.2 · Side-by-Side Diff with Syntax Highlighting
**Why:** The diff view shows raw text. For SKILL.md files, markdown rendering + diff highlighting would make changes easier to review.
- [ ] Render original and improved as markdown
- [ ] Highlight added/removed lines with green/red backgrounds
- [ ] Collapsible unchanged sections
- **Effort:** 1 day | **Impact:** Medium

### P3.3 · Skill Library Browser
**Why:** No way to browse previously optimized skills or compare versions.
- [ ] List all sessions with date, skill name, score improvement
- [ ] One-click re-optimize from a previous session
- [ ] Compare two versions of the same skill
- **Effort:** 2 days | **Impact:** Medium

---

## Priority 4 — Future Research

### P4.1 · APO / PE2 Meta-Prompting
Automatic Prompt Optimization (APO) and PE2 use a meta-prompt that learns *how to write better prompts* across many optimization runs. This is a second-order improvement: the optimizer itself gets better over time.
- [ ] Store successful Analyst→Mutator strategies that led to score increases
- [ ] Feed these as few-shot examples into future Analyst prompts
- [ ] Track which strategy types have highest success rate per skill category

### P4.2 · PromptBreeder Self-Referential Improvement
PromptBreeder mutates not just the skill but the mutation prompts themselves. The Analyst and Mutator system prompts evolve alongside the skills.
- [ ] Allow the Analyst prompt itself to be a mutable artifact
- [ ] Score the Analyst's effectiveness (did its diagnosis lead to improvement?)
- [ ] Mutate underperforming Analyst prompts

### P4.3 · Reinforcement Learning from Skill Optimization (RLSO)
Instead of DPO pairs → offline training, use the optimization loop as an online RL environment.
- [ ] Reward = score delta per round
- [ ] Action = which mutation strategy to apply
- [ ] State = current skill + score history + failure patterns
- [ ] Train a small policy network to pick strategies

---

## Research ↔ Implementation Gap Analysis

| Research Framework | Key Idea | Our Implementation | Gap |
|---|---|---|---|
| DSPy / GEPA (ICLR 2026) | Trajectory reflection + training data | ✅ Full traces in Analyst prompt, DPO/SFT export | Need population diversity |
| OPRO (ICLR 2024) | Score history in optimizer prompt | ✅ Score trajectory injected into Analyst | — |
| EvoPrompt (ACL 2024) | Evolutionary population | ❌ Single-variant mutation | P2.1 |
| TextGrad (NeurIPS 2024) | Text-as-gradient feedback | ❌ High-level diagnosis only | P2.2 |
| APE (Zhou et al. 2023) | Auto prompt engineer | ✅ Mutator generates edits | Need meta-learning (P4.1) |
| APO / PE2 | Meta-prompting | ❌ Not implemented | P4.1 |
| PromptBreeder | Self-referential mutation | ❌ Not implemented | P4.2 |

---

## Recommended Execution Order

```
Week 1:  P1.2 (merge data) → P1.3 (batch optimize) → generate ~500 DPO pairs
Week 2:  P1.1 (training pipeline) → first fine-tuned model from skill data
Week 3:  P2.3 (eval quality) → P2.1 (population) → re-run batch with better engine
Week 4:  P3.1-P3.3 (UX polish) → P2.2 (TextGrad for large models)
Ongoing: P4.x (research experiments as time permits)
```
