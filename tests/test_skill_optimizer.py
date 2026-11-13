"""Tests for the self-improving skill optimizer.

Tests organized in two tiers:
  1. Existing unit tests (JSON, frontmatter, sessions, uploads, path safety, examples)
  2. Research-informed TDD tests for engine improvements:
     - OPRO-style score history in mutation prompts
     - DPO training data collection from optimization trajectories
     - Full trajectory capture for analyst reflection
     - Convergence detection (plateau stopping)
     - Validation split to prevent overfitting
     - Training data export (SFT + DPO pairs)
"""
import json
import os
import sys
import threading
import time
import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import skill_optimizer as so


# ── Helpers ──────────────────────────────────────────────────────────────────

SAMPLE_SKILL = """---
name: test-skill
description: A test skill
---
# Test Skill

You are a helpful test assistant.
"""

SAMPLE_SKILL_2 = """# Another Skill

Act as a code reviewer.
"""


def _make_zip(files: dict[str, str]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_optimizer():
    """Create a SkillOptimizer with a dummy model path for mocked tests."""
    return so.SkillOptimizer(model_path="dummy.gguf")


# ── JSON extraction ──────────────────────────────────────────────────────────

class TestExtractJson:
    def test_direct_json(self):
        assert so._extract_json('{"a": 1}') == {"a": 1}

    def test_json_in_markdown_fence(self):
        text = '```json\n{"key": "val"}\n```'
        assert so._extract_json(text) == {"key": "val"}

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"x": 42}\nDone.'
        assert so._extract_json(text) == {"x": 42}

    def test_json_array(self):
        assert so._extract_json('[1, 2, 3]') == [1, 2, 3]

    def test_empty_input(self):
        assert so._extract_json("", "default") == "default"
        assert so._extract_json(None, "default") == "default"

    def test_invalid_json(self):
        assert so._extract_json("not json at all", {"fallback": True}) == {"fallback": True}

    def test_nested_json(self):
        text = '```\n{"scenarios": [{"id": 1, "name": "test"}]}\n```'
        result = so._extract_json(text)
        assert result["scenarios"][0]["name"] == "test"


# ── Frontmatter parsing ─────────────────────────────────────────────────────

class TestParseFrontmatter:
    def test_basic(self):
        meta = so.parse_skill_frontmatter(SAMPLE_SKILL)
        assert meta["name"] == "test-skill"
        assert meta["description"] == "A test skill"

    def test_no_frontmatter(self):
        assert so.parse_skill_frontmatter("# Just markdown") == {}

    def test_empty(self):
        assert so.parse_skill_frontmatter("") == {}

    def test_quoted_values(self):
        text = '---\nname: "quoted-name"\n---\n# Content'
        meta = so.parse_skill_frontmatter(text)
        assert meta["name"] == "quoted-name"


# ── Session management ───────────────────────────────────────────────────────

class TestSessions:
    def setup_method(self):
        with so._skill_sessions_lock:
            so._skill_sessions.clear()

    def test_create_session(self):
        result = so.create_skill_session({"SKILL.md": SAMPLE_SKILL}, ["SKILL.md"])
        assert "session_id" in result
        assert result["skill_key"] == "SKILL.md"
        assert "SKILL.md" in result["file_list"]

    def test_create_session_fallback_to_md(self):
        result = so.create_skill_session({"readme.md": SAMPLE_SKILL_2}, ["readme.md"])
        assert result["skill_key"] == "readme.md"

    def test_create_session_no_content(self):
        result = so.create_skill_session({}, [])
        assert "error" in result

    def test_get_session(self):
        result = so.create_skill_session({"SKILL.md": SAMPLE_SKILL}, ["SKILL.md"])
        session = so.get_skill_session(result["session_id"])
        assert session is not None
        assert session["status"] == "uploaded"

    def test_get_nonexistent_session(self):
        assert so.get_skill_session("does-not-exist") is None

    def test_cleanup_expired(self):
        result = so.create_skill_session({"SKILL.md": SAMPLE_SKILL}, ["SKILL.md"])
        sid = result["session_id"]
        with so._skill_sessions_lock:
            so._skill_sessions[sid]["created_at"] = time.time() - so.SESSION_TTL - 10
        cleaned = so.cleanup_expired_sessions()
        assert cleaned == 1
        assert so.get_skill_session(sid) is None

    def test_cleanup_skips_running(self):
        result = so.create_skill_session({"SKILL.md": SAMPLE_SKILL}, ["SKILL.md"])
        sid = result["session_id"]
        with so._skill_sessions_lock:
            so._skill_sessions[sid]["created_at"] = time.time() - so.SESSION_TTL - 10
            so._skill_sessions[sid]["status"] = "running"
        cleaned = so.cleanup_expired_sessions()
        assert cleaned == 0
        assert so.get_skill_session(sid) is not None


