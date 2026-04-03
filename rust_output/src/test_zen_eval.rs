/// Comprehensive tests for zen_eval module
/// ========================================
/// 
/// Tests all five features:
/// 1. Multi-turn conversation judges (UserFrustration, KnowledgeRetention)
/// 2. Judge alignment via human feedback
/// 3. Prompt versioning with aliases
/// 4. ToolCall evaluators (correctness & efficiency)
/// 5. Local model gateway (routing strategies, analytics)
/// 
/// Run:  pytest tests/test_zen_eval::py -v

use anyhow::{Result, Context};
use crate::zen_eval::*;
use std::collections::HashMap;
use std::collections::HashSet;

pub static REPO_ROOT: std::sync::LazyLock<String /* os::path.dirname */> = std::sync::LazyLock::new(|| Default::default());

/// Tests for the UserFrustration conversation judge.
#[derive(Debug, Clone)]
pub struct TestUserFrustrationJudge {
}

impl TestUserFrustrationJudge {
    /// Helper: build ConversationContext from (role, content) pairs.
    pub fn _make_ctx(&self, turns: Vec<(String, String)>, model: String) -> () {
        // Helper: build ConversationContext from (role, content) pairs.
        ConversationContext(/* conversation_id= */ "test-conv-1".to_string(), /* model_name= */ model, /* turns= */ turns.iter().enumerate().iter().map(|(i, (r, c))| TurnData(/* role= */ r, /* content= */ c, /* turn_num= */ i)).collect::<Vec<_>>())
    }
    /// Normal polite conversation should pass with low score.
    pub fn test_no_frustration(&mut self) -> () {
        // Normal polite conversation should pass with low score.
        let mut ctx = self._make_ctx(vec![("user".to_string(), "What is the capital of France?".to_string()), ("assistant".to_string(), "The capital of France is Paris.".to_string()), ("user".to_string(), "Thanks! And what about Germany?".to_string()), ("assistant".to_string(), "The capital of Germany is Berlin.".to_string())]);
        let mut result = judge_user_frustration(ctx);
        assert!(result.passed == true);
        assert!(result.score <= 0.1_f64);
        assert!(result.judge_name == "UserFrustration".to_string());
    }
    /// Explicit frustration indicators should trigger high score.
    pub fn test_high_frustration(&mut self) -> () {
        // Explicit frustration indicators should trigger high score.
        let mut ctx = self._make_ctx(vec![("user".to_string(), "What is 2+2?".to_string()), ("assistant".to_string(), "Let me think about that complex question.".to_string()), ("user".to_string(), "This is terrible, you are not helping at all!".to_string()), ("assistant".to_string(), "I apologize. 2+2 = 4.".to_string()), ("user".to_string(), "For the last time, I already told you the answer is obvious!".to_string()), ("assistant".to_string(), "You're right, I should have answered immediately.".to_string())]);
        let mut result = judge_user_frustration(ctx);
        assert!(result.passed == false);
        assert!(result.score > 0.3_f64);
        assert!(result.details["matches".to_string()].len() > 0);
    }
    /// Frustration that intensifies should be detected as escalating.
    pub fn test_escalating_frustration(&mut self) -> () {
        // Frustration that intensifies should be detected as escalating.
        let mut ctx = self._make_ctx(vec![("user".to_string(), "Can you help me with this?".to_string()), ("assistant".to_string(), "Sure, what do you need?".to_string()), ("user".to_string(), "I don't think you understand what I said.".to_string()), ("assistant".to_string(), "Let me try again.".to_string()), ("user".to_string(), "That's not what I asked! Please try again.".to_string()), ("assistant".to_string(), "Hmm, let me reconsider.".to_string()), ("user".to_string(), "This is terrible! You are never helpful! What is wrong with you?".to_string()), ("assistant".to_string(), "I sincerely apologize.".to_string())]);
        let mut result = judge_user_frustration(ctx);
        assert!(result.score > 0.3_f64);
        assert!(result.details["escalating".to_string()] == true);
    }
    /// Frustration that resolves in the last turn.
    pub fn test_resolved_frustration(&mut self) -> () {
        // Frustration that resolves in the last turn.
        let mut ctx = self._make_ctx(vec![("user".to_string(), "What is Python?".to_string()), ("assistant".to_string(), "Python is a snake.".to_string()), ("user".to_string(), "That's not what I meant! I meant the programming language!".to_string()), ("assistant".to_string(), "Python is a high-level programming language created by Guido van Rossum.".to_string()), ("user".to_string(), "Great, that's exactly what I needed. Thank you!".to_string()), ("assistant".to_string(), "Glad I could help!".to_string())]);
        let mut result = judge_user_frustration(ctx);
        assert!(result.details["resolved".to_string()] == true);
    }
    /// Edge case: no user turns.
    pub fn test_empty_conversation(&mut self) -> () {
        // Edge case: no user turns.
        let mut ctx = self._make_ctx(vec![("assistant".to_string(), "Hello! How can I help you?".to_string())]);
        let mut result = judge_user_frustration(ctx);
        assert!(result.passed == true);
        assert!(result.score == 0.0_f64);
    }
    /// Medium-severity patterns should contribute but less than high.
    pub fn test_medium_frustration_indicators(&mut self) -> () {
        // Medium-severity patterns should contribute but less than high.
        let mut ctx = self._make_ctx(vec![("user".to_string(), "What color is the sky?".to_string()), ("assistant".to_string(), "The sky can be many colors.".to_string()), ("user".to_string(), "No, that is wrong. It is blue on a clear day.".to_string()), ("assistant".to_string(), "You're correct!".to_string())]);
        let mut result = judge_user_frustration(ctx);
        assert!(result.score > 0.0_f64);
        assert!(result.score <= 0.5_f64);
    }
}

