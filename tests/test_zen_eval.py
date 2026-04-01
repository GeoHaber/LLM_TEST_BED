"""
Comprehensive tests for zen_eval module
========================================

Tests all five features:
  1. Multi-turn conversation judges (UserFrustration, KnowledgeRetention)
  2. Judge alignment via human feedback
  3. Prompt versioning with aliases
  4. ToolCall evaluators (correctness & efficiency)
  5. Local model gateway (routing strategies, analytics)

Run:  pytest tests/test_zen_eval.py -v
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import zen_eval


# ═══════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Each test gets a fresh SQLite database."""
    db_path = str(tmp_path / "test_zeneval.db")
    zen_eval.init_db(db_path)
    yield db_path


@pytest.fixture
def gateway(fresh_db):
    """Fresh gateway instance pointing to the test DB."""
    gw = zen_eval.LocalModelGateway()
    return gw


# ═══════════════════════════════════════════════════════════════════════════════
#  1. Multi-Turn Judges — UserFrustration
# ═══════════════════════════════════════════════════════════════════════════════


class TestUserFrustrationJudge:
    """Tests for the UserFrustration conversation judge."""

    def _make_ctx(self, turns: list[tuple[str, str]], model: str = "test-model"):
        """Helper: build ConversationContext from (role, content) pairs."""
        return zen_eval.ConversationContext(
            conversation_id="test-conv-1",
            model_name=model,
            turns=[
                zen_eval.TurnData(role=r, content=c, turn_num=i)
                for i, (r, c) in enumerate(turns)
            ],
        )

    def test_no_frustration(self):
        """Normal polite conversation should pass with low score."""
        ctx = self._make_ctx([
            ("user", "What is the capital of France?"),
            ("assistant", "The capital of France is Paris."),
            ("user", "Thanks! And what about Germany?"),
            ("assistant", "The capital of Germany is Berlin."),
        ])
        result = zen_eval.judge_user_frustration(ctx)
        assert result.passed is True
        assert result.score <= 0.1
        assert result.judge_name == "UserFrustration"

    def test_high_frustration(self):
        """Explicit frustration indicators should trigger high score."""
        ctx = self._make_ctx([
            ("user", "What is 2+2?"),
            ("assistant", "Let me think about that complex question."),
            ("user", "This is terrible, you are not helping at all!"),
            ("assistant", "I apologize. 2+2 = 4."),
            ("user", "For the last time, I already told you the answer is obvious!"),
            ("assistant", "You're right, I should have answered immediately."),
        ])
        result = zen_eval.judge_user_frustration(ctx)
        assert result.passed is False
        assert result.score > 0.3
        assert len(result.details["matches"]) > 0

    def test_escalating_frustration(self):
        """Frustration that intensifies should be detected as escalating."""
        ctx = self._make_ctx([
            ("user", "Can you help me with this?"),
            ("assistant", "Sure, what do you need?"),
            ("user", "I don't think you understand what I said."),
            ("assistant", "Let me try again."),
            ("user", "That's not what I asked! Please try again."),
            ("assistant", "Hmm, let me reconsider."),
            ("user", "This is terrible! You are never helpful! What is wrong with you?"),
            ("assistant", "I sincerely apologize."),
        ])
        result = zen_eval.judge_user_frustration(ctx)
        assert result.score > 0.3
        assert result.details["escalating"] is True

    def test_resolved_frustration(self):
        """Frustration that resolves in the last turn."""
        ctx = self._make_ctx([
            ("user", "What is Python?"),
            ("assistant", "Python is a snake."),
            ("user", "That's not what I meant! I meant the programming language!"),
            ("assistant", "Python is a high-level programming language created by Guido van Rossum."),
            ("user", "Great, that's exactly what I needed. Thank you!"),
            ("assistant", "Glad I could help!"),
        ])
        result = zen_eval.judge_user_frustration(ctx)
        assert result.details["resolved"] is True

    def test_empty_conversation(self):
        """Edge case: no user turns."""
        ctx = self._make_ctx([
            ("assistant", "Hello! How can I help you?"),
        ])
        result = zen_eval.judge_user_frustration(ctx)
        assert result.passed is True
        assert result.score == 0.0

    def test_medium_frustration_indicators(self):
        """Medium-severity patterns should contribute but less than high."""
        ctx = self._make_ctx([
            ("user", "What color is the sky?"),
            ("assistant", "The sky can be many colors."),
            ("user", "No, that is wrong. It is blue on a clear day."),
            ("assistant", "You're correct!"),
        ])
        result = zen_eval.judge_user_frustration(ctx)
        # Should detect medium indicators but not fail hard
        assert result.score > 0.0
        assert result.score <= 0.5