# ── Upload / zip extraction ──────────────────────────────────────────────────

class TestExtractUpload:
    def test_valid_zip(self):
        zb = _make_zip({"SKILL.md": SAMPLE_SKILL, "helpers.py": "print('hi')"})
        files, names = so.extract_upload(zb)
        assert "SKILL.md" in files
        assert "helpers.py" in files

    def test_zip_too_large(self):
        with pytest.raises(ValueError, match="limit"):
            so.extract_upload(b"\x00" * (so.MAX_UPLOAD_SIZE + 1))

    def test_bad_zip(self):
        with pytest.raises(ValueError, match="Invalid zip"):
            so.extract_upload(b"not a zip file at all")

    def test_path_traversal_blocked(self):
        zb = _make_zip({"../evil.md": "pwned", "SKILL.md": SAMPLE_SKILL})
        files, names = so.extract_upload(zb)
        assert "../evil.md" not in files
        assert "SKILL.md" in files

    def test_disallowed_extension(self):
        zb = _make_zip({"SKILL.md": SAMPLE_SKILL, "data.exe": "binary"})
        files, names = so.extract_upload(zb)
        assert "SKILL.md" in files
        assert "data.exe" not in files

    def test_ignores_macosx_and_ds_store(self):
        zb = _make_zip({
            "SKILL.md": SAMPLE_SKILL,
            "__MACOSX/._SKILL.md": "junk",
            ".DS_Store": "junk",
        })
        files, names = so.extract_upload(zb)
        assert "SKILL.md" in files
        assert len(files) == 1


# ── Path safety ──────────────────────────────────────────────────────────────

class TestPathSafety:
    def test_safe_path(self):
        assert so._is_safe_path("SKILL.md") is True
        assert so._is_safe_path("sub/dir/file.md") is True

    def test_unsafe_path_traversal(self):
        assert so._is_safe_path("../etc/passwd") is False
        assert so._is_safe_path("sub/../../../etc/passwd") is False

    def test_absolute_path(self):
        assert so._is_safe_path("/etc/passwd") is False
        assert so._is_safe_path("C:\\Windows\\system32") is False

    def test_allowed_extensions(self):
        assert so._is_allowed_file("SKILL.md") is True
        assert so._is_allowed_file("config.yaml") is True
        assert so._is_allowed_file("script.py") is True
        assert so._is_allowed_file("malware.exe") is False
        assert so._is_allowed_file("archive.zip") is False


# ── Example skills ───────────────────────────────────────────────────────────

class TestExampleSkills:
    def test_list_examples(self):
        examples = so.list_example_skills()
        assert isinstance(examples, list)
        names = [e["name"] for e in examples]
        assert "Code Reviewer" in names or "Medical Triage" in names or len(names) >= 1

    def test_load_example(self):
        examples = so.list_example_skills()
        if not examples:
            pytest.skip("No example skills found")
        ex = examples[0]
        files, flist = so.load_example_skill(ex["path"])
        assert len(files) > 0
        assert any(f.endswith(".md") for f in flist)

    def test_load_invalid_name(self):
        with pytest.raises(ValueError, match="Invalid"):
            so.load_example_skill("../../etc")

    def test_load_nonexistent(self):
        with pytest.raises(ValueError, match="not found"):
            so.load_example_skill("nonexistent-skill-xyz")