/// Tests for the KnowledgeRetention conversation judge.
#[derive(Debug, Clone)]
pub struct TestKnowledgeRetentionJudge {
}

impl TestKnowledgeRetentionJudge {
    pub fn _make_ctx(&self, turns: Vec<(String, String)>, model: String) -> () {
        ConversationContext(/* conversation_id= */ "test-conv-kr".to_string(), /* model_name= */ model, /* turns= */ turns.iter().enumerate().iter().map(|(i, (r, c))| TurnData(/* role= */ r, /* content= */ c, /* turn_num= */ i)).collect::<Vec<_>>())
    }
    /// Assistant correctly references user-provided facts.
    pub fn test_good_retention(&mut self) -> () {
        // Assistant correctly references user-provided facts.
        let mut ctx = self._make_ctx(vec![("user".to_string(), "My name is Alice and I work at Google as a software engineer.".to_string()), ("assistant".to_string(), "Nice to meet you, Alice! How's work at Google?".to_string()), ("user".to_string(), "I'm working on a machine learning project with TensorFlow.".to_string()), ("assistant".to_string(), "That sounds exciting! TensorFlow is great for ML. How long have you been a software engineer at Google, Alice?".to_string()), ("user".to_string(), "About 5 years now.".to_string()), ("assistant".to_string(), "Five years of software engineering at Google must have given you deep expertise in TensorFlow and machine learning.".to_string())]);
        let mut result = judge_knowledge_retention(ctx);
        assert!(result.passed == true);
        assert!(result.score >= 0.4_f64);
    }
    /// Assistant ignores user-provided context.
    pub fn test_poor_retention(&mut self) -> () {
        // Assistant ignores user-provided context.
        let mut ctx = self._make_ctx(vec![("user".to_string(), "I live in Tokyo and I speak Japanese, English and Mandarin fluently.".to_string()), ("assistant".to_string(), "Interesting! Tell me more.".to_string()), ("user".to_string(), "I work as a translator specializing in medical documents.".to_string()), ("assistant".to_string(), "What language do you speak? Where do you live?".to_string()), ("user".to_string(), "I already told you that!".to_string()), ("assistant".to_string(), "Sorry, can you repeat your location and profession?".to_string())]);
        let mut result = judge_knowledge_retention(ctx);
        assert!(result.score < 0.8_f64);
    }
    /// Conversations with < 4 turns should pass with 1.0.
    pub fn test_too_few_turns(&mut self) -> () {
        // Conversations with < 4 turns should pass with 1.0.
        let mut ctx = self._make_ctx(vec![("user".to_string(), "Hello".to_string()), ("assistant".to_string(), "Hi there!".to_string())]);
        let mut result = judge_knowledge_retention(ctx);
        assert!(result.passed == true);
        assert!(result.score == 1.0_f64);
    }
    /// Conversation where user provides no trackable facts.
    pub fn test_no_substantial_facts(&mut self) -> () {
        // Conversation where user provides no trackable facts.
        let mut ctx = self._make_ctx(vec![("user".to_string(), "Hi".to_string()), ("assistant".to_string(), "Hello!".to_string()), ("user".to_string(), "Ok".to_string()), ("assistant".to_string(), "How can I help?".to_string()), ("user".to_string(), "Thanks".to_string()), ("assistant".to_string(), "You're welcome!".to_string())]);
        let mut result = judge_knowledge_retention(ctx);
        assert!(result.passed == true);
    }
}

/// Tests for saving/loading conversations.
#[derive(Debug, Clone)]
pub struct TestConversationPersistence {
}

impl TestConversationPersistence {
    pub fn test_save_and_load(&self) -> () {
        let mut ctx = ConversationContext(/* conversation_id= */ "persist-test-1".to_string(), /* model_name= */ "llama-3".to_string(), /* turns= */ vec![TurnData(/* role= */ "user".to_string(), /* content= */ "Hello".to_string(), /* turn_num= */ 0), TurnData(/* role= */ "assistant".to_string(), /* content= */ "Hi!".to_string(), /* turn_num= */ 1)], /* metadata= */ HashMap::from([("source".to_string(), "test".to_string())]));
        save_conversation(ctx);
        let mut loaded = load_conversation("persist-test-1".to_string());
        assert!(loaded.is_some());
        assert!(loaded.model_name == "llama-3".to_string());
        assert!(loaded.turns.len() == 2);
        assert!(loaded.turns[0].content == "Hello".to_string());
        assert!(loaded.turns[1].role == "assistant".to_string());
    }
    pub fn test_load_nonexistent(&self) -> () {
        assert!(load_conversation("does-not-exist".to_string()).is_none());
    }
}

/// Tests for the feedback-based judge calibration system.
#[derive(Debug, Clone)]
pub struct TestJudgeAlignment {
}