# ═══════════════════════════════════════════════════════════════════════════════
#  1b. Multi-Turn Judges — KnowledgeRetention
# ═══════════════════════════════════════════════════════════════════════════════


class TestKnowledgeRetentionJudge:
    """Tests for the KnowledgeRetention conversation judge."""

    def _make_ctx(self, turns: list[tuple[str, str]], model: str = "test-model"):
        return zen_eval.ConversationContext(
            conversation_id="test-conv-kr",
            model_name=model,
            turns=[
                zen_eval.TurnData(role=r, content=c, turn_num=i)
                for i, (r, c) in enumerate(turns)
            ],
        )

    def test_good_retention(self):
        """Assistant correctly references user-provided facts."""
        ctx = self._make_ctx([
            ("user", "My name is Alice and I work at Google as a software engineer."),
            ("assistant", "Nice to meet you, Alice! How's work at Google?"),
            ("user", "I'm working on a machine learning project with TensorFlow."),
            ("assistant", "That sounds exciting! TensorFlow is great for ML. How long have you been a software engineer at Google, Alice?"),
            ("user", "About 5 years now."),
            ("assistant", "Five years of software engineering at Google must have given you deep expertise in TensorFlow and machine learning."),
        ])
        result = zen_eval.judge_knowledge_retention(ctx)
        assert result.passed is True
        assert result.score >= 0.4

    def test_poor_retention(self):
        """Assistant ignores user-provided context."""
        ctx = self._make_ctx([
            ("user", "I live in Tokyo and I speak Japanese, English and Mandarin fluently."),
            ("assistant", "Interesting! Tell me more."),
            ("user", "I work as a translator specializing in medical documents."),
            ("assistant", "What language do you speak? Where do you live?"),
            ("user", "I already told you that!"),
            ("assistant", "Sorry, can you repeat your location and profession?"),
        ])
        result = zen_eval.judge_knowledge_retention(ctx)
        assert result.score < 0.8

    def test_too_few_turns(self):
        """Conversations with < 4 turns should pass with 1.0."""
        ctx = self._make_ctx([
            ("user", "Hello"),
            ("assistant", "Hi there!"),
        ])
        result = zen_eval.judge_knowledge_retention(ctx)
        assert result.passed is True
        assert result.score == 1.0

    def test_no_substantial_facts(self):
        """Conversation where user provides no trackable facts."""
        ctx = self._make_ctx([
            ("user", "Hi"),
            ("assistant", "Hello!"),
            ("user", "Ok"),
            ("assistant", "How can I help?"),
            ("user", "Thanks"),
            ("assistant", "You're welcome!"),
        ])
        result = zen_eval.judge_knowledge_retention(ctx)
        assert result.passed is True


# ═══════════════════════════════════════════════════════════════════════════════
#  1c. Conversation Persistence
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationPersistence:
    """Tests for saving/loading conversations."""

    def test_save_and_load(self):
        ctx = zen_eval.ConversationContext(
            conversation_id="persist-test-1",
            model_name="llama-3",
            turns=[
                zen_eval.TurnData(role="user", content="Hello", turn_num=0),
                zen_eval.TurnData(role="assistant", content="Hi!", turn_num=1),
            ],
            metadata={"source": "test"},
        )
        zen_eval.save_conversation(ctx)
        loaded = zen_eval.load_conversation("persist-test-1")
        assert loaded is not None
        assert loaded.model_name == "llama-3"
        assert len(loaded.turns) == 2
        assert loaded.turns[0].content == "Hello"
        assert loaded.turns[1].role == "assistant"

    def test_load_nonexistent(self):
        assert zen_eval.load_conversation("does-not-exist") is None