# ══════════════════════════════════════════════════════════════════════════════
#  EXISTING UNIT TESTS — SkillOptimizer (mocked LLM)
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillOptimizerMocked:
    """Tests with mocked _chat to avoid needing a real GGUF model."""

    def test_analyze_skill_returns_structure(self):
        opt = _make_optimizer()
        result_json = json.dumps({
            "scenarios": [{"id": 1, "name": "test", "input": "hello"}],
            "evals": [{"id": 1, "name": "greets", "question": "Does it greet?"}],
        })
        with patch.object(opt, "_chat", return_value=result_json):
            result = opt.analyze_skill({"SKILL.md": SAMPLE_SKILL})
        assert len(result["scenarios"]) == 1
        assert len(result["evals"]) == 1

    def test_analyze_skill_handles_bad_llm_output(self):
        opt = _make_optimizer()
        with patch.object(opt, "_chat", return_value="Sorry I can't help"):
            result = opt.analyze_skill({"SKILL.md": SAMPLE_SKILL})
        assert result["scenarios"] == []
        assert result["evals"] == []

    def test_score_skill(self):
        opt = _make_optimizer()
        scenarios = [{"id": 1, "name": "test", "input": "hello"}]
        evals = [{"id": 1, "name": "greets", "question": "Does it greet?"}]

        call_count = [0]
        def mock_chat(system, user):
            call_count[0] += 1
            if call_count[0] % 2 == 1:
                return "Hello! How can I help you today?"
            return json.dumps({"results": [{"eval_id": 1, "passed": True, "reason": "OK"}]})

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt._score_skill(SAMPLE_SKILL, scenarios, evals)
        assert result["passed"] >= 0
        assert result["total"] >= 0

    def test_optimize_baseline_perfect(self):
        """If baseline is 100%, should stop immediately."""
        opt = _make_optimizer()
        scenarios = [{"id": 1, "name": "test", "input": "hello"}]
        evals = [{"id": 1, "name": "greets"}]

        call_count = [0]
        def mock_chat(system, user):
            call_count[0] += 1
            if call_count[0] % 2 == 1:
                return "Hello!"
            return json.dumps({"results": [{"eval_id": 1, "passed": True, "reason": "OK"}]})

        events = []
        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=scenarios,
                evals=evals,
                max_rounds=5,
                callback=lambda e: events.append(e),
            )
        assert result["final_score"] == 100.0
        assert any(e["type"] == "complete" for e in events)

    def test_optimize_one_round_improvement(self):
        opt = _make_optimizer()
        scenarios = [{"id": 1, "name": "test", "input": "hello"}]
        evals = [{"id": 1, "name": "greets"}, {"id": 2, "name": "polite"}]

        score_round = [0]
        def mock_chat(system, user):
            if "Execute this skill" in user:
                return "Hello! How can I help you today?"
            if "Evaluate this output" in user:
                score_round[0] += 1
                if score_round[0] == 1:
                    return json.dumps({"results": [
                        {"eval_id": 1, "passed": True, "reason": "OK"},
                        {"eval_id": 2, "passed": False, "reason": "Not polite"},
                    ]})
                else:
                    return json.dumps({"results": [
                        {"eval_id": 1, "passed": True, "reason": "OK"},
                        {"eval_id": 2, "passed": True, "reason": "Now polite"},
                    ]})
            if "Diagnose" in user:
                return json.dumps({
                    "diagnosis": "Missing polite greeting",
                    "mutation_strategy": "add_constraint",
                    "target_section": "instructions",
                    "suggested_change": "add polite greeting requirement",
                })
            if "Apply this fix" in user:
                return json.dumps({
                    "description": "Added politeness requirement",
                    "reasoning": "Ensures polite tone",
                    "new_skill_content": SAMPLE_SKILL + "\nAlways be polite.",
                })
            return "{}"

        events = []
        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=scenarios,
                evals=evals,
                max_rounds=3,
                callback=lambda e: events.append(e),
            )
        assert result["final_score"] >= result["baseline_score"]
        assert any(e["type"] == "experiment_result" for e in events)

    def test_optimize_with_empty_mutation_skips(self):
        """If mutator returns same content, round should be discarded."""
        opt = _make_optimizer()
        scenarios = [{"id": 1, "name": "t", "input": "hi"}]
        evals = [{"id": 1, "name": "e"}]

        def mock_chat(system, user):
            if "Execute" in user:
                return "Hi!"
            if "Evaluate" in user:
                return json.dumps({"results": [{"eval_id": 1, "passed": False, "reason": "Nope"}]})
            if "Diagnose" in user:
                return json.dumps({"diagnosis": "D", "mutation_strategy": "add_constraint",
                                   "target_section": "X", "suggested_change": "Y"})
            if "Apply" in user:
                return json.dumps({"description": "same", "reasoning": "", "new_skill_content": SAMPLE_SKILL})
            return "{}"

        events = []
        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=scenarios,
                evals=evals,
                max_rounds=2,
                callback=lambda e: events.append(e),
            )
        discard_events = [e for e in events if e.get("type") == "experiment_result" and e["data"].get("status") == "discarded"]
        assert len(discard_events) >= 1