impl TestJudgeAlignment {
    pub fn test_record_and_retrieve_feedback(&self) -> () {
        let mut fid = record_feedback(/* judge_name= */ "medical".to_string(), /* prompt= */ "What is hypertension?".to_string(), /* response= */ "High blood pressure.".to_string(), /* auto_score= */ 7.5_f64, /* human_score= */ 8.0_f64, /* feedback= */ "Good but could mention causes".to_string());
        assert!(fid > 0);
        let mut history = get_feedback_history("medical".to_string());
        assert!(history.len() == 1);
        assert!(history[0]["auto_score".to_string()] == 7.5_f64);
        assert!(history[0]["human_score".to_string()] == 8.0_f64);
    }
    pub fn test_update_human_score(&self) -> () {
        let mut fid = record_feedback(/* judge_name= */ "coding".to_string(), /* prompt= */ "Write hello world".to_string(), /* response= */ "print('hello world')".to_string(), /* auto_score= */ 9.0_f64);
        assert!(update_human_score(fid, 8.5_f64, "Minor style issues".to_string()));
        let mut history = get_feedback_history("coding".to_string());
        assert!(history[0]["human_score".to_string()] == 8.5_f64);
    }
    pub fn test_update_nonexistent(&self) -> () {
        assert!(update_human_score(99999, 5.0_f64) == false);
    }
    pub fn test_alignment_stats_no_data(&self) -> () {
        let mut stats = get_alignment_stats("nonexistent_judge".to_string());
        assert!(stats["total_feedback".to_string()] == 0);
        assert!(stats["alignment_rate".to_string()] == 0.0_f64);
    }
    pub fn test_alignment_stats_with_data(&self) -> () {
        for i in 0..10.iter() {
            record_feedback(/* judge_name= */ "test_judge".to_string(), /* prompt= */ format!("prompt_{}", i), /* response= */ format!("response_{}", i), /* auto_score= */ (7.0_f64 + ((i % 3) * 0.5_f64)), /* human_score= */ (6.0_f64 + ((i % 3) * 0.5_f64)));
        }
        let mut stats = get_alignment_stats("test_judge".to_string());
        assert!(stats["total_feedback".to_string()] == 10);
        assert!(stats["avg_bias".to_string()] > 0.8_f64);
        assert!(stats["calibration_offset".to_string()] < -0.8_f64);
    }
    pub fn test_calibrate_score(&self) -> () {
        for _ in 0..5.iter() {
            record_feedback(/* judge_name= */ "biased_judge".to_string(), /* prompt= */ "p".to_string(), /* response= */ "r".to_string(), /* auto_score= */ 8.0_f64, /* human_score= */ 6.0_f64);
        }
        let mut calibrated = calibrate_score("biased_judge".to_string(), 8.0_f64);
        assert!(calibrated < 8.0_f64);
        assert!(calibrated >= 5.0_f64);
    }
    /// Calibrated score should never go below 0 or above scale.
    pub fn test_calibrate_clamped(&self) -> () {
        // Calibrated score should never go below 0 or above scale.
        for _ in 0..5.iter() {
            record_feedback(/* judge_name= */ "extreme_judge".to_string(), /* prompt= */ "p".to_string(), /* response= */ "r".to_string(), /* auto_score= */ 2.0_f64, /* human_score= */ 9.0_f64);
        }
        let mut calibrated = calibrate_score("extreme_judge".to_string(), 1.0_f64);
        assert!(calibrated >= 0.0_f64);
    }
    /// With enough data points, Pearson correlation should be computed.
    pub fn test_correlation_computed(&self) -> () {
        // With enough data points, Pearson correlation should be computed.
        for i in 0..20.iter() {
            let mut auto = (5.0_f64 + (i * 0.25_f64));
            let mut human = ((5.0_f64 + (i * 0.25_f64)) + if (i % 2) == 0 { 0.1_f64 } else { -0.1_f64 });
            record_feedback(/* judge_name= */ "corr_judge".to_string(), /* prompt= */ format!("p{}", i), /* response= */ format!("r{}", i), /* auto_score= */ auto, /* human_score= */ human);
        }
        let mut stats = get_alignment_stats("corr_judge".to_string());
        assert!(stats["correlation".to_string()] > 0.9_f64);
    }
}

/// Tests for the prompt versioning and alias system.
#[derive(Debug, Clone)]
pub struct TestPromptVersioning {
}