# ═══════════════════════════════════════════════════════════════════════════════
#  2. Judge Alignment via Human Feedback
# ═══════════════════════════════════════════════════════════════════════════════


class TestJudgeAlignment:
    """Tests for the feedback-based judge calibration system."""

    def test_record_and_retrieve_feedback(self):
        fid = zen_eval.record_feedback(
            judge_name="medical",
            prompt="What is hypertension?",
            response="High blood pressure.",
            auto_score=7.5,
            human_score=8.0,
            feedback="Good but could mention causes",
        )
        assert fid > 0
        history = zen_eval.get_feedback_history("medical")
        assert len(history) == 1
        assert history[0]["auto_score"] == 7.5
        assert history[0]["human_score"] == 8.0

    def test_update_human_score(self):
        fid = zen_eval.record_feedback(
            judge_name="coding",
            prompt="Write hello world",
            response="print('hello world')",
            auto_score=9.0,
        )
        assert zen_eval.update_human_score(fid, 8.5, "Minor style issues")
        history = zen_eval.get_feedback_history("coding")
        assert history[0]["human_score"] == 8.5

    def test_update_nonexistent(self):
        assert zen_eval.update_human_score(99999, 5.0) is False

    def test_alignment_stats_no_data(self):
        stats = zen_eval.get_alignment_stats("nonexistent_judge")
        assert stats["total_feedback"] == 0
        assert stats["alignment_rate"] == 0.0

    def test_alignment_stats_with_data(self):
        # Judge consistently overscores by ~1.0
        for i in range(10):
            zen_eval.record_feedback(
                judge_name="test_judge",
                prompt=f"prompt_{i}",
                response=f"response_{i}",
                auto_score=7.0 + (i % 3) * 0.5,
                human_score=6.0 + (i % 3) * 0.5,  # always 1.0 lower
            )
        stats = zen_eval.get_alignment_stats("test_judge")
        assert stats["total_feedback"] == 10
        assert stats["avg_bias"] > 0.8  # overscoring
        assert stats["calibration_offset"] < -0.8  # correction is negative

    def test_calibrate_score(self):
        # Set up bias data: auto=8, human=6 → bias=+2, offset=-2
        for _ in range(5):
            zen_eval.record_feedback(
                judge_name="biased_judge",
                prompt="p", response="r",
                auto_score=8.0, human_score=6.0,
            )
        calibrated = zen_eval.calibrate_score("biased_judge", 8.0)
        assert calibrated < 8.0
        assert calibrated >= 5.0  # Should be around 6.0

    def test_calibrate_clamped(self):
        """Calibrated score should never go below 0 or above scale."""
        for _ in range(5):
            zen_eval.record_feedback(
                judge_name="extreme_judge",
                prompt="p", response="r",
                auto_score=2.0, human_score=9.0,
            )
        # With huge negative bias offset, calibrating low score should clamp to 0
        calibrated = zen_eval.calibrate_score("extreme_judge", 1.0)
        assert calibrated >= 0.0

    def test_correlation_computed(self):
        """With enough data points, Pearson correlation should be computed."""
        for i in range(20):
            auto = 5.0 + i * 0.25
            human = 5.0 + i * 0.25 + (0.1 if i % 2 == 0 else -0.1)
            zen_eval.record_feedback(
                judge_name="corr_judge",
                prompt=f"p{i}", response=f"r{i}",
                auto_score=auto, human_score=human,
            )
        stats = zen_eval.get_alignment_stats("corr_judge")
        assert stats["correlation"] > 0.9  # Strong positive correlation


# ═══════════════════════════════════════════════════════════════════════════════
#  3. Prompt Versioning with Aliases
# ═══════════════════════════════════════════════════════════════════════════════