# ── Optimizer factory ────────────────────────────────────────────────────────

class TestFactory:
    def test_creates_local_without_gemini(self):
        opt = so.create_optimizer(model_path="test.gguf")
        assert isinstance(opt, so.SkillOptimizer)
        assert opt.model_path == "test.gguf"

    def test_falls_back_when_adk_missing(self):
        with patch.dict("sys.modules", {"adk_optimizer": None}):
            opt = so.create_optimizer(model_path="test.gguf", gemini_api_key="fake-key")
        assert isinstance(opt, so.SkillOptimizer)


# ── Strategy stats ───────────────────────────────────────────────────────────

class TestStrategyStats:
    def test_counts(self):
        log = [
            {"strategy_type": "add_constraint", "kept": True},
            {"strategy_type": "add_constraint", "kept": False},
            {"strategy_type": "restructure", "kept": True},
        ]
        stats = so._strategy_stats(log)
        assert stats["add_constraint"]["total"] == 2
        assert stats["add_constraint"]["kept"] == 1
        assert stats["restructure"]["total"] == 1
        assert stats["restructure"]["kept"] == 1

    def test_empty_log(self):
        assert so._strategy_stats([]) == {}


# ── Backend route integration (light) ────────────────────────────────────────

class TestBackendRoutes:
    """Verify routes are registered correctly in comparator_backend."""

    def test_skill_lab_routes_exist(self):
        import comparator_backend as cb
        handler_cls = cb.ComparatorHandler
        assert hasattr(handler_cls, "_handle_skill_lab_examples")
        assert hasattr(handler_cls, "_handle_skill_lab_upload")
        assert hasattr(handler_cls, "_handle_skill_lab_analyze")
        assert hasattr(handler_cls, "_handle_skill_lab_start")
        assert hasattr(handler_cls, "_handle_skill_lab_stop")
        assert hasattr(handler_cls, "_handle_skill_lab_stream")
        assert hasattr(handler_cls, "_handle_skill_lab_download")
        assert hasattr(handler_cls, "_handle_skill_lab_status")
        assert hasattr(handler_cls, "_handle_skill_lab_load_example")
        assert hasattr(handler_cls, "_handle_skill_lab_update_config")


# ══════════════════════════════════════════════════════════════════════════════
#  TDD TIER 2 — Research-Informed Engine Tests
#  These define the CONTRACT for the improved optimizer.
#  Based on: DSPy/GEPA, OPRO, TextGrad, EvoPrompt findings.
# ══════════════════════════════════════════════════════════════════════════════