impl TestPromptVersioning {
    pub fn test_register_first_version(&self) -> () {
        let mut p = register_prompt(/* name= */ "medical_triage".to_string(), /* template= */ "You are a triage nurse. Assess: {{patient_complaint}}".to_string(), /* system_prompt= */ "Be concise and accurate.".to_string(), /* temperature= */ 0.3_f64, /* max_tokens= */ 256, /* commit_msg= */ "Initial triage prompt".to_string());
        assert!(p.name == "medical_triage".to_string());
        assert!(p.version == 1);
        assert!(p.template.contains(&"{{patient_complaint}}".to_string()));
    }
    pub fn test_register_increments_version(&self) -> () {
        register_prompt(/* name= */ "test_prompt".to_string(), /* template= */ "v1 content".to_string());
        let mut p2 = register_prompt(/* name= */ "test_prompt".to_string(), /* template= */ "v2 content".to_string());
        let mut p3 = register_prompt(/* name= */ "test_prompt".to_string(), /* template= */ "v3 content".to_string());
        assert!(p2.version == 2);
        assert!(p3.version == 3);
    }
    pub fn test_load_latest(&self) -> () {
        register_prompt(/* name= */ "evolving".to_string(), /* template= */ "first".to_string());
        register_prompt(/* name= */ "evolving".to_string(), /* template= */ "second".to_string());
        register_prompt(/* name= */ "evolving".to_string(), /* template= */ "third".to_string());
        let mut p = load_prompt("evolving".to_string());
        assert!(p.is_some());
        assert!(p.version == 3);
        assert!(p.template == "third".to_string());
    }
    pub fn test_load_specific_version(&self) -> () {
        register_prompt(/* name= */ "pinned".to_string(), /* template= */ "alpha".to_string());
        register_prompt(/* name= */ "pinned".to_string(), /* template= */ "beta".to_string());
        let mut p = load_prompt("pinned".to_string(), /* version= */ 1);
        assert!(p.is_some());
        assert!(p.template == "alpha".to_string());
    }
    pub fn test_load_nonexistent(&self) -> () {
        assert!(load_prompt("ghost_prompt".to_string()).is_none());
        assert!(load_prompt("ghost_prompt".to_string(), /* version= */ 99).is_none());
    }
    pub fn test_set_and_resolve_alias(&self) -> () {
        register_prompt(/* name= */ "prod_prompt".to_string(), /* template= */ "v1".to_string());
        register_prompt(/* name= */ "prod_prompt".to_string(), /* template= */ "v2".to_string());
        register_prompt(/* name= */ "prod_prompt".to_string(), /* template= */ "v3".to_string());
        assert!(set_alias("prod_prompt".to_string(), "production".to_string(), 2) == true);
        assert!(set_alias("prod_prompt".to_string(), "latest".to_string(), 3) == true);
        let mut p = load_prompt("prod_prompt".to_string(), /* alias= */ "production".to_string());
        assert!(p.is_some());
        assert!(p.version == 2);
        assert!(p.template == "v2".to_string());
    }
    /// Reassigning an alias should update it.
    pub fn test_alias_overwrite(&self) -> () {
        // Reassigning an alias should update it.
        register_prompt(/* name= */ "alias_test".to_string(), /* template= */ "v1".to_string());
        register_prompt(/* name= */ "alias_test".to_string(), /* template= */ "v2".to_string());
        set_alias("alias_test".to_string(), "stable".to_string(), 1);
        set_alias("alias_test".to_string(), "stable".to_string(), 2);
        let mut p = load_prompt("alias_test".to_string(), /* alias= */ "stable".to_string());
        assert!(p.is_some());
        assert!(p.version == 2);
    }
    pub fn test_alias_invalid_version(&self) -> () {
        register_prompt(/* name= */ "no_v99".to_string(), /* template= */ "content".to_string());
        assert!(set_alias("no_v99".to_string(), "bad".to_string(), 99) == false);
    }
    pub fn test_delete_alias(&self) -> () {
        register_prompt(/* name= */ "del_alias".to_string(), /* template= */ "content".to_string());
        set_alias("del_alias".to_string(), "temp".to_string(), 1);
        assert!(delete_alias("del_alias".to_string(), "temp".to_string()) == true);
        assert!(load_prompt("del_alias".to_string(), /* alias= */ "temp".to_string()).is_none());
    }
    pub fn test_list_prompts_summary(&self) -> () {
        register_prompt(/* name= */ "prompt_a".to_string(), /* template= */ "a1".to_string());
        register_prompt(/* name= */ "prompt_a".to_string(), /* template= */ "a2".to_string());
        register_prompt(/* name= */ "prompt_b".to_string(), /* template= */ "b1".to_string());
        let mut all_prompts = list_prompts();
        assert!(all_prompts.len() == 2);
        let mut names = all_prompts.iter().map(|p| p["name".to_string()]).collect::<HashSet<_>>();
        assert!(names == HashSet::from(["prompt_a".to_string(), "prompt_b".to_string()]));
    }
    pub fn test_list_prompt_versions(&self) -> () {
        register_prompt(/* name= */ "versions".to_string(), /* template= */ "v1".to_string(), /* commit_msg= */ "first".to_string());
        register_prompt(/* name= */ "versions".to_string(), /* template= */ "v2".to_string(), /* commit_msg= */ "second".to_string());
        let mut versions = list_prompts("versions".to_string());
        assert!(versions.len() == 2);
        assert!(versions[0]["version".to_string()] == 2);
    }
    pub fn test_list_aliases(&self) -> () {
        register_prompt(/* name= */ "with_aliases".to_string(), /* template= */ "v1".to_string());
        register_prompt(/* name= */ "with_aliases".to_string(), /* template= */ "v2".to_string());
        set_alias("with_aliases".to_string(), "production".to_string(), 1);
        set_alias("with_aliases".to_string(), "canary".to_string(), 2);
        let mut aliases = list_aliases("with_aliases".to_string());
        assert!(aliases.len() == 2);
        let mut alias_names = aliases.iter().map(|a| a["alias".to_string()]).collect::<HashSet<_>>();
        assert!(alias_names == HashSet::from(["production".to_string(), "canary".to_string()]));
    }
    /// Registering same name creates new version, not update.
    pub fn test_prompt_immutability(&self) -> () {
        // Registering same name creates new version, not update.
        register_prompt(/* name= */ "immut".to_string(), /* template= */ "original".to_string());
        register_prompt(/* name= */ "immut".to_string(), /* template= */ "modified".to_string());
        let mut p1 = load_prompt("immut".to_string(), /* version= */ 1);
        let mut p2 = load_prompt("immut".to_string(), /* version= */ 2);
        assert!(p1.template == "original".to_string());
        assert!(p2.template == "modified".to_string());
    }
}

/// Tests for the ToolCallCorrectness judge.
#[derive(Debug, Clone)]
pub struct TestToolCallCorrectness {
}