class TestPromptVersioning:
    """Tests for the prompt versioning and alias system."""

    def test_register_first_version(self):
        p = zen_eval.register_prompt(
            name="medical_triage",
            template="You are a triage nurse. Assess: {{patient_complaint}}",
            system_prompt="Be concise and accurate.",
            temperature=0.3,
            max_tokens=256,
            commit_msg="Initial triage prompt",
        )
        assert p.name == "medical_triage"
        assert p.version == 1
        assert "{{patient_complaint}}" in p.template

    def test_register_increments_version(self):
        zen_eval.register_prompt(name="test_prompt", template="v1 content")
        p2 = zen_eval.register_prompt(name="test_prompt", template="v2 content")
        p3 = zen_eval.register_prompt(name="test_prompt", template="v3 content")
        assert p2.version == 2
        assert p3.version == 3

    def test_load_latest(self):
        zen_eval.register_prompt(name="evolving", template="first")
        zen_eval.register_prompt(name="evolving", template="second")
        zen_eval.register_prompt(name="evolving", template="third")
        p = zen_eval.load_prompt("evolving")
        assert p is not None
        assert p.version == 3
        assert p.template == "third"

    def test_load_specific_version(self):
        zen_eval.register_prompt(name="pinned", template="alpha")
        zen_eval.register_prompt(name="pinned", template="beta")
        p = zen_eval.load_prompt("pinned", version=1)
        assert p is not None
        assert p.template == "alpha"

    def test_load_nonexistent(self):
        assert zen_eval.load_prompt("ghost_prompt") is None
        assert zen_eval.load_prompt("ghost_prompt", version=99) is None

    def test_set_and_resolve_alias(self):
        zen_eval.register_prompt(name="prod_prompt", template="v1")
        zen_eval.register_prompt(name="prod_prompt", template="v2")
        zen_eval.register_prompt(name="prod_prompt", template="v3")

        assert zen_eval.set_alias("prod_prompt", "production", 2) is True
        assert zen_eval.set_alias("prod_prompt", "latest", 3) is True

        p = zen_eval.load_prompt("prod_prompt", alias="production")
        assert p is not None
        assert p.version == 2
        assert p.template == "v2"

    def test_alias_overwrite(self):
        """Reassigning an alias should update it."""
        zen_eval.register_prompt(name="alias_test", template="v1")
        zen_eval.register_prompt(name="alias_test", template="v2")
        zen_eval.set_alias("alias_test", "stable", 1)
        zen_eval.set_alias("alias_test", "stable", 2)  # overwrite
        p = zen_eval.load_prompt("alias_test", alias="stable")
        assert p is not None
        assert p.version == 2

    def test_alias_invalid_version(self):
        zen_eval.register_prompt(name="no_v99", template="content")
        assert zen_eval.set_alias("no_v99", "bad", 99) is False

    def test_delete_alias(self):
        zen_eval.register_prompt(name="del_alias", template="content")
        zen_eval.set_alias("del_alias", "temp", 1)
        assert zen_eval.delete_alias("del_alias", "temp") is True
        assert zen_eval.load_prompt("del_alias", alias="temp") is None

    def test_list_prompts_summary(self):
        zen_eval.register_prompt(name="prompt_a", template="a1")
        zen_eval.register_prompt(name="prompt_a", template="a2")
        zen_eval.register_prompt(name="prompt_b", template="b1")
        all_prompts = zen_eval.list_prompts()
        assert len(all_prompts) == 2
        names = {p["name"] for p in all_prompts}
        assert names == {"prompt_a", "prompt_b"}

    def test_list_prompt_versions(self):
        zen_eval.register_prompt(name="versions", template="v1", commit_msg="first")
        zen_eval.register_prompt(name="versions", template="v2", commit_msg="second")
        versions = zen_eval.list_prompts("versions")
        assert len(versions) == 2
        assert versions[0]["version"] == 2  # newest first

    def test_list_aliases(self):
        zen_eval.register_prompt(name="with_aliases", template="v1")
        zen_eval.register_prompt(name="with_aliases", template="v2")
        zen_eval.set_alias("with_aliases", "production", 1)
        zen_eval.set_alias("with_aliases", "canary", 2)
        aliases = zen_eval.list_aliases("with_aliases")
        assert len(aliases) == 2
        alias_names = {a["alias"] for a in aliases}
        assert alias_names == {"production", "canary"}

    def test_prompt_immutability(self):
        """Registering same name creates new version, not update."""
        zen_eval.register_prompt(name="immut", template="original")
        zen_eval.register_prompt(name="immut", template="modified")
        p1 = zen_eval.load_prompt("immut", version=1)
        p2 = zen_eval.load_prompt("immut", version=2)
        assert p1.template == "original"
        assert p2.template == "modified"