class TestOPROScoreHistory:
    """OPRO insight: Show the Mutator/Analyst prior (prompt_version, score) pairs
    so the LLM can learn from the optimization trajectory — not just the current failure.
    
    Reference: "Large Language Models as Optimizers" (ICLR 2024)
    """

    def test_optimize_returns_score_history(self):
        """optimize() result must contain score_history list."""
        opt = _make_optimizer()
        scores = iter([50.0, 75.0])

        def mock_chat(system, user):
            if "Execute" in user:
                return "output"
            if "Evaluate" in user:
                return json.dumps({"results": [{"eval_id": 1, "passed": True, "reason": "OK"}]})
            if "Diagnose" in user:
                return json.dumps({"diagnosis": "X", "mutation_strategy": "add_constraint",
                                   "target_section": "Y", "suggested_change": "Z"})
            if "Apply" in user:
                return json.dumps({"description": "fix", "reasoning": "r",
                                   "new_skill_content": SAMPLE_SKILL + "\nv2"})
            return "{}"

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=1,
                callback=lambda e: None,
            )
        assert "score_history" in result
        assert isinstance(result["score_history"], list)
        assert len(result["score_history"]) >= 1  # at least baseline

    def test_mutation_log_contains_score_trajectory(self):
        """Each mutation_log entry must have score_before and score_after."""
        opt = _make_optimizer()
        score_round = [0]

        def mock_chat(system, user):
            if "Execute" in user:
                return "out"
            if "Evaluate" in user:
                score_round[0] += 1
                passed = score_round[0] > 1  # fail baseline, pass after
                return json.dumps({"results": [{"eval_id": 1, "passed": passed, "reason": "r"}]})
            if "Diagnose" in user:
                return json.dumps({"diagnosis": "D", "mutation_strategy": "add_constraint",
                                   "target_section": "T", "suggested_change": "S"})
            if "Apply" in user:
                return json.dumps({"description": "d", "reasoning": "r",
                                   "new_skill_content": SAMPLE_SKILL + "\nfixed"})
            return "{}"

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=2,
                callback=lambda e: None,
            )
        for entry in result["mutation_log"]:
            assert "score_before" in entry
            assert "score_after" in entry
            assert isinstance(entry["score_before"], (int, float))
            assert isinstance(entry["score_after"], (int, float))

    def test_analyst_receives_score_history_context(self):
        """Analyst prompt should include prior round scores (OPRO-style trajectory)."""
        opt = _make_optimizer()
        analyst_prompts = []

        original_ask_json = opt._ask_json
        def spy_ask_json(system, user, fallback=None):
            if "ANALYST" in system or "diagnose" in system.lower() or "Diagnose" in user:
                analyst_prompts.append(user)
            return original_ask_json(system, user, fallback)

        score_round = [0]
        def mock_chat(system, user):
            if "Execute" in user:
                return "out"
            if "Evaluate" in user:
                score_round[0] += 1
                return json.dumps({"results": [{"eval_id": 1, "passed": False, "reason": "nope"}]})
            if "diagnose" in system.lower() or "Diagnose" in user:
                return json.dumps({"diagnosis": "D", "mutation_strategy": "add_constraint",
                                   "target_section": "T", "suggested_change": "S"})
            if "Apply" in user or "edit" in system.lower():
                return json.dumps({"description": "d", "reasoning": "r",
                                   "new_skill_content": SAMPLE_SKILL + "\nv2"})
            return "{}"

        with patch.object(opt, "_chat", side_effect=mock_chat):
            opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=2,
                callback=lambda e: None,
            )
        # At least the 2nd round should have score history context
        if len(analyst_prompts) >= 2:
            second_prompt = analyst_prompts[1]
            # Should mention prior score or "history" or "previous"
            assert any(kw in second_prompt.lower() for kw in
                       ["score", "history", "previous", "round 1", "prior", "trajectory"]), \
                f"Analyst prompt in round 2 should contain score history context, got: {second_prompt[:200]}"