impl TestToolCallCorrectness {
    pub fn test_perfect_match(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "get_weather".to_string(), /* arguments= */ HashMap::from([("city".to_string(), "Paris".to_string())]))];
        let mut expected = vec![ToolCallExpectation(/* name= */ "get_weather".to_string(), /* arguments= */ HashMap::from([("city".to_string(), "Paris".to_string())]))];
        let mut result = judge_tool_call_correctness(actual, expected);
        assert!(result.passed == true);
        assert!(result.score >= 0.9_f64);
    }
    pub fn test_wrong_function_called(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "get_time".to_string(), /* arguments= */ HashMap::from([("timezone".to_string(), "UTC".to_string())]))];
        let mut expected = vec![ToolCallExpectation(/* name= */ "get_weather".to_string(), /* arguments= */ HashMap::from([("city".to_string(), "Paris".to_string())]))];
        let mut result = judge_tool_call_correctness(actual, expected);
        assert!(result.passed == false);
        assert!(result.score < 0.5_f64);
        assert!(result.details["missing_required".to_string()].contains(&"get_weather".to_string()));
    }
    pub fn test_partial_argument_match(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "search".to_string(), /* arguments= */ HashMap::from([("query".to_string(), "python".to_string()), ("limit".to_string(), 5)]))];
        let mut expected = vec![ToolCallExpectation(/* name= */ "search".to_string(), /* arguments= */ HashMap::from([("query".to_string(), "python".to_string()), ("limit".to_string(), 10)]))];
        let mut result = judge_tool_call_correctness(actual, expected);
        assert!(result.score > 0.0_f64);
    }
    pub fn test_no_expectations(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "random_func".to_string(), /* arguments= */ HashMap::new())];
        let mut result = judge_tool_call_correctness(actual, vec![]);
        assert!(result.passed == true);
        assert!(result.score == 0.7_f64);
    }
    pub fn test_no_expectations_no_calls(&self) -> () {
        let mut result = judge_tool_call_correctness(vec![], vec![]);
        assert!(result.passed == true);
        assert!(result.score == 1.0_f64);
    }
    pub fn test_missing_required_call(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "step_1".to_string(), /* arguments= */ HashMap::new())];
        let mut expected = vec![ToolCallExpectation(/* name= */ "step_1".to_string(), /* arguments= */ None), ToolCallExpectation(/* name= */ "step_2".to_string(), /* arguments= */ None, /* required= */ true)];
        let mut result = judge_tool_call_correctness(actual, expected);
        assert!(result.details["missing_required".to_string()].contains(&"step_2".to_string()));
    }
    pub fn test_optional_not_penalized(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "required_func".to_string(), /* arguments= */ HashMap::new())];
        let mut expected = vec![ToolCallExpectation(/* name= */ "required_func".to_string(), /* arguments= */ None, /* required= */ true), ToolCallExpectation(/* name= */ "optional_func".to_string(), /* arguments= */ None, /* required= */ false)];
        let mut result = judge_tool_call_correctness(actual, expected);
        assert!(result.passed == true);
    }
    pub fn test_unexpected_calls_penalized(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "expected_func".to_string(), /* arguments= */ HashMap::new()), ToolCall(/* name= */ "rogue_func_1".to_string(), /* arguments= */ HashMap::new()), ToolCall(/* name= */ "rogue_func_2".to_string(), /* arguments= */ HashMap::new()), ToolCall(/* name= */ "rogue_func_3".to_string(), /* arguments= */ HashMap::new())];
        let mut expected = vec![ToolCallExpectation(/* name= */ "expected_func".to_string(), /* arguments= */ None)];
        let mut result = judge_tool_call_correctness(actual, expected);
        assert!(result.score < 1.0_f64);
    }
    pub fn test_order_check(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "step_b".to_string(), /* arguments= */ HashMap::new()), ToolCall(/* name= */ "step_a".to_string(), /* arguments= */ HashMap::new())];
        let mut expected = vec![ToolCallExpectation(/* name= */ "step_a".to_string(), /* arguments= */ None, /* order= */ 0), ToolCallExpectation(/* name= */ "step_b".to_string(), /* arguments= */ None, /* order= */ 1)];
        let mut result = judge_tool_call_correctness(actual, expected);
        assert!(result.details["order_correct".to_string()] == false);
    }
    pub fn test_case_insensitive_matching(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "GetWeather".to_string(), /* arguments= */ HashMap::from([("City".to_string(), "Paris".to_string())]))];
        let mut expected = vec![ToolCallExpectation(/* name= */ "getweather".to_string(), /* arguments= */ HashMap::from([("City".to_string(), "paris".to_string())]))];
        let mut result = judge_tool_call_correctness(actual, expected);
        assert!(result.score >= 0.8_f64);
    }
    pub fn test_numeric_tolerance(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "set_temp".to_string(), /* arguments= */ HashMap::from([("value".to_string(), 72.01_f64)]))];
        let mut expected = vec![ToolCallExpectation(/* name= */ "set_temp".to_string(), /* arguments= */ HashMap::from([("value".to_string(), 72.0_f64)]))];
        let mut result = judge_tool_call_correctness(actual, expected);
        assert!(result.score >= 0.9_f64);
    }
}

/// Tests for the ToolCallEfficiency judge.
#[derive(Debug, Clone)]
pub struct TestToolCallEfficiency {
}

impl TestToolCallEfficiency {
    pub fn test_efficient_calls(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "search".to_string(), /* arguments= */ HashMap::from([("q".to_string(), "hello".to_string())]), /* result= */ "found".to_string()), ToolCall(/* name= */ "format".to_string(), /* arguments= */ HashMap::from([("data".to_string(), "x".to_string())]), /* result= */ "formatted".to_string())];
        let mut result = judge_tool_call_efficiency(actual);
        assert!(result.passed == true);
        assert!(result.score >= 0.9_f64);
    }
    pub fn test_duplicate_calls(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "search".to_string(), /* arguments= */ HashMap::from([("q".to_string(), "hello".to_string())]), /* result= */ "found".to_string()), ToolCall(/* name= */ "search".to_string(), /* arguments= */ HashMap::from([("q".to_string(), "hello".to_string())]), /* result= */ "found".to_string())];
        let mut result = judge_tool_call_efficiency(actual);
        assert!(result.score < 1.0_f64);
        assert!(result.details["duplicates".to_string()].contains(&"search".to_string()));
    }
    pub fn test_wasted_calls(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "lookup".to_string(), /* arguments= */ HashMap::from([("id".to_string(), 1)]), /* result= */ None), ToolCall(/* name= */ "lookup".to_string(), /* arguments= */ HashMap::from([("id".to_string(), 2)]), /* result= */ None)];
        let mut result = judge_tool_call_efficiency(actual);
        assert!(result.details["wasted_calls".to_string()] == 2);
    }
    pub fn test_out_of_bounds(&self) -> () {
        let mut actual = vec![ToolCall(/* name= */ "a".to_string(), /* arguments= */ HashMap::new(), /* result= */ "ok".to_string()), ToolCall(/* name= */ "b".to_string(), /* arguments= */ HashMap::new(), /* result= */ "ok".to_string()), ToolCall(/* name= */ "c".to_string(), /* arguments= */ HashMap::new(), /* result= */ "ok".to_string())];
        let mut result = judge_tool_call_efficiency(actual, /* min_expected= */ 1, /* max_expected= */ 2);
        assert!(result.details["bounds_ok".to_string()] == false);
    }
}