# ═══════════════════════════════════════════════════════════════════════════════
#  4. ToolCall Evaluators
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolCallCorrectness:
    """Tests for the ToolCallCorrectness judge."""

    def test_perfect_match(self):
        actual = [
            zen_eval.ToolCall(name="get_weather", arguments={"city": "Paris"}),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="get_weather", arguments={"city": "Paris"}),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, expected)
        assert result.passed is True
        assert result.score >= 0.9

    def test_wrong_function_called(self):
        actual = [
            zen_eval.ToolCall(name="get_time", arguments={"timezone": "UTC"}),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="get_weather", arguments={"city": "Paris"}),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, expected)
        assert result.passed is False
        assert result.score < 0.5
        assert "get_weather" in result.details["missing_required"]

    def test_partial_argument_match(self):
        actual = [
            zen_eval.ToolCall(name="search", arguments={"query": "python", "limit": 5}),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="search", arguments={"query": "python", "limit": 10}),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, expected)
        assert result.score > 0.0  # Partial match for correct function

    def test_no_expectations(self):
        actual = [
            zen_eval.ToolCall(name="random_func", arguments={}),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, [])
        assert result.passed is True
        assert result.score == 0.7  # No expectations, calls made

    def test_no_expectations_no_calls(self):
        result = zen_eval.judge_tool_call_correctness([], [])
        assert result.passed is True
        assert result.score == 1.0

    def test_missing_required_call(self):
        actual = [
            zen_eval.ToolCall(name="step_1", arguments={}),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="step_1", arguments=None),
            zen_eval.ToolCallExpectation(name="step_2", arguments=None, required=True),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, expected)
        assert "step_2" in result.details["missing_required"]

    def test_optional_not_penalized(self):
        actual = [
            zen_eval.ToolCall(name="required_func", arguments={}),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="required_func", arguments=None, required=True),
            zen_eval.ToolCallExpectation(name="optional_func", arguments=None, required=False),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, expected)
        assert result.passed is True

    def test_unexpected_calls_penalized(self):
        actual = [
            zen_eval.ToolCall(name="expected_func", arguments={}),
            zen_eval.ToolCall(name="rogue_func_1", arguments={}),
            zen_eval.ToolCall(name="rogue_func_2", arguments={}),
            zen_eval.ToolCall(name="rogue_func_3", arguments={}),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="expected_func", arguments=None),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, expected)
        # Should be penalized for unexpected calls
        assert result.score < 1.0

    def test_order_check(self):
        actual = [
            zen_eval.ToolCall(name="step_b", arguments={}),
            zen_eval.ToolCall(name="step_a", arguments={}),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="step_a", arguments=None, order=0),
            zen_eval.ToolCallExpectation(name="step_b", arguments=None, order=1),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, expected)
        assert result.details["order_correct"] is False

    def test_case_insensitive_matching(self):
        actual = [
            zen_eval.ToolCall(name="GetWeather", arguments={"City": "Paris"}),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="getweather", arguments={"City": "paris"}),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, expected)
        assert result.score >= 0.8

    def test_numeric_tolerance(self):
        actual = [
            zen_eval.ToolCall(name="set_temp", arguments={"value": 72.01}),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="set_temp", arguments={"value": 72.0}),
        ]
        result = zen_eval.judge_tool_call_correctness(actual, expected)
        assert result.score >= 0.9