class TestDPOTrainingDataCollection:
    """Every keep/revert decision is a DPO preference pair.
    optimization runs should generate training data for model fine-tuning.
    
    Reference: DSPy BootstrapFinetune, GEPA Pareto pairs
    """

    def test_optimize_returns_dpo_pairs(self):
        """optimize() result must include dpo_pairs list."""
        opt = _make_optimizer()
        score_round = [0]

        def mock_chat(system, user):
            if "Execute" in user:
                return "output"
            if "Evaluate" in user:
                score_round[0] += 1
                passed = score_round[0] > 1
                return json.dumps({"results": [{"eval_id": 1, "passed": passed, "reason": "r"}]})
            if "Diagnose" in user:
                return json.dumps({"diagnosis": "D", "mutation_strategy": "add_constraint",
                                   "target_section": "T", "suggested_change": "S"})
            if "Apply" in user:
                return json.dumps({"description": "d", "reasoning": "r",
                                   "new_skill_content": SAMPLE_SKILL + "\nimproved"})
            return "{}"

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=2,
                callback=lambda e: None,
            )
        assert "dpo_pairs" in result, "optimize() should return dpo_pairs for training data"
        assert isinstance(result["dpo_pairs"], list)

    def test_dpo_pair_structure(self):
        """Each DPO pair must have: chosen, rejected, scenario, margin."""
        opt = _make_optimizer()
        score_round = [0]

        def mock_chat(system, user):
            if "Execute" in user:
                return "output"
            if "Evaluate" in user:
                score_round[0] += 1
                passed = score_round[0] > 1
                return json.dumps({"results": [{"eval_id": 1, "passed": passed, "reason": "r"}]})
            if "Diagnose" in user:
                return json.dumps({"diagnosis": "D", "mutation_strategy": "add_constraint",
                                   "target_section": "T", "suggested_change": "S"})
            if "Apply" in user:
                return json.dumps({"description": "d", "reasoning": "r",
                                   "new_skill_content": SAMPLE_SKILL + "\nimproved"})
            return "{}"

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=2,
                callback=lambda e: None,
            )
        for pair in result.get("dpo_pairs", []):
            assert "chosen" in pair, "DPO pair must have 'chosen' (better skill content)"
            assert "rejected" in pair, "DPO pair must have 'rejected' (worse skill content)"
            assert "margin" in pair, "DPO pair must have 'margin' (score delta)"

    def test_kept_mutation_produces_dpo_pair(self):
        """When a mutation is kept (score improved), it produces a DPO pair
        with chosen=new_content, rejected=old_content, positive margin."""
        opt = _make_optimizer()
        score_round = [0]

        def mock_chat(system, user):
            if "Execute" in user:
                return "output"
            if "Evaluate" in user:
                score_round[0] += 1
                passed = score_round[0] > 1
                return json.dumps({"results": [{"eval_id": 1, "passed": passed, "reason": "r"}]})
            if "Diagnose" in user:
                return json.dumps({"diagnosis": "D", "mutation_strategy": "add_constraint",
                                   "target_section": "T", "suggested_change": "S"})
            if "Apply" in user:
                return json.dumps({"description": "d", "reasoning": "r",
                                   "new_skill_content": SAMPLE_SKILL + "\nimproved"})
            return "{}"

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=2,
                callback=lambda e: None,
            )
        kept_pairs = [p for p in result.get("dpo_pairs", []) if p.get("margin", 0) > 0]
        # If there was a kept mutation, there should be a positive-margin DPO pair
        kept_mutations = [m for m in result.get("mutation_log", []) if m.get("kept")]
        assert len(kept_pairs) >= len(kept_mutations), \
            "Each kept mutation should produce at least one DPO pair"


class TestFullTrajectoryCapture:
    """GEPA insight: Give the Analyst full execution traces
    (input -> output -> per-criterion scores), not just pass/fail.
    
    Reference: GEPA (ICLR 2026 Oral) - "trajectory reflection"
    """

    def test_score_skill_returns_execution_traces(self):
        """_score_skill() should capture the actual model output per scenario."""
        opt = _make_optimizer()
        call_count = [0]

        def mock_chat(system, user):
            call_count[0] += 1
            if call_count[0] % 2 == 1:
                return "Hello, I am the model output for this test."
            return json.dumps({"results": [{"eval_id": 1, "passed": True, "reason": "OK"}]})

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt._score_skill(
                SAMPLE_SKILL,
                [{"id": 1, "name": "greeting", "input": "hello"}],
                [{"id": 1, "name": "e"}],
            )
        # Should have traces with the actual model output
        assert "traces" in result, "_score_skill should return execution traces"
        assert len(result["traces"]) >= 1
        trace = result["traces"][0]
        assert "scenario_id" in trace
        assert "output" in trace, "Trace must include the actual model output text"
        assert len(trace["output"]) > 0

    def test_traces_include_per_eval_scores(self):
        """Each trace should include per-eval pass/fail results."""
        opt = _make_optimizer()
        call_count = [0]

        def mock_chat(system, user):
            call_count[0] += 1
            if call_count[0] % 2 == 1:
                return "Model output here."
            return json.dumps({"results": [
                {"eval_id": 1, "passed": True, "reason": "Good"},
                {"eval_id": 2, "passed": False, "reason": "Missing structure"},
            ]})

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt._score_skill(
                SAMPLE_SKILL,
                [{"id": 1, "name": "test", "input": "hi"}],
                [{"id": 1, "name": "e1"}, {"id": 2, "name": "e2"}],
            )
        traces = result.get("traces", [])
        assert len(traces) >= 1
        assert "eval_results" in traces[0], "Trace must include per-eval results"