/// Tests for the Local Model Gateway routing system.
#[derive(Debug, Clone)]
pub struct TestLocalModelGateway {
}

impl TestLocalModelGateway {
    pub fn test_round_robin(&self, gateway: String) -> () {
        let mut route = GatewayRoute(/* name= */ "rr_test".to_string(), /* strategy= */ "round_robin".to_string(), /* models= */ vec!["model_a.gguf".to_string(), "model_b.gguf".to_string(), "model_c.gguf".to_string()]);
        gateway.add_route(route);
        let mut results = 0..6.iter().map(|_| gateway.resolve("rr_test".to_string())).collect::<Vec<_>>();
        assert!(results == vec!["model_a.gguf".to_string(), "model_b.gguf".to_string(), "model_c.gguf".to_string(), "model_a.gguf".to_string(), "model_b.gguf".to_string(), "model_c.gguf".to_string()]);
    }
    pub fn test_fallback_returns_first(&self, gateway: String) -> () {
        let mut route = GatewayRoute(/* name= */ "fb_test".to_string(), /* strategy= */ "fallback".to_string(), /* models= */ vec!["primary.gguf".to_string(), "secondary.gguf".to_string(), "tertiary.gguf".to_string()]);
        gateway.add_route(route);
        assert!(gateway.resolve("fb_test".to_string()) == "primary.gguf".to_string());
    }
    pub fn test_fallback_chain(&self, gateway: String) -> () {
        let mut route = GatewayRoute(/* name= */ "chain_test".to_string(), /* strategy= */ "fallback".to_string(), /* models= */ vec!["m1.gguf".to_string(), "m2.gguf".to_string(), "m3.gguf".to_string()]);
        gateway.add_route(route);
        let mut chain = gateway.get_fallback_chain("chain_test".to_string());
        assert!(chain == vec!["m1.gguf".to_string(), "m2.gguf".to_string(), "m3.gguf".to_string()]);
    }
    /// Weighted routing should roughly match the weight distribution.
    pub fn test_weighted_distribution(&self, gateway: String) -> () {
        // Weighted routing should roughly match the weight distribution.
        let mut route = GatewayRoute(/* name= */ "weighted_test".to_string(), /* strategy= */ "weighted".to_string(), /* models= */ vec!["heavy.gguf".to_string(), "light.gguf".to_string()], /* config= */ HashMap::from([("weights".to_string(), vec![90, 10])]));
        gateway.add_route(route);
        let mut counts = HashMap::from([("heavy.gguf".to_string(), 0), ("light.gguf".to_string(), 0)]);
        let mut n = 1000;
        for _ in 0..n.iter() {
            let mut model = gateway.resolve("weighted_test".to_string());
            counts[model] += 1;
        }
        let mut heavy_pct = (counts["heavy.gguf".to_string()] / n);
        assert!((0.8_f64 < heavy_pct) && (heavy_pct < 0.98_f64), "Expected ~90% got {:.1}%", (heavy_pct * 100));
    }
    /// A/B test should split traffic according to percentages.
    pub fn test_ab_test(&self, gateway: String) -> () {
        // A/B test should split traffic according to percentages.
        let mut route = GatewayRoute(/* name= */ "ab_test".to_string(), /* strategy= */ "ab_test".to_string(), /* models= */ vec!["control.gguf".to_string(), "treatment.gguf".to_string()], /* config= */ HashMap::from([("split".to_string(), vec![70, 30])]));
        gateway.add_route(route);
        let mut counts = HashMap::from([("control.gguf".to_string(), 0), ("treatment.gguf".to_string(), 0)]);
        let mut n = 1000;
        for _ in 0..n.iter() {
            let mut model = gateway.resolve("ab_test".to_string());
            counts[model] += 1;
        }
        let mut control_pct = (counts["control.gguf".to_string()] / n);
        assert!((0.55_f64 < control_pct) && (control_pct < 0.85_f64), "Expected ~70% got {:.1}%", (control_pct * 100));
    }
    pub fn test_resolve_nonexistent_route(&self, gateway: String) -> () {
        assert!(gateway.resolve("does_not_exist".to_string()).is_none());
    }
    pub fn test_remove_route(&self, gateway: String) -> () {
        let mut route = GatewayRoute(/* name= */ "removable".to_string(), /* strategy= */ "round_robin".to_string(), /* models= */ vec!["m.gguf".to_string()]);
        gateway.add_route(route);
        assert!(gateway.resolve("removable".to_string()) == "m.gguf".to_string());
        assert!(gateway.remove_route("removable".to_string()) == true);
        assert!(gateway.resolve("removable".to_string()).is_none());
    }
    /// Request logging should produce accurate statistics.
    pub fn test_log_and_stats(&self, gateway: String) -> () {
        // Request logging should produce accurate statistics.
        let mut route = GatewayRoute(/* name= */ "logged".to_string(), /* strategy= */ "round_robin".to_string(), /* models= */ vec!["a.gguf".to_string(), "b.gguf".to_string()]);
        gateway.add_route(route);
        gateway.log_request("logged".to_string(), "a.gguf".to_string(), "prompt1".to_string(), /* latency_ms= */ 100, /* tokens= */ 50, /* success= */ true);
        gateway.log_request("logged".to_string(), "a.gguf".to_string(), "prompt2".to_string(), /* latency_ms= */ 200, /* tokens= */ 60, /* success= */ true);
        gateway.log_request("logged".to_string(), "b.gguf".to_string(), "prompt3".to_string(), /* latency_ms= */ 150, /* tokens= */ 40, /* success= */ true);
        gateway.log_request("logged".to_string(), "b.gguf".to_string(), "prompt4".to_string(), /* latency_ms= */ 300, /* tokens= */ 70, /* success= */ false);
        let mut stats = gateway.get_route_stats("logged".to_string());
        assert!(stats["total_requests".to_string()] == 4);
        assert!(stats["models".to_string()].contains(&"a.gguf".to_string()));
        assert!(stats["models".to_string()]["a.gguf".to_string()]["requests".to_string()] == 2);
        assert!(stats["models".to_string()]["a.gguf".to_string()]["avg_latency_ms".to_string()] == 150.0_f64);
        assert!(stats["models".to_string()]["b.gguf".to_string()]["success_rate".to_string()] == 0.5_f64);
    }
    pub fn test_list_routes(&self, gateway: String) -> () {
        gateway.add_route(GatewayRoute(/* name= */ "route_1".to_string(), /* strategy= */ "round_robin".to_string(), /* models= */ vec!["m1.gguf".to_string()]));
        gateway.add_route(GatewayRoute(/* name= */ "route_2".to_string(), /* strategy= */ "fallback".to_string(), /* models= */ vec!["m2.gguf".to_string()]));
        let mut routes = gateway.list_routes();
        let mut names = routes.iter().map(|r| r["name".to_string()]).collect::<HashSet<_>>();
        assert!(names == HashSet::from(["route_1".to_string(), "route_2".to_string()]));
    }
    pub fn test_empty_fallback_chain(&self, gateway: String) -> () {
        assert!(gateway.get_fallback_chain("nonexistent".to_string()) == vec![]);
    }
}