class TestToolCallEfficiency:
    """Tests for the ToolCallEfficiency judge."""

    def test_efficient_calls(self):
        actual = [
            zen_eval.ToolCall(name="search", arguments={"q": "hello"}, result="found"),
            zen_eval.ToolCall(name="format", arguments={"data": "x"}, result="formatted"),
        ]
        result = zen_eval.judge_tool_call_efficiency(actual)
        assert result.passed is True
        assert result.score >= 0.9

    def test_duplicate_calls(self):
        actual = [
            zen_eval.ToolCall(name="search", arguments={"q": "hello"}, result="found"),
            zen_eval.ToolCall(name="search", arguments={"q": "hello"}, result="found"),
        ]
        result = zen_eval.judge_tool_call_efficiency(actual)
        assert result.score < 1.0
        assert "search" in result.details["duplicates"]

    def test_wasted_calls(self):
        actual = [
            zen_eval.ToolCall(name="lookup", arguments={"id": 1}, result=None),
            zen_eval.ToolCall(name="lookup", arguments={"id": 2}, result=None),
        ]
        result = zen_eval.judge_tool_call_efficiency(actual)
        assert result.details["wasted_calls"] == 2

    def test_out_of_bounds(self):
        actual = [
            zen_eval.ToolCall(name="a", arguments={}, result="ok"),
            zen_eval.ToolCall(name="b", arguments={}, result="ok"),
            zen_eval.ToolCall(name="c", arguments={}, result="ok"),
        ]
        result = zen_eval.judge_tool_call_efficiency(actual, min_expected=1, max_expected=2)
        assert result.details["bounds_ok"] is False


# ═══════════════════════════════════════════════════════════════════════════════
#  5. Local Model Gateway
# ═══════════════════════════════════════════════════════════════════════════════


class TestLocalModelGateway:
    """Tests for the Local Model Gateway routing system."""

    def test_round_robin(self, gateway):
        route = zen_eval.GatewayRoute(
            name="rr_test",
            strategy="round_robin",
            models=["model_a.gguf", "model_b.gguf", "model_c.gguf"],
        )
        gateway.add_route(route)

        results = [gateway.resolve("rr_test") for _ in range(6)]
        assert results == [
            "model_a.gguf", "model_b.gguf", "model_c.gguf",
            "model_a.gguf", "model_b.gguf", "model_c.gguf",
        ]

    def test_fallback_returns_first(self, gateway):
        route = zen_eval.GatewayRoute(
            name="fb_test",
            strategy="fallback",
            models=["primary.gguf", "secondary.gguf", "tertiary.gguf"],
        )
        gateway.add_route(route)
        assert gateway.resolve("fb_test") == "primary.gguf"

    def test_fallback_chain(self, gateway):
        route = zen_eval.GatewayRoute(
            name="chain_test",
            strategy="fallback",
            models=["m1.gguf", "m2.gguf", "m3.gguf"],
        )
        gateway.add_route(route)
        chain = gateway.get_fallback_chain("chain_test")
        assert chain == ["m1.gguf", "m2.gguf", "m3.gguf"]

    def test_weighted_distribution(self, gateway):
        """Weighted routing should roughly match the weight distribution."""
        route = zen_eval.GatewayRoute(
            name="weighted_test",
            strategy="weighted",
            models=["heavy.gguf", "light.gguf"],
            config={"weights": [90, 10]},
        )
        gateway.add_route(route)

        counts = {"heavy.gguf": 0, "light.gguf": 0}
        n = 1000
        for _ in range(n):
            model = gateway.resolve("weighted_test")
            counts[model] += 1

        # Heavy should get ~90% (with margin for randomness)
        heavy_pct = counts["heavy.gguf"] / n
        assert 0.8 < heavy_pct < 0.98, f"Expected ~90% got {heavy_pct*100:.1f}%"

    def test_ab_test(self, gateway):
        """A/B test should split traffic according to percentages."""
        route = zen_eval.GatewayRoute(
            name="ab_test",
            strategy="ab_test",
            models=["control.gguf", "treatment.gguf"],
            config={"split": [70, 30]},
        )
        gateway.add_route(route)

        counts = {"control.gguf": 0, "treatment.gguf": 0}
        n = 1000
        for _ in range(n):
            model = gateway.resolve("ab_test")
            counts[model] += 1

        control_pct = counts["control.gguf"] / n
        assert 0.55 < control_pct < 0.85, f"Expected ~70% got {control_pct*100:.1f}%"

    def test_resolve_nonexistent_route(self, gateway):
        assert gateway.resolve("does_not_exist") is None

    def test_remove_route(self, gateway):
        route = zen_eval.GatewayRoute(
            name="removable", strategy="round_robin", models=["m.gguf"]
        )
        gateway.add_route(route)
        assert gateway.resolve("removable") == "m.gguf"
        assert gateway.remove_route("removable") is True
        assert gateway.resolve("removable") is None

    def test_log_and_stats(self, gateway):
        """Request logging should produce accurate statistics."""
        route = zen_eval.GatewayRoute(
            name="logged", strategy="round_robin", models=["a.gguf", "b.gguf"]
        )
        gateway.add_route(route)

        # Simulate requests
        gateway.log_request("logged", "a.gguf", "prompt1", latency_ms=100, tokens=50, success=True)
        gateway.log_request("logged", "a.gguf", "prompt2", latency_ms=200, tokens=60, success=True)
        gateway.log_request("logged", "b.gguf", "prompt3", latency_ms=150, tokens=40, success=True)
        gateway.log_request("logged", "b.gguf", "prompt4", latency_ms=300, tokens=70, success=False)

        stats = gateway.get_route_stats("logged")
        assert stats["total_requests"] == 4
        assert "a.gguf" in stats["models"]
        assert stats["models"]["a.gguf"]["requests"] == 2
        assert stats["models"]["a.gguf"]["avg_latency_ms"] == 150.0
        assert stats["models"]["b.gguf"]["success_rate"] == 0.5

    def test_list_routes(self, gateway):
        gateway.add_route(zen_eval.GatewayRoute(
            name="route_1", strategy="round_robin", models=["m1.gguf"]
        ))
        gateway.add_route(zen_eval.GatewayRoute(
            name="route_2", strategy="fallback", models=["m2.gguf"]
        ))
        routes = gateway.list_routes()
        names = {r["name"] for r in routes}
        assert names == {"route_1", "route_2"}

    def test_empty_fallback_chain(self, gateway):
        assert gateway.get_fallback_chain("nonexistent") == []