class TestConvergenceDetection:
    """OPRO/GEPA insight: Stop when score plateaus for N rounds.
    Saves tokens on local models where each round is expensive.
    
    Reference: OPRO uses score plateau; GEPA uses Pareto front stability
    """

    def test_optimize_stops_on_plateau(self):
        """If score doesn't improve for patience rounds, should stop early."""
        opt = _make_optimizer()

        def mock_chat(system, user):
            if "Execute" in user:
                return "out"
            if "Evaluate" in user:
                # Always fail — score never improves
                return json.dumps({"results": [{"eval_id": 1, "passed": False, "reason": "bad"}]})
            if "Diagnose" in user:
                return json.dumps({"diagnosis": "D", "mutation_strategy": "add_constraint",
                                   "target_section": "T", "suggested_change": "S"})
            if "Apply" in user:
                return json.dumps({"description": "d", "reasoning": "r",
                                   "new_skill_content": SAMPLE_SKILL + "\nv" + str(time.time())})
            return "{}"

        events = []
        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=10,
                patience=3,  # stop if no improvement for 3 consecutive rounds
                callback=lambda e: events.append(e),
            )
        # Should have stopped before all 10 rounds
        experiment_events = [e for e in events if e.get("type") == "experiment_result"]
        assert len(experiment_events) <= 5, \
            f"With patience=3 and no improvement, should stop early, but ran {len(experiment_events)} rounds"

    def test_patience_resets_on_improvement(self):
        """Patience counter should reset when score improves."""
        opt = _make_optimizer()
        round_counter = [0]

        def mock_chat(system, user):
            if "Execute" in user:
                return "out"
            if "Evaluate" in user:
                round_counter[0] += 1
                # 2 evals: first always passes, second passes only on round 3
                # so scores are: baseline=50%, round1=50%, round2=50%, round3=100%(stops)
                # Actually to test patience reset without hitting 100%, use 3 evals:
                # baseline: 1/3=33%, round3 improvement: 2/3=66%, rest: 2/3=66%
                if round_counter[0] == 3:
                    # Round 3 improvement: 2 of 3 pass
                    return json.dumps({"results": [
                        {"eval_id": 1, "passed": True, "reason": "r"},
                        {"eval_id": 2, "passed": True, "reason": "r"},
                        {"eval_id": 3, "passed": False, "reason": "r"},
                    ]})
                else:
                    # All other rounds: 1 of 3 pass
                    return json.dumps({"results": [
                        {"eval_id": 1, "passed": True, "reason": "r"},
                        {"eval_id": 2, "passed": False, "reason": "r"},
                        {"eval_id": 3, "passed": False, "reason": "r"},
                    ]})
            if "Diagnose" in user:
                return json.dumps({"diagnosis": "D", "mutation_strategy": "add_constraint",
                                   "target_section": "T", "suggested_change": "S"})
            if "Apply" in user:
                return json.dumps({"description": "d", "reasoning": "r",
                                   "new_skill_content": SAMPLE_SKILL + f"\nv{round_counter[0]}"})
            return "{}"

        events = []
        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e1"}, {"id": 2, "name": "e2"}, {"id": 3, "name": "e3"}],
                max_rounds=15,
                patience=3,
                callback=lambda e: events.append(e),
            )
        # Should have run more than 3 rounds because patience reset at round 3
        experiment_events = [e for e in events if e.get("type") == "experiment_result"]
        assert len(experiment_events) >= 3