/// Tests that verify features work together correctly.
#[derive(Debug, Clone)]
pub struct TestFeatureIntegration {
}

impl TestFeatureIntegration {
    /// Register prompt → use it → collect feedback → calibrate.
    pub fn test_prompt_with_judge_feedback_loop(&self) -> () {
        // Register prompt → use it → collect feedback → calibrate.
        let mut p = register_prompt(/* name= */ "qa_prompt".to_string(), /* template= */ "Answer this medical question: {{question}}".to_string(), /* system_prompt= */ "You are a medical expert.".to_string(), /* commit_msg= */ "Initial QA prompt".to_string());
        set_alias("qa_prompt".to_string(), "production".to_string(), p.version);
        let mut loaded = load_prompt("qa_prompt".to_string(), /* alias= */ "production".to_string());
        assert!(loaded.is_some());
        assert!(loaded.template == p.template);
        for i in 0..5.iter() {
            record_feedback(/* judge_name= */ "medical".to_string(), /* prompt= */ format!("question_{}", i), /* response= */ format!("answer_{}", i), /* auto_score= */ 8.0_f64, /* human_score= */ 7.0_f64);
        }
        let mut calibrated = calibrate_score("medical".to_string(), 8.0_f64);
        assert!((6.5_f64 <= calibrated) && (calibrated <= 7.5_f64));
    }
    /// Gateway resolves model → model makes tool calls → evaluate.
    pub fn test_gateway_with_toolcall_eval(&self, gateway: String) -> () {
        // Gateway resolves model → model makes tool calls → evaluate.
        gateway.add_route(GatewayRoute(/* name= */ "tool_capable".to_string(), /* strategy= */ "fallback".to_string(), /* models= */ vec!["tool_model_a.gguf".to_string(), "tool_model_b.gguf".to_string()]));
        let mut model = gateway.resolve("tool_capable".to_string());
        assert!(model == "tool_model_a.gguf".to_string());
        let mut actual_calls = vec![ToolCall(/* name= */ "search_db".to_string(), /* arguments= */ HashMap::from([("query".to_string(), "patient 123".to_string())]), /* result= */ "found".to_string()), ToolCall(/* name= */ "get_vitals".to_string(), /* arguments= */ HashMap::from([("patient_id".to_string(), 123)]), /* result= */ "BP: 120/80".to_string())];
        let mut expected = vec![ToolCallExpectation(/* name= */ "search_db".to_string(), /* arguments= */ HashMap::from([("query".to_string(), "patient 123".to_string())]), /* order= */ 0), ToolCallExpectation(/* name= */ "get_vitals".to_string(), /* arguments= */ HashMap::from([("patient_id".to_string(), 123)]), /* order= */ 1)];
        let mut correctness = judge_tool_call_correctness(actual_calls, expected);
        let mut efficiency = judge_tool_call_efficiency(actual_calls, /* max_expected= */ 3);
        assert!(correctness.passed == true);
        assert!(efficiency.passed == true);
        gateway.log_request("tool_capable".to_string(), model, "diagnose patient".to_string(), /* latency_ms= */ 500, /* tokens= */ 200);
    }
    /// Judge a conversation and persist it.
    pub fn test_conversation_judge_with_persistence(&self) -> () {
        // Judge a conversation and persist it.
        let mut ctx = ConversationContext(/* conversation_id= */ "integration-conv-1".to_string(), /* model_name= */ "llama-3.1-8b".to_string(), /* turns= */ vec![TurnData(/* role= */ "user".to_string(), /* content= */ "My dog's name is Rex and he is a golden retriever.".to_string(), /* turn_num= */ 0), TurnData(/* role= */ "assistant".to_string(), /* content= */ "That's a wonderful name for a golden retriever! How old is Rex?".to_string(), /* turn_num= */ 1), TurnData(/* role= */ "user".to_string(), /* content= */ "He is 5 years old and loves swimming.".to_string(), /* turn_num= */ 2), TurnData(/* role= */ "assistant".to_string(), /* content= */ "Five-year-old golden retrievers like Rex are very active. Swimming is great exercise for them!".to_string(), /* turn_num= */ 3), TurnData(/* role= */ "user".to_string(), /* content= */ "What activities would you recommend for Rex?".to_string(), /* turn_num= */ 4), TurnData(/* role= */ "assistant".to_string(), /* content= */ "For Rex, a 5-year-old golden retriever who loves swimming, I'd recommend fetch, dock diving, and hiking trails.".to_string(), /* turn_num= */ 5)]);
        let mut frustration = judge_user_frustration(ctx);
        let mut retention = judge_knowledge_retention(ctx);
        assert!(frustration.passed == true);
        assert!(frustration.score == 0.0_f64);
        save_conversation(ctx);
        let mut loaded = load_conversation("integration-conv-1".to_string());
        assert!(loaded.is_some());
        assert!(loaded.turns.len() == 6);
    }
}

