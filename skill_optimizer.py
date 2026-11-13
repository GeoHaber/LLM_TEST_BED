"""
Multi-Agent Skill Optimizer (Local GGUF)
========================================
Three agent personas collaborate to test, diagnose, and fix agent skills
using local GGUF models via llama-cpp-python.

Agents:
    Executor  — Runs the skill against test scenarios, scores outputs, generates test configs
    Analyst   — Diagnoses failure patterns, picks a mutation strategy
    Mutator   — Applies ONE surgical fix to the skill per round

Inspired by Karpathy's autoresearch and Shubhamsaboo's self-improving-agent-skills.
Runs entirely offline on local GGUF models. No cloud API required.
"""

import json
import os
import re
import threading
import time
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Optional


# ── Pydantic schemas for structured agent output ─────────────────────────────

try:
    from pydantic import BaseModel, Field
except ImportError:  # xray: ignore[QUAL-002]
    # Lightweight fallback if pydantic not available at import time
    BaseModel = object  # type: ignore[misc,assignment]
    Field = lambda **kw: None  # type: ignore[assignment] # noqa: E731


class FailureAnalysis(BaseModel):
    """Structured output from the Analyst agent."""
    diagnosis: str = Field(default="", description="Root cause of failures")
    mutation_strategy: str = Field(
        default="add_constraint",
        description="One of: add_example, add_constraint, restructure, add_edge_case",
    )
    target_section: str = Field(default="", description="Which part of the skill to change")
    suggested_change: str = Field(default="", description="What specific change to make")


class SkillMutation(BaseModel):
    """Structured output from the Mutator agent."""
    description: str = Field(default="", description="Short description of the change made")
    reasoning: str = Field(default="", description="Why this change should help")
    new_skill_content: str = Field(default="", description="The full updated skill content")


# ── Agent system prompts ─────────────────────────────────────────────────────

EXECUTOR_SYSTEM = """You are a versatile skill execution and evaluation agent. You have three modes:

1. EXECUTE MODE: Given a skill's instructions and a user request, produce the output that skill would generate. Follow the instructions exactly. No meta-commentary — just produce the output as the skill would.  # xray: ignore[QUAL-013]

2. ANALYZE MODE: Given a skill definition, generate test scenarios and evaluation criteria. Return valid JSON only.

3. SCORE MODE: Given an output and evaluation criteria, score the output against each criterion. Return valid JSON only."""

ANALYST_SYSTEM = """You diagnose why agent skill evaluations fail. Given failed eval results, identify the root cause and suggest a specific fix.

You MUST pick exactly ONE mutation_strategy from: add_example, add_constraint, restructure, add_edge_case.

Strategies:
- add_example: Add a concrete example to the skill instructions showing the expected behavior
- add_constraint: Add a specific rule or constraint the skill must follow
- restructure: Reorganize sections for clarity or reorder instructions for priority
- add_edge_case: Handle a specific edge case that was causing failures

Return ONLY valid JSON with these fields:
{"diagnosis": "...", "mutation_strategy": "...", "target_section": "...", "suggested_change": "..."}"""

MUTATOR_SYSTEM = """You edit agent skill files. Given a skill definition and a diagnosis, make exactly ONE targeted change. Keep the overall structure intact. Preserve any YAML frontmatter.

Return ONLY valid JSON with these fields:
{"description": "short description of change", "reasoning": "why this helps", "new_skill_content": "the FULL updated skill content"}"""


# ── Session management ───────────────────────────────────────────────────────

SESSION_TTL = 3600  # 1 hour
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB per file
MAX_FILE_COUNT = 50
MAX_OPTIMIZATION_ROUNDS = 30
ALLOWED_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts",
    ".html", ".css", ".xml", ".toml", ".cfg", ".ini", ".sh",
}

_skill_sessions: dict[str, dict] = {}
_skill_sessions_lock = threading.Lock()


def _is_allowed_file(name: str) -> bool:
    _, ext = os.path.splitext(name)
    return ext.lower() in ALLOWED_EXTENSIONS


def _is_safe_path(name: str) -> bool:
    """Reject path traversal attempts."""
    return ".." not in name and not os.path.isabs(name)