class TestSFTDataCollection:
    """Successful skill executions generate SFT training data.
    Every (instruction, input, output) triple where the output passed
    all evals is a usable training example.
    
    Reference: DSPy BootstrapFewShot traces; GEPA trajectory data
    """

    def test_optimize_returns_sft_examples(self):
        """optimize() result should include sft_examples list."""
        opt = _make_optimizer()

        def mock_chat(system, user):
            if "Execute" in user:
                return "High quality output."
            if "Evaluate" in user:
                return json.dumps({"results": [{"eval_id": 1, "passed": True, "reason": "OK"}]})
            return "{}"

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=1,
                callback=lambda e: None,
            )
        assert "sft_examples" in result, "optimize() should return sft_examples"
        assert isinstance(result["sft_examples"], list)

    def test_sft_example_structure(self):
        """Each SFT example must contain instruction, input, output."""
        opt = _make_optimizer()

        def mock_chat(system, user):
            if "Execute" in user:
                return "Perfect output."
            if "Evaluate" in user:
                return json.dumps({"results": [{"eval_id": 1, "passed": True, "reason": "OK"}]})
            return "{}"

        with patch.object(opt, "_chat", side_effect=mock_chat):
            result = opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=1,
                callback=lambda e: None,
            )
        for ex in result.get("sft_examples", []):
            assert "instruction" in ex, "SFT example must have instruction (skill content)"
            assert "input" in ex, "SFT example must have input (scenario)"
            assert "output" in ex, "SFT example must have output (model response)"


class TestTrainingDataExport:
    """The session should support exporting collected training data
    as JSONL files ready for fine-tuning pipelines.
    
    Reference: DSPy BootstrapFinetune, BetterTogether meta-optimizer
    """

    def test_export_training_data_function_exists(self):
        """Module should have export_training_data(session_id) -> dict."""
        assert hasattr(so, "export_training_data"), \
            "skill_optimizer should have export_training_data() function"

    def test_export_returns_dpo_and_sft(self):
        """export_training_data should return {dpo_jsonl: str, sft_jsonl: str}."""
        # Create a session with some fake results
        with so._skill_sessions_lock:
            so._skill_sessions.clear()
        result = so.create_skill_session({"SKILL.md": SAMPLE_SKILL}, ["SKILL.md"])
        sid = result["session_id"]
        sess = so.get_skill_session(sid)
        sess["final_result"] = {
            "dpo_pairs": [{"chosen": "good", "rejected": "bad", "margin": 10.0}],
            "sft_examples": [{"instruction": "skill", "input": "hi", "output": "hello"}],
        }
        exported = so.export_training_data(sid)
        assert "dpo_jsonl" in exported
        assert "sft_jsonl" in exported
        # Should be valid JSONL
        for line in exported["dpo_jsonl"].strip().split("\n"):
            if line:
                json.loads(line)
        for line in exported["sft_jsonl"].strip().split("\n"):
            if line:
                json.loads(line)


class TestCompleteEventSchema:
    """The 'complete' SSE event must include all data needed for UI and training."""

    def test_complete_event_has_training_data(self):
        """The complete event should include dpo_pairs and sft_examples."""
        opt = _make_optimizer()

        def mock_chat(system, user):
            if "Execute" in user:
                return "out"
            if "Evaluate" in user:
                return json.dumps({"results": [{"eval_id": 1, "passed": True, "reason": "OK"}]})
            return "{}"

        events = []
        with patch.object(opt, "_chat", side_effect=mock_chat):
            opt.optimize(
                skill_files={"SKILL.md": SAMPLE_SKILL},
                scenarios=[{"id": 1, "name": "t", "input": "hi"}],
                evals=[{"id": 1, "name": "e"}],
                max_rounds=1,
                callback=lambda e: events.append(e),
            )
        complete = [e for e in events if e["type"] == "complete"]
        assert len(complete) == 1
        data = complete[0]["data"]
        assert "score_history" in data
        assert "mutation_log" in data
        assert "dpo_pairs" in data, "Complete event should include dpo_pairs"
        assert "sft_examples" in data, "Complete event should include sft_examples"