# ═══════════════════════════════════════════════════════════════════════════════
#  Integration: Feature Interaction Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFeatureIntegration:
    """Tests that verify features work together correctly."""

    def test_prompt_with_judge_feedback_loop(self):
        """Register prompt → use it → collect feedback → calibrate."""
        # 1. Register a prompt
        p = zen_eval.register_prompt(
            name="qa_prompt",
            template="Answer this medical question: {{question}}",
            system_prompt="You are a medical expert.",
            commit_msg="Initial QA prompt",
        )
        zen_eval.set_alias("qa_prompt", "production", p.version)

        # 2. Load via alias
        loaded = zen_eval.load_prompt("qa_prompt", alias="production")
        assert loaded is not None
        assert loaded.template == p.template

        # 3. Simulate judge scoring with feedback
        for i in range(5):
            zen_eval.record_feedback(
                judge_name="medical",
                prompt=f"question_{i}",
                response=f"answer_{i}",
                auto_score=8.0,
                human_score=7.0,  # judge overscores by 1
            )

        # 4. Calibrate future scores
        calibrated = zen_eval.calibrate_score("medical", 8.0)
        assert 6.5 <= calibrated <= 7.5  # Should be close to 7.0

    def test_gateway_with_toolcall_eval(self, gateway):
        """Gateway resolves model → model makes tool calls → evaluate."""
        # Setup gateway
        gateway.add_route(zen_eval.GatewayRoute(
            name="tool_capable",
            strategy="fallback",
            models=["tool_model_a.gguf", "tool_model_b.gguf"],
        ))

        # Resolve model
        model = gateway.resolve("tool_capable")
        assert model == "tool_model_a.gguf"

        # Simulate tool calls from that model
        actual_calls = [
            zen_eval.ToolCall(name="search_db", arguments={"query": "patient 123"}, result="found"),
            zen_eval.ToolCall(name="get_vitals", arguments={"patient_id": 123}, result="BP: 120/80"),
        ]
        expected = [
            zen_eval.ToolCallExpectation(name="search_db", arguments={"query": "patient 123"}, order=0),
            zen_eval.ToolCallExpectation(name="get_vitals", arguments={"patient_id": 123}, order=1),
        ]

        correctness = zen_eval.judge_tool_call_correctness(actual_calls, expected)
        efficiency = zen_eval.judge_tool_call_efficiency(actual_calls, max_expected=3)

        assert correctness.passed is True
        assert efficiency.passed is True

        # Log gateway usage
        gateway.log_request("tool_capable", model, "diagnose patient", latency_ms=500, tokens=200)

    def test_conversation_judge_with_persistence(self):
        """Judge a conversation and persist it."""
        ctx = zen_eval.ConversationContext(
            conversation_id="integration-conv-1",
            model_name="llama-3.1-8b",
            turns=[
                zen_eval.TurnData(role="user", content="My dog's name is Rex and he is a golden retriever.", turn_num=0),
                zen_eval.TurnData(role="assistant", content="That's a wonderful name for a golden retriever! How old is Rex?", turn_num=1),
                zen_eval.TurnData(role="user", content="He is 5 years old and loves swimming.", turn_num=2),
                zen_eval.TurnData(role="assistant", content="Five-year-old golden retrievers like Rex are very active. Swimming is great exercise for them!", turn_num=3),
                zen_eval.TurnData(role="user", content="What activities would you recommend for Rex?", turn_num=4),
                zen_eval.TurnData(role="assistant", content="For Rex, a 5-year-old golden retriever who loves swimming, I'd recommend fetch, dock diving, and hiking trails.", turn_num=5),
            ],
        )

        # Judge
        frustration = zen_eval.judge_user_frustration(ctx)
        retention = zen_eval.judge_knowledge_retention(ctx)
        assert frustration.passed is True
        assert frustration.score == 0.0  # No frustration

        # Persist
        zen_eval.save_conversation(ctx)
        loaded = zen_eval.load_conversation("integration-conv-1")
        assert loaded is not None
        assert len(loaded.turns) == 6