/// Edge cases and boundary conditions.
#[derive(Debug, Clone)]
pub struct TestEdgeCases {
}

impl TestEdgeCases {
    pub fn test_empty_tool_calls(&self) -> () {
        let mut result = judge_tool_call_correctness(vec![], vec![]);
        assert!(result.passed == true);
    }
    pub fn test_single_turn_conversation(&self) -> () {
        let mut ctx = ConversationContext(/* conversation_id= */ "edge-1".to_string(), /* model_name= */ "m".to_string(), /* turns= */ vec![TurnData(/* role= */ "user".to_string(), /* content= */ "Hi".to_string(), /* turn_num= */ 0)]);
        let mut f = judge_user_frustration(ctx);
        let mut k = judge_knowledge_retention(ctx);
        assert!(f.passed == true);
        assert!(k.passed == true);
    }
    /// Stress test with many turns.
    pub fn test_very_long_conversation(&self) -> () {
        // Stress test with many turns.
        let mut turns = vec![];
        for i in 0..100.iter() {
            turns.push(TurnData(/* role= */ "user".to_string(), /* content= */ format!("Question number {} about topic {}.", i, (i % 10)), /* turn_num= */ (i * 2)));
            turns.push(TurnData(/* role= */ "assistant".to_string(), /* content= */ format!("Answer to question {}.", i), /* turn_num= */ ((i * 2) + 1)));
        }
        let mut ctx = ConversationContext(/* conversation_id= */ "stress-test".to_string(), /* model_name= */ "m".to_string(), /* turns= */ turns);
        let mut f = judge_user_frustration(ctx);
        let mut k = judge_knowledge_retention(ctx);
        assert!(/* /* isinstance(f.score, float) */ */ true);
        assert!(/* /* isinstance(k.score, float) */ */ true);
    }
    pub fn test_unicode_in_prompts(&self) -> () {
        let mut p = register_prompt(/* name= */ "unicode_test".to_string(), /* template= */ "分析以下文本：{{text}} — résumé — العربية".to_string(), /* commit_msg= */ "Unicode support test".to_string());
        let mut loaded = load_prompt("unicode_test".to_string());
        assert!(loaded.template.contains(&"分析".to_string()));
        assert!(loaded.template.contains(&"résumé".to_string()));
    }
    /// All strategies should work with a single model.
    pub fn test_gateway_single_model(&self, gateway: String) -> () {
        // All strategies should work with a single model.
        for strategy in vec!["round_robin".to_string(), "weighted".to_string(), "fallback".to_string(), "ab_test".to_string()].iter() {
            let mut route_name = format!("single_{}", strategy);
            gateway.add_route(GatewayRoute(/* name= */ route_name, /* strategy= */ strategy, /* models= */ vec!["only.gguf".to_string()]));
            assert!(gateway.resolve(route_name) == "only.gguf".to_string());
        }
    }
    /// Multiple threads accessing the database simultaneously.
    pub fn test_concurrent_db_access(&self) -> () {
        // Multiple threads accessing the database simultaneously.
        // TODO: import concurrent.futures
        let register_and_load = |i| {
            register_prompt(/* name= */ format!("concurrent_{}", i), /* template= */ format!("content_{}", i));
            load_prompt(format!("concurrent_{}", i))
        };
        let mut pool = concurrent.futures.ThreadPoolExecutor(/* max_workers= */ 8);
        {
            let mut futures = 0..20.iter().map(|i| pool.submit(register_and_load, i)).collect::<Vec<_>>();
            let mut results = concurrent.futures.as_completed(futures).iter().map(|f| f.result()).collect::<Vec<_>>();
        }
        assert!(results.iter().map(|r| r.is_some()).collect::<Vec<_>>().iter().all(|v| *v));
        assert!(results.len() == 20);
    }
}

/// Each test gets a fresh SQLite database.
pub fn fresh_db(tmp_path: String) -> () {
    // Each test gets a fresh SQLite database.
    let mut db_path = (tmp_path / "test_zeneval.db".to_string()).to_string();
    init_db(db_path);
    /* yield db_path */;
}

/// Fresh gateway instance pointing to the test DB.
pub fn gateway(fresh_db: String) -> () {
    // Fresh gateway instance pointing to the test DB.
    let mut gw = LocalModelGateway();
    gw
}