def parse_skill_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from SKILL.md (simple key: value parser)."""
    if not content.startswith("---"):
        return {}
    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        metadata = {}
        for line in parts[1].strip().split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith(" "):
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip('"')
        return metadata
    except Exception:  # xray: ignore[QUAL-002]  # xray: ignore[QUAL-011]
        return {}


def create_skill_session(skill_files: dict, file_list: list) -> dict:
    """Create a new skill optimization session."""
    # Find the main skill content (SKILL.md or largest .md file)
    skill_content = ""
    skill_key = ""
    for name, content in skill_files.items():
        if name.endswith("SKILL.md"):
            skill_content = content
            skill_key = name
            break
    if not skill_content:
        # Fallback: use the first .md file or first file
        for name, content in skill_files.items():
            if name.endswith(".md"):
                skill_content = content
                skill_key = name
                break
        if not skill_content and skill_files:
            skill_key = next(iter(skill_files))
            skill_content = skill_files[skill_key]

    if not skill_content:
        return {"error": "No skill content found in uploaded files"}

    metadata = parse_skill_frontmatter(skill_content)
    session_id = str(uuid.uuid4())

    session = {
        "session_id": session_id,
        "skill_files": skill_files,
        "file_list": file_list,
        "skill_key": skill_key,
        "metadata": metadata,
        "status": "uploaded",
        "scenarios": None,
        "evals": None,
        "experiments": [],
        "current_skill_content": skill_content,
        "original_skill_content": skill_content,
        "final_result": None,
        "error": None,
        "stop_requested": False,
        "created_at": time.time(),
    }

    with _skill_sessions_lock:
        _skill_sessions[session_id] = session

    return {
        "session_id": session_id,
        "file_list": file_list,
        "metadata": metadata,
        "skill_key": skill_key,
    }


def get_skill_session(session_id: str) -> Optional[dict]:
    with _skill_sessions_lock:
        return _skill_sessions.get(session_id)


def cleanup_expired_sessions():  # xray: ignore[DESTRUCT-007]
    """Remove sessions older than SESSION_TTL."""
    now = time.time()
    with _skill_sessions_lock:
        expired = [
            sid for sid, s in _skill_sessions.items()
            if now - s.get("created_at", now) > SESSION_TTL
            and s.get("status") not in ("running",)
        ]
        for sid in expired:
            del _skill_sessions[sid]
    return len(expired)


def extract_upload(raw_bytes: bytes) -> tuple[dict, list]:
    """Extract .zip upload into {filename: content} dict and file list.
    
    Raises ValueError on invalid input.
    """
    if len(raw_bytes) > MAX_UPLOAD_SIZE:
        raise ValueError(f"Upload exceeds {MAX_UPLOAD_SIZE // (1024*1024)}MB limit")

    try:
        with zipfile.ZipFile(BytesIO(raw_bytes)) as zf:
            skill_files = {}
            file_list = []
            for name in zf.namelist():
                if name.endswith("/") or name.startswith("__MACOSX") or ".DS_Store" in name:
                    continue
                if not _is_safe_path(name):
                    continue
                if not _is_allowed_file(name):
                    continue
                raw = zf.read(name)
                if len(raw) > MAX_FILE_SIZE:
                    continue
                if len(file_list) >= MAX_FILE_COUNT:
                    break
                skill_files[name] = raw.decode("utf-8", errors="ignore")
                file_list.append(name)

            # Normalize: strip common directory prefix
            if file_list:
                common = os.path.commonpath(file_list)
                if common and common != file_list[0]:
                    skill_files = {os.path.relpath(k, common): v for k, v in skill_files.items()}
                    file_list = [os.path.relpath(f, common) for f in file_list]

            return skill_files, file_list
    except zipfile.BadZipFile:
        raise ValueError("Invalid zip file")


# ── JSON extraction helpers ──────────────────────────────────────────────────

def _extract_json(text: str, fallback: Any = None) -> Any:
    """Extract JSON from LLM output, handling markdown fences and extra text."""
    if not text or not text.strip():
        return fallback

    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try finding first { or [ and raw_decode
    decoder = json.JSONDecoder()
    for start_char in ("{", "["):  # xray: ignore[PERF-001]
        idx = cleaned.find(start_char)
        if idx != -1:
            try:
                result, _ = decoder.raw_decode(cleaned, idx)
                return result
            except json.JSONDecodeError:
                pass

    return fallback


# ── Core Optimizer ───────────────────────────────────────────────────────────

class SkillOptimizer:
    """Multi-agent skill optimizer using local GGUF models.

    Uses the comparator backend's model loading infrastructure.
    """

    def __init__(self, model_path: str, n_ctx: int = 4096):
        self.model_path = model_path
        self.n_ctx = n_ctx

    def _chat(self, system_prompt: str, user_content: str) -> str:
        """Run a single chat completion against the local GGUF model."""
        # Import here to avoid circular dependency with comparator_backend
        from comparator_backend import _get_or_load_model, _build_messages

        llm = _get_or_load_model(self.model_path, self.n_ctx)
        messages = _build_messages(system_prompt, user_content, self.model_path)

        try:
            result = llm.create_chat_completion(
                messages=messages,
                max_tokens=2048,
                temperature=0.4,
                top_p=0.9,
                stream=False,
            )
            choices = result.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""
        except Exception as e:
            print(f"[skill_optimizer] Chat error: {e}", flush=True)  # xray: ignore[PY-004]
            return ""

    def _ask_json(self, system_prompt: str, user_content: str, fallback: Any = None) -> Any:
        """Run chat and parse JSON from the response."""
        text = self._chat(system_prompt, user_content)
        return _extract_json(text, fallback)

    def analyze_skill(self, skill_files: dict) -> dict:
        """Generate test scenarios and eval criteria from skill files.
        
        Returns: {"scenarios": [...], "evals": [...]}
        """
        # Find main skill content
        skill_content = ""
        for k, v in skill_files.items():
            if k.endswith("SKILL.md"):
                skill_content = v
                break
        if not skill_content:
            for k, v in skill_files.items():
                if k.endswith(".md"):
                    skill_content = v
                    break
        if not skill_content and skill_files:
            skill_content = next(iter(skill_files.values()))

        # Gather reference files
        refs = {k: v for k, v in skill_files.items() if "references/" in k}
        ref_text = ""
        if refs:
            ref_text = "\n\nReference files:\n" + "\n---\n".join(
                f"## {k}\n{v}" for k, v in refs.items()  # xray: ignore[QUAL-005]
            )

        prompt = (
            f"Analyze this agent skill and generate test scenarios "
            f"with evaluation criteria.\n\n"
            f"# Skill Content\n{skill_content[:4000]}\n{ref_text}\n\n"
            f"Generate:\n"
            f"1. 3-4 diverse test scenarios (realistic user inputs that exercise different aspects)\n"
            f"2. 4-6 binary yes/no evaluation criteria (things every good output should have)\n\n"
            f"Return ONLY valid JSON:\n"
            f'{{"scenarios": [{{"id": 1, "name": "short name", '
            f'"description": "what this tests", '
            f'"input": "the actual user request to test"}}], '
            f'"evals": [{{"id": 1, "name": "what to check", '
            f'"question": "yes/no question about the output", '
            f'"pass_condition": "what a pass looks like", '
            f'"fail_condition": "what a fail looks like"}}]}}'
        )

        result = self._ask_json(EXECUTOR_SYSTEM, prompt, fallback={"scenarios": [], "evals": []})

        # Validate structure
        if not isinstance(result, dict):
            result = {"scenarios": [], "evals": []}
        if "scenarios" not in result:
            result["scenarios"] = []
        if "evals" not in result:
            result["evals"] = []

        # Ensure IDs
        for i, s in enumerate(result["scenarios"]):
            if "id" not in s:
                s["id"] = i + 1
        for i, e in enumerate(result["evals"]):
            if "id" not in e:
                e["id"] = i + 1

        return result

    def _score_skill(self, skill_content: str, scenarios: list, evals: list) -> dict:
        """Executor runs all scenarios, then scores outputs against eval criteria.

        Returns dict with passed, total, per_eval, details, and traces
        (full execution traces for trajectory reflection).
        """
        all_results = []
        traces = []
        total_passed = 0
        total_checks = 0
        per_eval = {e["id"]: {"passed": 0, "total": 0} for e in evals}

        for sc in scenarios:
            # Executor runs the skill (free-form text output)
            output = self._chat(
                EXECUTOR_SYSTEM,
                f"Execute this skill — produce the output it would generate.\n\n"
                f"SKILL INSTRUCTIONS:\n{skill_content[:3000]}\n\n"
                f"USER REQUEST:\n{sc.get('input', sc.get('description', ''))}",
            )

            # Executor scores the output against criteria
            scoring = self._ask_json(
                EXECUTOR_SYSTEM,
                (
                    f"Evaluate this output against the criteria.\n\n"
                    f"Input: {sc.get('input', '')}\n\n"
                    f"Output:\n{output[:2000]}\n\n"
                    f"Criteria:\n{json.dumps(evals, indent=2)}\n\n"
                    f'Return ONLY valid JSON: {{"results": [{{"eval_id": 1, "passed": true, "reason": "..."}}]}}'
                ),
                fallback={"results": []},
            )

            scores = scoring.get("results", []) if isinstance(scoring, dict) else []

            # Build per-scenario trace with full output and eval results
            trace = {
                "scenario_id": sc["id"],
                "scenario_name": sc.get("name", ""),
                "input": sc.get("input", sc.get("description", "")),
                "output": output,
                "eval_results": list(scores),
            }
            traces.append(trace)

            for s in scores:
                eid = s.get("eval_id")
                passed = s.get("passed", False)
                if passed:
                    total_passed += 1
                total_checks += 1
                if eid in per_eval:
                    per_eval[eid]["total"] += 1
                    if passed:
                        per_eval[eid]["passed"] += 1
                all_results.append({**s, "scenario_id": sc["id"]})

        return {
            "passed": total_passed,
            "total": total_checks,
            "per_eval": [
                {
                    "eval_id": k,
                    **v,
                    "pass_rate": round(v["passed"] / max(v["total"], 1) * 100, 1),
                }
                for k, v in per_eval.items()  # xray: ignore[QUAL-005]
            ],
            "details": all_results,
            "traces": traces,
        }

    def _analyze_failures(
        self,
        skill_content: str,
        scenarios: list,
        evals: list,
        details: list,
        score_history: Optional[list] = None,
        traces: Optional[list] = None,
    ) -> dict:
        """Analyst agent diagnoses the worst failures.

        OPRO insight: includes score_history so the LLM can see the
        optimization trajectory and learn from prior rounds.
        GEPA insight: includes full execution traces, not just pass/fail.
        """
        failed = [d for d in details if not d.get("passed")]
        if not failed:
            return {
                "diagnosis": "All passed",
                "mutation_strategy": "add_constraint",
                "target_section": "N/A",
                "suggested_change": "none",
            }

        # Build OPRO-style trajectory context
        history_ctx = ""
        if score_history and len(score_history) > 1:
            history_lines = [f"  Round {i}: {s}%" for i, s in enumerate(score_history)]
            history_ctx = (
                f"\n\nScore trajectory (previous rounds):\n"
                + "\n".join(history_lines)
                + "\n"
            )

        # Build execution trace context (GEPA-style trajectory reflection)
        trace_ctx = ""
        if traces:
            trace_summaries = []
            for t in traces[:3]:  # limit to 3 for context window
                output_preview = t.get("output", "")[:300]
                trace_summaries.append(
                    f"  Scenario '{t.get('scenario_name', '')}': "
                    f"output='{output_preview}'"
                )
            trace_ctx = "\n\nExecution traces:\n" + "\n".join(trace_summaries) + "\n"

        return self._ask_json(
            ANALYST_SYSTEM,
            (
                f"Diagnose these failures and suggest ONE fix.\n\n"
                f"Skill:\n{skill_content[:2000]}\n\n"
                f"Failed evaluations:\n{json.dumps(failed[:5], indent=2)}"
                f"{history_ctx}{trace_ctx}"
            ),
            fallback={
                "diagnosis": "Unable to diagnose",
                "mutation_strategy": "add_constraint",
                "target_section": "unknown",
                "suggested_change": "unclear",
            },
        )

    def _mutate_skill(self, skill_content: str, analysis: dict) -> dict:
        """Mutator agent makes one targeted change."""
        return self._ask_json(
            MUTATOR_SYSTEM,
            (
                f"Apply this fix to the skill. Make ONE change only.\n\n"
                f"SKILL CONTENT:\n{skill_content}\n\n"
                f"Diagnosis: {analysis.get('diagnosis')}\n"
                f"Strategy: {analysis.get('mutation_strategy')}\n"
                f"Target: {analysis.get('target_section')}\n"
                f"Change: {analysis.get('suggested_change')}"
            ),
            fallback={
                "description": "Failed to generate mutation",
                "reasoning": "",
                "new_skill_content": skill_content,
            },
        )

    def optimize(
        self,
        skill_files: dict,
        scenarios: list,
        evals: list,
        max_rounds: int = 10,
        patience: int = 0,
        callback: Optional[Callable] = None,
    ) -> dict:
        """Run the optimization loop with 3 agent personas.
        
        Args:
            skill_files: Dict of filename -> content
            scenarios: List of test scenarios
            evals: List of evaluation criteria
            max_rounds: Max optimization rounds (1-30)
            patience: Stop after this many rounds with no improvement (0=disabled)
            callback: Optional function called with event dicts for SSE streaming
            
        Returns:
            Dict with baseline_score, final_score, improved_skill_content,
            score_history, mutation_log, dpo_pairs, sft_examples
        """
        def emit(event: dict):
            if callback:
                callback(event)

        # Find main skill content
        skill_content = ""
        for k, v in skill_files.items():
            if k.endswith("SKILL.md") or k.endswith(".md"):
                skill_content = v
                break
        if not skill_content and skill_files:
            skill_content = next(iter(skill_files.values()))

        current_content = skill_content
        score_history = []
        mutation_log = []
        dpo_pairs = []
        sft_examples = []
        rounds_without_improvement = 0

        # ── Baseline scoring ──
        emit({"type": "status", "data": {"message": "Running baseline evaluation..."}})
        baseline = self._score_skill(current_content, scenarios, evals)
        baseline_pct = round(100 * baseline["passed"] / max(baseline["total"], 1), 1)
        score_history.append(baseline_pct)

        emit({
            "type": "baseline",
            "data": {
                "score": baseline_pct,
                "passed": baseline["passed"],
                "total": baseline["total"],
                "per_eval": baseline["per_eval"],
            },
        })

        # Collect SFT examples from successful baseline traces
        for trace in baseline.get("traces", []):
            all_passed = all(r.get("passed", False) for r in trace.get("eval_results", []))
            if all_passed and trace.get("output", "").strip():
                sft_examples.append({
                    "instruction": current_content,
                    "input": trace.get("input", ""),
                    "output": trace["output"],
                })

        if baseline_pct >= 100.0:
            result_data = {
                "baseline_score": baseline_pct,
                "final_score": baseline_pct,
                "improved_skill_content": current_content,
                "score_history": score_history,
                "mutation_log": [],
                "dpo_pairs": dpo_pairs,
                "sft_examples": sft_examples,
                "strategy_stats": {},
                "message": "Skill already passes all evaluations!",
            }
            emit({"type": "complete", "data": result_data})
            return result_data

        # ── Optimization rounds ──
        for rnd in range(1, max_rounds + 1):
            emit({"type": "experiment_start", "data": {"round": rnd, "total_rounds": max_rounds}})

            # Check for stop request
            # (caller should set session["stop_requested"] = True)

            # Convergence detection: stop after `patience` rounds of no improvement
            if patience > 0 and rounds_without_improvement >= patience:
                emit({"type": "status", "data": {"message": f"Converged after {patience} rounds with no improvement."}})
                break

            # Analyst diagnoses failures (with OPRO score history + GEPA traces)
            emit({"type": "status", "data": {"message": f"Round {rnd}: Analyst diagnosing failures..."}})
            analysis = self._analyze_failures(
                current_content, scenarios, evals, baseline["details"],
                score_history=score_history,
                traces=baseline.get("traces"),
            )

            emit({
                "type": "analysis",
                "data": {
                    "round": rnd,
                    "diagnosis": analysis.get("diagnosis", ""),
                    "strategy": analysis.get("mutation_strategy", ""),
                    "target": analysis.get("target_section", ""),
                },
            })

            # Mutator applies fix
            emit({"type": "status", "data": {"message": f"Round {rnd}: Mutator applying fix..."}})
            mutation = self._mutate_skill(current_content, analysis)
            new_content = mutation.get("new_skill_content", current_content)

            # Skip if mutator returned empty or identical content
            if not new_content.strip() or new_content.strip() == current_content.strip():
                score_history.append(baseline_pct)
                entry = {
                    "round": rnd,
                    "strategy_type": analysis.get("mutation_strategy", "unknown"),
                    "diagnosis": analysis.get("diagnosis", ""),
                    "description": "No change produced",
                    "score_before": baseline_pct,
                    "score_after": baseline_pct,
                    "kept": False,
                }
                mutation_log.append(entry)
                emit({
                    "type": "experiment_result",
                    "data": {**entry, "status": "discarded", "per_eval": baseline["per_eval"]},
                })
                continue

            # Re-score with the mutation
            emit({"type": "status", "data": {"message": f"Round {rnd}: Re-evaluating..."}})
            result = self._score_skill(new_content, scenarios, evals)
            new_pct = round(100 * result["passed"] / max(result["total"], 1), 1)

            kept = new_pct > baseline_pct
            entry = {
                "round": rnd,
                "strategy_type": analysis.get("mutation_strategy", "unknown"),
                "diagnosis": analysis.get("diagnosis", ""),
                "description": mutation.get("description", ""),
                "score_before": baseline_pct,
                "score_after": new_pct,
                "kept": kept,
            }
            mutation_log.append(entry)

            # DPO pair: chosen=winner, rejected=loser, margin=score delta
            if kept:
                dpo_pairs.append({
                    "chosen": new_content,
                    "rejected": current_content,
                    "margin": round(new_pct - baseline_pct, 1),
                })
                current_content = new_content
                baseline = result
                baseline_pct = new_pct
                rounds_without_improvement = 0
            else:
                dpo_pairs.append({
                    "chosen": current_content,
                    "rejected": new_content,
                    "margin": round(baseline_pct - new_pct, 1),
                })
                rounds_without_improvement += 1

            # Collect SFT examples from successful traces in this round
            for trace in result.get("traces", []):
                all_passed = all(r.get("passed", False) for r in trace.get("eval_results", []))
                if all_passed and trace.get("output", "").strip():
                    sft_examples.append({
                        "instruction": new_content if kept else current_content,
                        "input": trace.get("input", ""),
                        "output": trace["output"],
                    })

            score_history.append(baseline_pct)

            emit({
                "type": "experiment_result",
                "data": {
                    **entry,
                    "status": "kept" if kept else "discarded",
                    "per_eval": result["per_eval"],
                },
            })

            # Early exit if perfect score
            if baseline_pct >= 100.0:
                break

        # ── Done ──
        final_pct = baseline_pct
        strategy_stats = _strategy_stats(mutation_log)

        result_data = {
            "baseline_score": score_history[0],
            "final_score": final_pct,
            "improved_skill_content": current_content,
            "score_history": score_history,
            "mutation_log": mutation_log,
            "strategy_stats": strategy_stats,
            "dpo_pairs": dpo_pairs,
            "sft_examples": sft_examples,
        }

        emit({"type": "complete", "data": result_data})

        return result_data


def _strategy_stats(mutation_log: list) -> dict:
    """Compute per-strategy success rates."""
    stats: dict[str, dict] = {}
    for m in mutation_log:
        s = m.get("strategy_type", "unknown")
        if s not in stats:
            stats[s] = {"total": 0, "kept": 0}
        stats[s]["total"] += 1
        if m.get("kept"):
            stats[s]["kept"] += 1
    return stats


# ── Optimizer factory ────────────────────────────────────────────────────────

def create_optimizer(model_path: str, n_ctx: int = 4096, gemini_api_key: str = "") -> SkillOptimizer:
    """Create the best available optimizer.
    
    If google-adk is installed AND a Gemini API key is provided, uses the ADK
    optimizer with Gemini models. Otherwise falls back to local GGUF optimizer.
    """
    if gemini_api_key:
        try:
            from adk_optimizer import ADKSkillOptimizer
            print("[skill_optimizer] Using Google ADK optimizer with Gemini", flush=True)  # xray: ignore[PY-004]
            return ADKSkillOptimizer(api_key=gemini_api_key)
        except ImportError:  # xray: ignore[QUAL-002]
            print("[skill_optimizer] google-adk not installed, falling back to local", flush=True)  # xray: ignore[PY-004]
        except Exception as e:
            print(f"[skill_optimizer] ADK init failed ({e}), falling back to local", flush=True)  # xray: ignore[PY-004]

    print(f"[skill_optimizer] Using local GGUF optimizer: {os.path.basename(model_path)}", flush=True)  # xray: ignore[PY-004]
    return SkillOptimizer(model_path=model_path, n_ctx=n_ctx)


# ── Training data export ─────────────────────────────────────────────────────

def export_training_data(session_id: str) -> dict:
    """Export collected DPO and SFT training data from a completed session.
    
    Returns: {"dpo_jsonl": str, "sft_jsonl": str}
    Raises ValueError if session not found or has no results.
    """
    session = get_skill_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    final = session.get("final_result") or {}
    dpo_pairs = final.get("dpo_pairs", [])
    sft_examples = final.get("sft_examples", [])

    dpo_lines = []
    for pair in dpo_pairs:
        dpo_lines.append(json.dumps({
            "chosen": pair.get("chosen", ""),
            "rejected": pair.get("rejected", ""),
            "margin": pair.get("margin", 0),
        }, ensure_ascii=False))

    sft_lines = []
    for ex in sft_examples:
        sft_lines.append(json.dumps({
            "instruction": ex.get("instruction", ""),
            "input": ex.get("input", ""),
            "output": ex.get("output", ""),
        }, ensure_ascii=False))

    return {
        "dpo_jsonl": "\n".join(dpo_lines),
        "sft_jsonl": "\n".join(sft_lines),
    }


# ── Example skills loader ───────────────────────────────────────────────────

def get_example_skills_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "example_skills")


def list_example_skills() -> list[dict]:
    """List bundled example skills."""
    examples_dir = get_example_skills_dir()
    examples = []
    if not os.path.isdir(examples_dir):
        return examples
    for name in sorted(os.listdir(examples_dir)):
        skill_dir = os.path.join(examples_dir, name)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if os.path.isdir(skill_dir) and os.path.isfile(skill_md):
            with open(skill_md, "r", encoding="utf-8") as f:
                content = f.read()
            metadata = parse_skill_frontmatter(content)
            examples.append({
                "name": metadata.get("name", name),
                "description": metadata.get("description", ""),
                "path": name,
            })
    return examples


def load_example_skill(example_name: str) -> tuple[dict, list]:
    """Load an example skill by name. Returns (skill_files, file_list).
    
    Raises ValueError on invalid/missing example.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", example_name):
        raise ValueError("Invalid example name")

    examples_dir = os.path.realpath(get_example_skills_dir())
    skill_dir = os.path.realpath(os.path.join(examples_dir, example_name))

    # Path traversal prevention
    if not skill_dir.startswith(examples_dir + os.sep):
        raise ValueError("Invalid example name")
    if not os.path.isdir(skill_dir):
        raise ValueError("Example skill not found")

    skill_files = {}
    file_list = []
    for root, _dirs, files in os.walk(skill_dir):
        for fname in files:
            if fname.startswith("."):
                continue
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, skill_dir)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    skill_files[rel_path] = f.read()
                file_list.append(rel_path)
            except (UnicodeDecodeError, OSError):
                continue

    return skill_files, file_list