# ═══════════════════════════════════════════════════════════════════════════════
#  Edge Cases & Robustness
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_tool_calls(self):
        result = zen_eval.judge_tool_call_correctness([], [])
        assert result.passed is True

    def test_single_turn_conversation(self):
        ctx = zen_eval.ConversationContext(
            conversation_id="edge-1", model_name="m",
            turns=[zen_eval.TurnData(role="user", content="Hi", turn_num=0)],
        )
        f = zen_eval.judge_user_frustration(ctx)
        k = zen_eval.judge_knowledge_retention(ctx)
        assert f.passed is True
        assert k.passed is True

    def test_very_long_conversation(self):
        """Stress test with many turns."""
        turns = []
        for i in range(100):
            turns.append(zen_eval.TurnData(role="user", content=f"Question number {i} about topic {i % 10}.", turn_num=i * 2))
            turns.append(zen_eval.TurnData(role="assistant", content=f"Answer to question {i}.", turn_num=i * 2 + 1))
        ctx = zen_eval.ConversationContext(
            conversation_id="stress-test", model_name="m", turns=turns,
        )
        f = zen_eval.judge_user_frustration(ctx)
        k = zen_eval.judge_knowledge_retention(ctx)
        assert isinstance(f.score, float)
        assert isinstance(k.score, float)

    def test_unicode_in_prompts(self):
        p = zen_eval.register_prompt(
            name="unicode_test",
            template="分析以下文本：{{text}} — résumé — العربية",
            commit_msg="Unicode support test",
        )
        loaded = zen_eval.load_prompt("unicode_test")
        assert "分析" in loaded.template
        assert "résumé" in loaded.template

    def test_gateway_single_model(self, gateway):
        """All strategies should work with a single model."""
        for strategy in ["round_robin", "weighted", "fallback", "ab_test"]:
            route_name = f"single_{strategy}"
            gateway.add_route(zen_eval.GatewayRoute(
                name=route_name, strategy=strategy, models=["only.gguf"],
            ))
            assert gateway.resolve(route_name) == "only.gguf"

    def test_concurrent_db_access(self):
        """Multiple threads accessing the database simultaneously."""
        import concurrent.futures

        def register_and_load(i: int):
            zen_eval.register_prompt(name=f"concurrent_{i}", template=f"content_{i}")
            return zen_eval.load_prompt(f"concurrent_{i}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(register_and_load, i) for i in range(20)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert all(r is not None for r in results)
        assert len(results) == 20
