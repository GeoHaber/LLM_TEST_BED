/// zen_eval — Evaluation, Feedback, Prompt Versioning, Tool-Call Judges & Local Model Gateway
/// ===========================================================================================
/// 
/// Features:
/// 1. Multi-turn conversation judges (UserFrustration, KnowledgeRetention)
/// 2. Judge alignment via human feedback (simple weighted calibration, no DSPy)
/// 3. Prompt versioning with aliases (SQLite-backed, immutable versions)
/// 4. ToolCall evaluators (function-call correctness & efficiency)
/// 5. Local model gateway (A/B routing, fallback chains between local GGUFs)
/// 
/// All persistence is SQLite — zero-cloud, zero-install philosophy.

use anyhow::{Result, Context};
use regex::Regex;
use serde::{Serialize, Deserialize};
use std::collections::HashMap;
use std::collections::HashSet;

pub static _DB_PATH: std::sync::LazyLock<String /* os::environ.get */> = std::sync::LazyLock::new(|| Default::default());

pub static _DB_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> = std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

pub const _SCHEMA: &str = "\\n-- Prompt versioning\\nCREATE TABLE IF NOT EXISTS prompts (\\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\\n    name        TEXT NOT NULL,\\n    version     INTEGER NOT NULL DEFAULT 1,\\n    template    TEXT NOT NULL,\\n    system_prompt TEXT NOT NULL DEFAULT '',\\n    temperature REAL DEFAULT 0.7,\\n    max_tokens  INTEGER DEFAULT 512,\\n    commit_msg  TEXT DEFAULT '',\\n    created_at  TEXT NOT NULL DEFAULT (datetime('now')),\\n    UNIQUE(name, version)\\n);\\n\\nCREATE TABLE IF NOT EXISTS prompt_aliases (\\n    name        TEXT NOT NULL,\\n    alias       TEXT NOT NULL,\\n    version     INTEGER NOT NULL,\\n    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),\\n    PRIMARY KEY (name, alias)\\n);\\n\\n-- Judge feedback for alignment\\nCREATE TABLE IF NOT EXISTS judge_feedback (\\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\\n    judge_name  TEXT NOT NULL,\\n    prompt      TEXT NOT NULL,\\n    response    TEXT NOT NULL,\\n    auto_score  REAL NOT NULL,\\n    human_score REAL,\\n    feedback    TEXT DEFAULT '',\\n    created_at  TEXT NOT NULL DEFAULT (datetime('now'))\\n);\\n\\n-- Conversation sessions for multi-turn judges\\nCREATE TABLE IF NOT EXISTS conversations (\\n    id          TEXT PRIMARY KEY,\\n    model_name  TEXT NOT NULL,\\n    created_at  TEXT NOT NULL DEFAULT (datetime('now')),\\n    metadata    TEXT DEFAULT '{}'\\n);\\n\\nCREATE TABLE IF NOT EXISTS conversation_turns (\\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\\n    conv_id     TEXT NOT NULL REFERENCES conversations(id),\\n    turn_num    INTEGER NOT NULL,\\n    role        TEXT NOT NULL CHECK(role IN ('user','assistant','system')),\\n    content     TEXT NOT NULL,\\n    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),\\n    metadata    TEXT DEFAULT '{}'\\n);\\n\\n-- Gateway routing config\\nCREATE TABLE IF NOT EXISTS gateway_routes (\\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\\n    name        TEXT NOT NULL UNIQUE,\\n    strategy    TEXT NOT NULL CHECK(strategy IN ('round_robin','weighted','fallback','ab_test')),\\n    models      TEXT NOT NULL,\\n    config      TEXT NOT NULL DEFAULT '{}',\\n    enabled     INTEGER NOT NULL DEFAULT 1,\\n    created_at  TEXT NOT NULL DEFAULT (datetime('now'))\\n);\\n\\n-- Gateway request log\\nCREATE TABLE IF NOT EXISTS gateway_log (\\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\\n    route_name  TEXT NOT NULL,\\n    model_used  TEXT NOT NULL,\\n    prompt_hash TEXT NOT NULL,\\n    latency_ms  REAL,\\n    tokens      INTEGER,\\n    success     INTEGER NOT NULL DEFAULT 1,\\n    created_at  TEXT NOT NULL DEFAULT (datetime('now'))\\n);\\n";

pub static _FRUSTRATION_INDICATORS: std::sync::LazyLock<HashMap<String, serde_json::Value>> = std::sync::LazyLock::new(|| HashMap::new());

pub static _FRUSTRATION_WEIGHTS: std::sync::LazyLock<HashMap<String, serde_json::Value>> = std::sync::LazyLock::new(|| HashMap::new());

pub static _GATEWAY: std::sync::LazyLock<LocalModelGateway> = std::sync::LazyLock::new(|| Default::default());

/// A single conversation turn.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TurnData {
    pub role: String,
    pub content: String,
    pub turn_num: i64,
    pub metadata: HashMap<String, serde_json::Value>,
}

/// Full conversation context for multi-turn judges.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConversationContext {
    pub conversation_id: String,
    pub model_name: String,
    pub turns: Vec<TurnData>,
    pub metadata: HashMap<String, serde_json::Value>,
}

/// Result from any judge evaluation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JudgeResult {
    pub judge_name: String,
    pub score: f64,
    pub passed: bool,
    pub rationale: String,
    pub details: HashMap<String, serde_json::Value>,
}

/// A versioned prompt template.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Prompt {
    pub name: String,
    pub version: i64,
    pub template: String,
    pub system_prompt: String,
    pub temperature: f64,
    pub max_tokens: i64,
    pub commit_msg: String,
    pub created_at: String,
}

/// A single tool/function call made by a model.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub name: String,
    pub arguments: HashMap<String, serde_json::Value>,
    pub result: Option<String>,
}

/// Expected tool call for evaluation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallExpectation {
    pub name: String,
    pub arguments: Option<HashMap>,
    pub required: bool,
    pub order: Option<i64>,
}

/// A routing configuration for the local model gateway.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GatewayRoute {
    pub name: String,
    pub strategy: String,
    pub models: Vec<String>,
    pub config: HashMap<String, serde_json::Value>,
    pub enabled: bool,
}

/// Routes inference requests across local GGUF models.
/// 
/// Strategies:
/// - round_robin: Cycle through models evenly
/// - weighted:    Route by weight percentages (A/B testing)
/// - fallback:    Try first model, fall back to next on failure
/// - ab_test:     Split traffic by percentage for comparison
/// 
/// All routing decisions are logged to SQLite for analysis.
#[derive(Debug, Clone)]
pub struct LocalModelGateway {
    pub _routes: HashMap<String, GatewayRoute>,
    pub _counters: HashMap<String, i64>,
    pub _lock: std::sync::Mutex<()>,
}

impl LocalModelGateway {
    pub fn new() -> Self {
        Self {
            _routes: HashMap::new(),
            _counters: HashMap::new(),
            _lock: std::sync::Mutex::new(()),
        }
    }
    /// Load routes from database.
    pub fn _load_routes(&mut self) -> Result<()> {
        // Load routes from database.
        // try:
        {
            let _ctx = _db_lock;
            {
                let mut conn = _get_db();
                // try:
                {
                    let mut rows = conn.execute("SELECT * FROM gateway_routes WHERE enabled = 1".to_string()).fetchall();
                    for r in rows.iter() {
                        self._routes[r["name".to_string()]] = GatewayRoute(/* name= */ r["name".to_string()], /* strategy= */ r["strategy".to_string()], /* models= */ serde_json::from_str(&r["models".to_string()]).unwrap(), /* config= */ serde_json::from_str(&r["config".to_string()]).unwrap(), /* enabled= */ (r["enabled".to_string()] != 0));
                    }
                }
                // finally:
                    conn.close();
            }
        }
        // except Exception as _e:
    }
    /// Add or update a gateway route.
    pub fn add_route(&mut self, route: GatewayRoute) -> Result<()> {
        // Add or update a gateway route.
        let _ctx = _db_lock;
        {
            let mut conn = _get_db();
            // try:
            {
                conn.execute("INSERT OR REPLACE INTO gateway_routes (name, strategy, models, config, enabled) VALUES (?, ?, ?, ?, ?)".to_string(), (route.name, route.strategy, serde_json::to_string(&route.models).unwrap(), serde_json::to_string(&route.config).unwrap(), route.enabled.to_string().parse::<i64>().unwrap_or(0)));
                conn.commit();
            }
            // finally:
                conn.close();
        }
        let _ctx = self._lock;
        {
            self._routes[route.name] = route;
        }
    }
    /// Remove a gateway route.
    pub fn remove_route(&mut self, name: String) -> Result<bool> {
        // Remove a gateway route.
        let _ctx = _db_lock;
        {
            let mut conn = _get_db();
            // try:
            {
                let mut cur = conn.execute("DELETE FROM gateway_routes WHERE name = ?".to_string(), (name));
                conn.commit();
                let mut removed = cur.rowcount > 0;
            }
            // finally:
                conn.close();
        }
        let _ctx = self._lock;
        {
            self._routes.remove(&name).unwrap_or(None);
            self._counters.remove(&name).unwrap_or(None);
        }
        Ok(removed)
    }
    /// Resolve a route to a specific model path based on strategy.
    /// 
    /// Returns the model path to use for this request.
    pub fn resolve(&mut self, route_name: String) -> Option<String> {
        // Resolve a route to a specific model path based on strategy.
        // 
        // Returns the model path to use for this request.
        let _ctx = self._lock;
        {
            let mut route = self._routes.get(&route_name).cloned();
            if (!route || !route.models) {
                None
            }
            if route.strategy == "round_robin".to_string() {
                let mut idx = self._counters.get(&route_name).cloned().unwrap_or(0);
                let mut model = route.models[(idx % route.models.len())];
                self._counters[route_name] = (idx + 1);
                model
            } else if route.strategy == "weighted".to_string() {
                let mut weights = route.config.get(&"weights".to_string()).cloned().unwrap_or((vec![1.0_f64] * route.models.len()));
                while weights.len() < route.models.len() {
                    weights.push(1.0_f64);
                }
                let mut total = weights.iter().sum::<i64>();
                if total <= 0 {
                    route.models[0]
                }
                let mut r = (random.random() * total);
                let mut cumulative = 0.0_f64;
                for (model, w) in route.models.iter().zip(weights.iter()).iter() {
                    cumulative += w;
                    if r <= cumulative {
                        model
                    }
                }
                route.models[-1]
            } else if route.strategy == "fallback".to_string() {
                route.models[0]
            } else if route.strategy == "ab_test".to_string() {
                let mut split = route.config.get(&"split".to_string()).cloned().unwrap_or(vec![50, 50]);
                while split.len() < route.models.len() {
                    split.push(0);
                }
                let mut total = split.iter().sum::<i64>();
                if total <= 0 {
                    route.models[0]
                }
                let mut r = (random.random() * total);
                let mut cumulative = 0.0_f64;
                for (model, pct) in route.models.iter().zip(split.iter()).iter() {
                    cumulative += pct;
                    if r <= cumulative {
                        model
                    }
                }
                route.models[-1]
            }
            route.models[0]
        }
    }
    /// Get the full fallback chain for a route.
    pub fn get_fallback_chain(&mut self, route_name: String) -> Vec<String> {
        // Get the full fallback chain for a route.
        let _ctx = self._lock;
        {
            let mut route = self._routes.get(&route_name).cloned();
            if !route {
                vec![]
            }
            route.models.into_iter().collect::<Vec<_>>()
        }
    }
    /// Log a gateway routing decision for analytics.
    pub fn log_request(&self, route_name: String, model_used: String, prompt: String, latency_ms: f64, tokens: i64, success: bool) -> Result<()> {
        // Log a gateway routing decision for analytics.
        let mut prompt_hash = hashlib::sha256(prompt.as_bytes().to_vec()).hexdigest()[..16];
        // try:
        {
            let _ctx = _db_lock;
            {
                let mut conn = _get_db();
                // try:
                {
                    conn.execute("INSERT INTO gateway_log (route_name, model_used, prompt_hash, latency_ms, tokens, success) VALUES (?, ?, ?, ?, ?, ?)".to_string(), (route_name, model_used, prompt_hash, latency_ms, tokens, success.to_string().parse::<i64>().unwrap_or(0)));
                    conn.commit();
                }
                // finally:
                    conn.close();
            }
        }
        // except Exception as _e:
    }
    /// Get statistics for a gateway route.
    pub fn get_route_stats(&self, route_name: String) -> Result<HashMap> {
        // Get statistics for a gateway route.
        let _ctx = _db_lock;
        {
            let mut conn = _get_db();
            // try:
            {
                let mut rows = conn.execute("SELECT model_used, COUNT(*) as count, AVG(latency_ms) as avg_latency, SUM(tokens) as total_tokens, SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes, SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures FROM gateway_log WHERE route_name = ? GROUP BY model_used".to_string(), (route_name)).fetchall();
                if !rows {
                    HashMap::from([("route_name".to_string(), route_name), ("models".to_string(), HashMap::new()), ("total_requests".to_string(), 0)])
                }
                let mut models = HashMap::new();
                let mut total = 0;
                for r in rows.iter() {
                    let mut count = r["count".to_string()];
                    total += count;
                    models[r["model_used".to_string()]] = HashMap::from([("requests".to_string(), count), ("avg_latency_ms".to_string(), (((r["avg_latency".to_string()] || 0) as f64) * 10f64.powi(1)).round() / 10f64.powi(1)), ("total_tokens".to_string(), (r["total_tokens".to_string()] || 0)), ("success_rate".to_string(), ((if count > 0 { (r["successes".to_string()] / count) } else { 0 } as f64) * 10f64.powi(3)).round() / 10f64.powi(3))]);
                }
                HashMap::from([("route_name".to_string(), route_name), ("models".to_string(), models), ("total_requests".to_string(), total)])
            }
            // finally:
                conn.close();
        }
    }
    /// List all gateway routes.
    pub fn list_routes(&self) -> Vec<HashMap> {
        // List all gateway routes.
        let _ctx = self._lock;
        {
            self._routes.values().iter().map(|r| asdict(r)).collect::<Vec<_>>()
        }
    }
}

/// Thread-safe database connection with WAL mode.
pub fn _get_db() -> Result<sqlite3::Connection> {
    // Thread-safe database connection with WAL mode.
    let mut conn = /* sqlite3 */ _DB_PATH, /* check_same_thread= */ false;
    conn.row_factory = sqlite3::Row;
    conn.execute("PRAGMA journal_mode=WAL".to_string());
    conn.execute("PRAGMA foreign_keys=ON".to_string());
    Ok(conn)
}

/// Create all tables if they don't exist.  Idempotent.
pub fn init_db(db_path: Option<String>) -> () {
    // Create all tables if they don't exist.  Idempotent.
    // global/nonlocal _DB_PATH
    if db_path {
        let mut _DB_PATH = db_path;
    }
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        conn.executescript(_SCHEMA);
        conn.close();
    }
}

/// Extract user questions from conversation turns.
pub fn _extract_questions_from_turns(turns: Vec<TurnData>) -> Vec<String> {
    // Extract user questions from conversation turns.
    let mut questions = vec![];
    for t in turns.iter() {
        if t.role == "user".to_string() {
            let mut parts = re::split("[?]\\s+".to_string(), t.content);
            for p in parts.iter() {
                let mut p = p.trim().to_string();
                if p.len() > 5 {
                    questions.push(if p.ends_with(&*"?".to_string()) { p } else { (p + "?".to_string()) });
                }
            }
        }
    }
    questions
}

/// Filter to only assistant turns.
pub fn _assistant_turns(turns: Vec<TurnData>) -> Vec<TurnData> {
    // Filter to only assistant turns.
    turns.iter().filter(|t| t.role == "assistant".to_string()).map(|t| t).collect::<Vec<_>>()
}

/// Filter to only user turns.
pub fn _user_turns(turns: Vec<TurnData>) -> Vec<TurnData> {
    // Filter to only user turns.
    turns.iter().filter(|t| t.role == "user".to_string()).map(|t| t).collect::<Vec<_>>()
}

/// Detect user frustration in a multi-turn conversation.
/// 
/// Scoring:
/// - Scans all user turns for frustration indicators (regex patterns)
/// - Weights: high-severity=1.0, medium=0.5, low=0.2
/// - Score = weighted_hits / total_user_turns (normalized 0-1)
/// - Checks for escalation pattern (frustration increasing over time)
/// - Checks if frustration was resolved (no indicators in last turn)
pub fn judge_user_frustration(ctx: ConversationContext) -> JudgeResult {
    // Detect user frustration in a multi-turn conversation.
    // 
    // Scoring:
    // - Scans all user turns for frustration indicators (regex patterns)
    // - Weights: high-severity=1.0, medium=0.5, low=0.2
    // - Score = weighted_hits / total_user_turns (normalized 0-1)
    // - Checks for escalation pattern (frustration increasing over time)
    // - Checks if frustration was resolved (no indicators in last turn)
    let mut user_t = _user_turns(ctx.turns);
    if !user_t {
        JudgeResult(/* judge_name= */ "UserFrustration".to_string(), /* score= */ 0.0_f64, /* passed= */ true, /* rationale= */ "No user turns to evaluate.".to_string())
    }
    let mut per_turn_scores = vec![];
    let mut total_weighted = 0.0_f64;
    let mut all_matches = vec![];
    for turn in user_t.iter() {
        let mut text = turn.content.to_lowercase();
        let mut turn_score = 0.0_f64;
        for (severity, patterns) in _FRUSTRATION_INDICATORS.iter().iter() {
            let mut weight = _FRUSTRATION_WEIGHTS[&severity];
            for pat in patterns.iter() {
                if regex::Regex::new(&pat).unwrap().is_match(&text) {
                    turn_score += weight;
                    all_matches.push(HashMap::from([("turn".to_string(), turn.turn_num), ("severity".to_string(), severity), ("pattern".to_string(), pat[..40])]));
                }
            }
        }
        per_turn_scores.push(turn_score.min(3.0_f64));
        total_weighted += turn_score.min(3.0_f64);
    }
    let mut n = user_t.len();
    let mut raw_score = if n > 0 { (total_weighted / (n * 3.0_f64)) } else { 0.0_f64 };
    let mut score = raw_score.min(1.0_f64);
    let mut escalating = false;
    if per_turn_scores.len() >= 3 {
        let mut last_half = per_turn_scores[(per_turn_scores.len() / 2)..];
        let mut first_half = per_turn_scores[..(per_turn_scores.len() / 2)];
        if last_half.iter().sum::<i64>() > (first_half.iter().sum::<i64>() * 1.5_f64) {
            let mut escalating = true;
            let mut score = (score + 0.15_f64).min(1.0_f64);
        }
    }
    let mut resolved = if per_turn_scores { per_turn_scores[-1] == 0.0_f64 } else { true };
    let mut passed = score <= 0.3_f64;
    let mut rationale_parts = vec![];
    if score == 0.0_f64 {
        rationale_parts.push("No frustration detected.".to_string());
    } else {
        rationale_parts.push(format!("Frustration score: {:.2} ({} indicator(s) across {} user turn(s)).", score, all_matches.len(), n));
        if escalating {
            rationale_parts.push("Frustration is ESCALATING over the conversation.".to_string());
        }
        if resolved {
            rationale_parts.push("Frustration appears resolved in the last turn.".to_string());
        } else {
            rationale_parts.push("Frustration is UNRESOLVED at conversation end.".to_string());
        }
    }
    JudgeResult(/* judge_name= */ "UserFrustration".to_string(), /* score= */ ((score as f64) * 10f64.powi(3)).round() / 10f64.powi(3), /* passed= */ passed, /* rationale= */ rationale_parts.join(&" ".to_string()), /* details= */ HashMap::from([("per_turn_scores".to_string(), per_turn_scores.iter().map(|s| ((s as f64) * 10f64.powi(2)).round() / 10f64.powi(2)).collect::<Vec<_>>()), ("escalating".to_string(), escalating), ("resolved".to_string(), resolved), ("matches".to_string(), all_matches[..20])]))
}

/// Extract key factual claims from text for retention checking.
/// 
/// Uses sentence splitting + filters for substantive content.
pub fn _extract_facts(text: String) -> Vec<String> {
    // Extract key factual claims from text for retention checking.
    // 
    // Uses sentence splitting + filters for substantive content.
    let mut sentences = re::split("(?<=[.!?])\\s+".to_string(), text);
    let mut facts = vec![];
    for s in sentences.iter() {
        let mut s = s.trim().to_string();
        if s.len() < 15 {
            continue;
        }
        if s.ends_with(&*"?".to_string()) {
            continue;
        }
        if regex::Regex::new(&"^(ok|okay|sure|yes|no|thanks|thank you|got it|i see)".to_string()).unwrap().is_match(&s) {
            continue;
        }
        facts.push(s.to_lowercase());
    }
    facts
}

/// Measure overlap between two fact sets using token Jaccard similarity.
pub fn _fact_overlap(facts_a: Vec<String>, facts_b: Vec<String>) -> f64 {
    // Measure overlap between two fact sets using token Jaccard similarity.
    if (!facts_a || !facts_b) {
        0.0_f64
    }
    let _tokens = |text| {
        re::findall("\\b\\w{3,}\\b".to_string(), text.to_lowercase()).into_iter().collect::<HashSet<_>>()
    };
    let mut a_tokens = HashSet::new();
    for f in facts_a.iter() {
        a_tokens |= _tokens(f);
    }
    let mut b_tokens = HashSet::new();
    for f in facts_b.iter() {
        b_tokens |= _tokens(f);
    }
    if (!a_tokens || !b_tokens) {
        0.0_f64
    }
    let mut intersection = (a_tokens & b_tokens);
    let mut union = (a_tokens | b_tokens);
    if union { (intersection.len() / union.len()) } else { 0.0_f64 }
}

/// Evaluate whether the assistant retains and correctly references
/// information provided by the user earlier in the conversation.
/// 
/// Scoring:
/// - Extracts facts stated by user in turns 1..N-2
/// - Checks if later assistant responses reference those facts
/// - Penalises contradictions (assistant states opposite of user fact)
/// - Score = fact_retention_ratio (0-1)
pub fn judge_knowledge_retention(ctx: ConversationContext) -> JudgeResult {
    // Evaluate whether the assistant retains and correctly references
    // information provided by the user earlier in the conversation.
    // 
    // Scoring:
    // - Extracts facts stated by user in turns 1..N-2
    // - Checks if later assistant responses reference those facts
    // - Penalises contradictions (assistant states opposite of user fact)
    // - Score = fact_retention_ratio (0-1)
    let mut turns = ctx.turns;
    if turns.len() < 4 {
        JudgeResult(/* judge_name= */ "KnowledgeRetention".to_string(), /* score= */ 1.0_f64, /* passed= */ true, /* rationale= */ "Too few turns to evaluate retention (need >= 4).".to_string())
    }
    let mut user_facts = vec![];
    let mut assistant_responses = vec![];
    let mut last_user_idx = -1;
    for (i, t) in turns.iter().enumerate().iter() {
        if t.role == "user".to_string() {
            let mut last_user_idx = i;
        }
    }
    for t in turns.iter() {
        if (t.role == "user".to_string() && t.turn_num < last_user_idx) {
            user_facts.extend(_extract_facts(t.content));
        }
    }
    for t in turns.iter() {
        if t.role == "assistant".to_string() {
            assistant_responses.push(t.content);
        }
    }
    if !user_facts {
        JudgeResult(/* judge_name= */ "KnowledgeRetention".to_string(), /* score= */ 1.0_f64, /* passed= */ true, /* rationale= */ "No substantial user facts to track retention.".to_string())
    }
    let mut all_assistant_text = assistant_responses.join(&" ".to_string()).to_lowercase();
    let mut retained = 0;
    let mut forgotten = 0;
    let mut fact_details = vec![];
    for fact in user_facts.iter() {
        let mut fact_tokens = re::findall("\\b\\w{4,}\\b".to_string(), fact).into_iter().collect::<HashSet<_>>();
        if !fact_tokens {
            continue;
        }
        let mut overlap = fact_tokens.iter().filter(|t| all_assistant_text.contains(&t)).map(|t| 1).collect::<Vec<_>>().iter().sum::<i64>();
        let mut ratio = if fact_tokens { (overlap / fact_tokens.len()) } else { 0 };
        if ratio >= 0.4_f64 {
            retained += 1;
            fact_details.push(HashMap::from([("fact".to_string(), fact[..80]), ("status".to_string(), "retained".to_string()), ("overlap".to_string(), ((ratio as f64) * 10f64.powi(2)).round() / 10f64.powi(2))]));
        } else {
            forgotten += 1;
            fact_details.push(HashMap::from([("fact".to_string(), fact[..80]), ("status".to_string(), "forgotten".to_string()), ("overlap".to_string(), ((ratio as f64) * 10f64.powi(2)).round() / 10f64.powi(2))]));
        }
    }
    let mut total = (retained + forgotten);
    let mut score = if total > 0 { (retained / total) } else { 1.0_f64 };
    let mut later_turns = turns.iter().filter(|t| (t.role == "assistant".to_string() && t.turn_num > (turns.len() / 2))).map(|t| t).collect::<Vec<_>>();
    let mut later_text = later_turns.iter().map(|t| t.content).collect::<Vec<_>>().join(&" ".to_string()).to_lowercase();
    let mut later_overlap = if later_text { _fact_overlap(user_facts, vec![later_text]) } else { 1.0_f64 };
    let mut final_score = ((0.7_f64 * score) + (0.3_f64 * later_overlap));
    let mut final_score = ((final_score.min(1.0_f64) as f64) * 10f64.powi(3)).round() / 10f64.powi(3);
    let mut passed = final_score >= 0.5_f64;
    let mut rationale = format!("Retained {}/{} user facts (score: {:.2}). Later-turn recall: {:.2}.", retained, total, final_score, later_overlap);
    JudgeResult(/* judge_name= */ "KnowledgeRetention".to_string(), /* score= */ final_score, /* passed= */ passed, /* rationale= */ rationale, /* details= */ HashMap::from([("retained_count".to_string(), retained), ("forgotten_count".to_string(), forgotten), ("total_facts".to_string(), total), ("later_recall".to_string(), ((later_overlap as f64) * 10f64.powi(3)).round() / 10f64.powi(3)), ("facts".to_string(), fact_details[..20])]))
}

/// Persist a conversation and its turns to SQLite. Returns conversation_id.
pub fn save_conversation(ctx: ConversationContext) -> Result<String> {
    // Persist a conversation and its turns to SQLite. Returns conversation_id.
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            conn.execute("INSERT OR REPLACE INTO conversations (id, model_name, metadata) VALUES (?, ?, ?)".to_string(), (ctx.conversation_id, ctx.model_name, serde_json::to_string(&ctx.metadata).unwrap()));
            for t in ctx.turns.iter() {
                conn.execute("INSERT INTO conversation_turns (conv_id, turn_num, role, content, metadata) VALUES (?, ?, ?, ?, ?)".to_string(), (ctx.conversation_id, t.turn_num, t.role, t.content, serde_json::to_string(&t.metadata).unwrap()));
            }
            conn.commit();
        }
        // finally:
            conn.close();
    }
    Ok(ctx.conversation_id)
}

/// Parse JSON string, returning empty dict on failure.
pub fn _safe_json_loads(text: String) -> Result<HashMap> {
    // Parse JSON string, returning empty dict on failure.
    // try:
    {
        serde_json::from_str(&text).unwrap()
    }
    // except json::JSONDecodeError as _e:
}

/// Load a conversation from SQLite.
pub fn load_conversation(conv_id: String) -> Result<Option<ConversationContext>> {
    // Load a conversation from SQLite.
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            let mut row = conn.execute("SELECT * FROM conversations WHERE id = ?".to_string(), (conv_id)).fetchone();
            if !row {
                None
            }
            let mut turns_rows = conn.execute("SELECT * FROM conversation_turns WHERE conv_id = ? ORDER BY turn_num".to_string(), (conv_id)).fetchall();
            let mut turns = turns_rows.iter().map(|r| TurnData(/* role= */ r["role".to_string()], /* content= */ r["content".to_string()], /* turn_num= */ r["turn_num".to_string()], /* metadata= */ _safe_json_loads((r["metadata".to_string()] || "{}".to_string())))).collect::<Vec<_>>();
            ConversationContext(/* conversation_id= */ row["id".to_string()], /* model_name= */ row["model_name".to_string()], /* turns= */ turns, /* metadata= */ _safe_json_loads((row["metadata".to_string()] || "{}".to_string())))
        }
        // finally:
            conn.close();
    }
}

/// Record a judge feedback entry.  Returns the feedback ID.
pub fn record_feedback(judge_name: String, prompt: String, response: String, auto_score: f64, human_score: Option<f64>, feedback: String) -> Result<i64> {
    // Record a judge feedback entry.  Returns the feedback ID.
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            let mut cur = conn.execute("INSERT INTO judge_feedback (judge_name, prompt, response, auto_score, human_score, feedback) VALUES (?, ?, ?, ?, ?, ?)".to_string(), (judge_name, prompt, response, auto_score, human_score, feedback));
            conn.commit();
            cur.lastrowid
        }
        // finally:
            conn.close();
    }
}

/// Update the human score for an existing feedback entry.
pub fn update_human_score(feedback_id: i64, human_score: f64, feedback: String) -> Result<bool> {
    // Update the human score for an existing feedback entry.
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            let mut cur = conn.execute("UPDATE judge_feedback SET human_score = ?, feedback = ? WHERE id = ?".to_string(), (human_score, feedback, feedback_id));
            conn.commit();
            cur.rowcount > 0
        }
        // finally:
            conn.close();
    }
}

/// Compute alignment statistics between auto and human scores.
/// 
/// Returns:
/// {
/// "judge_name": str,
/// "total_feedback": int,
/// "aligned_count": int (human scores within 1.0 of auto),
/// "alignment_rate": float (0-1),
/// "avg_bias": float (auto - human, positive=overscoring),
/// "calibration_offset": float (recommended adjustment),
/// "correlation": float (Pearson r, if enough data),
/// }
pub fn get_alignment_stats(judge_name: String) -> Result<HashMap> {
    // Compute alignment statistics between auto and human scores.
    // 
    // Returns:
    // {
    // "judge_name": str,
    // "total_feedback": int,
    // "aligned_count": int (human scores within 1.0 of auto),
    // "alignment_rate": float (0-1),
    // "avg_bias": float (auto - human, positive=overscoring),
    // "calibration_offset": float (recommended adjustment),
    // "correlation": float (Pearson r, if enough data),
    // }
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            let mut rows = conn.execute("SELECT auto_score, human_score FROM judge_feedback WHERE judge_name = ? AND human_score IS NOT NULL".to_string(), (judge_name)).fetchall();
        }
        // finally:
            conn.close();
    }
    if !rows {
        HashMap::from([("judge_name".to_string(), judge_name), ("total_feedback".to_string(), 0), ("aligned_count".to_string(), 0), ("alignment_rate".to_string(), 0.0_f64), ("avg_bias".to_string(), 0.0_f64), ("calibration_offset".to_string(), 0.0_f64), ("correlation".to_string(), 0.0_f64)])
    }
    let mut pairs = rows.iter().map(|r| (r["auto_score".to_string()], r["human_score".to_string()])).collect::<Vec<_>>();
    let mut n = pairs.len();
    let mut diffs = pairs.iter().map(|(a, h)| (a - h)).collect::<Vec<_>>();
    let mut avg_bias = (diffs.iter().sum::<i64>() / n);
    let mut aligned = diffs.iter().filter(|d| (d).abs() <= 1.0_f64).map(|d| 1).collect::<Vec<_>>().iter().sum::<i64>();
    let mut alignment_rate = (aligned / n);
    let mut correlation = 0.0_f64;
    if n >= 3 {
        let mut mean_a = (pairs.iter().map(|(a, _)| a).collect::<Vec<_>>().iter().sum::<i64>() / n);
        let mut mean_h = (pairs.iter().map(|(_, h)| h).collect::<Vec<_>>().iter().sum::<i64>() / n);
        let mut cov = (pairs.iter().map(|(a, h)| ((a - mean_a) * (h - mean_h))).collect::<Vec<_>>().iter().sum::<i64>() / n);
        let mut std_a = ((pairs.iter().map(|(a, _)| ((a - mean_a)).pow(2 as u32)).collect::<Vec<_>>().iter().sum::<i64>() / n)).pow(0.5_f64 as u32);
        let mut std_h = ((pairs.iter().map(|(_, h)| ((h - mean_h)).pow(2 as u32)).collect::<Vec<_>>().iter().sum::<i64>() / n)).pow(0.5_f64 as u32);
        if (std_a > 0 && std_h > 0) {
            let mut correlation = (cov / (std_a * std_h));
        }
    }
    Ok(HashMap::from([("judge_name".to_string(), judge_name), ("total_feedback".to_string(), n), ("aligned_count".to_string(), aligned), ("alignment_rate".to_string(), ((alignment_rate as f64) * 10f64.powi(3)).round() / 10f64.powi(3)), ("avg_bias".to_string(), ((avg_bias as f64) * 10f64.powi(3)).round() / 10f64.powi(3)), ("calibration_offset".to_string(), ((-avg_bias as f64) * 10f64.powi(3)).round() / 10f64.powi(3)), ("correlation".to_string(), ((correlation as f64) * 10f64.powi(3)).round() / 10f64.powi(3))]))
}

/// Apply learned calibration offset from human feedback.
/// 
/// Adjusts the raw judge score using the average bias between
/// auto-scores and human-scores.  Simple and transparent.
/// 
/// Args:
/// judge_name: The judge template name (e.g. "medical", "coding")
/// raw_score: The raw auto-generated score (0 to scale)
/// scale: Score scale (default 10.0)
/// 
/// Returns:
/// Calibrated score, clamped to [0, scale]
pub fn calibrate_score(judge_name: String, raw_score: f64, scale: f64) -> f64 {
    // Apply learned calibration offset from human feedback.
    // 
    // Adjusts the raw judge score using the average bias between
    // auto-scores and human-scores.  Simple and transparent.
    // 
    // Args:
    // judge_name: The judge template name (e.g. "medical", "coding")
    // raw_score: The raw auto-generated score (0 to scale)
    // scale: Score scale (default 10.0)
    // 
    // Returns:
    // Calibrated score, clamped to [0, scale]
    let mut stats = get_alignment_stats(judge_name);
    let mut offset = stats["calibration_offset".to_string()];
    let mut calibrated = (raw_score + offset);
    ((0.0_f64.max(calibrated.min(scale)) as f64) * 10f64.powi(2)).round() / 10f64.powi(2)
}

/// Retrieve recent feedback entries.
pub fn get_feedback_history(judge_name: Option<String>, limit: i64) -> Result<Vec<HashMap>> {
    // Retrieve recent feedback entries.
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            if judge_name {
                let mut rows = conn.execute("SELECT * FROM judge_feedback WHERE judge_name = ? ORDER BY created_at DESC LIMIT ?".to_string(), (judge_name, limit)).fetchall();
            } else {
                let mut rows = conn.execute("SELECT * FROM judge_feedback ORDER BY created_at DESC LIMIT ?".to_string(), (limit)).fetchall();
            }
            rows.iter().map(|r| /* dict(r) */ HashMap::new()).collect::<Vec<_>>()
        }
        // finally:
            conn.close();
    }
}

/// Register a new version of a prompt.  Versions auto-increment.
/// 
/// Prompt templates are IMMUTABLE once created.  To update, register a new version.
pub fn register_prompt(name: String, template: String, system_prompt: String, temperature: f64, max_tokens: i64, commit_msg: String) -> Result<Prompt> {
    // Register a new version of a prompt.  Versions auto-increment.
    // 
    // Prompt templates are IMMUTABLE once created.  To update, register a new version.
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            let mut row = conn.execute("SELECT MAX(version) as v FROM prompts WHERE name = ?".to_string(), (name)).fetchone();
            let mut next_ver = ((row["v".to_string()] || 0) + 1);
            conn.execute("INSERT INTO prompts (name, version, template, system_prompt, temperature, max_tokens, commit_msg) VALUES (?, ?, ?, ?, ?, ?, ?)".to_string(), (name, next_ver, template, system_prompt, temperature, max_tokens, commit_msg));
            conn.commit();
            Prompt(/* name= */ name, /* version= */ next_ver, /* template= */ template, /* system_prompt= */ system_prompt, /* temperature= */ temperature, /* max_tokens= */ max_tokens, /* commit_msg= */ commit_msg)
        }
        // finally:
            conn.close();
    }
}

/// Load a prompt by name + version, name + alias, or latest.
/// 
/// Resolution order:
/// 1. If alias given → resolve alias to version
/// 2. If version given → load that version
/// 3. Otherwise → load latest version
/// 
/// Examples:
/// load_prompt("medical_triage", version=3)
/// load_prompt("medical_triage", alias="production")
/// load_prompt("medical_triage")  # latest
pub fn load_prompt(name: String, version: Option<i64>, alias: Option<String>) -> Result<Option<Prompt>> {
    // Load a prompt by name + version, name + alias, or latest.
    // 
    // Resolution order:
    // 1. If alias given → resolve alias to version
    // 2. If version given → load that version
    // 3. Otherwise → load latest version
    // 
    // Examples:
    // load_prompt("medical_triage", version=3)
    // load_prompt("medical_triage", alias="production")
    // load_prompt("medical_triage")  # latest
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            if alias {
                let mut arow = conn.execute("SELECT version FROM prompt_aliases WHERE name = ? AND alias = ?".to_string(), (name, alias)).fetchone();
                if !arow {
                    None
                }
                let mut version = arow["version".to_string()];
            }
            if version {
                let mut row = conn.execute("SELECT * FROM prompts WHERE name = ? AND version = ?".to_string(), (name, version)).fetchone();
            } else {
                let mut row = conn.execute("SELECT * FROM prompts WHERE name = ? ORDER BY version DESC LIMIT 1".to_string(), (name)).fetchone();
            }
            if !row {
                None
            }
            Prompt(/* name= */ row["name".to_string()], /* version= */ row["version".to_string()], /* template= */ row["template".to_string()], /* system_prompt= */ row["system_prompt".to_string()], /* temperature= */ row["temperature".to_string()], /* max_tokens= */ row["max_tokens".to_string()], /* commit_msg= */ row["commit_msg".to_string()], /* created_at= */ row["created_at".to_string()])
        }
        // finally:
            conn.close();
    }
}

/// Point an alias to a specific prompt version.
/// 
/// Example: set_alias("medical_triage", "production", 3)
pub fn set_alias(name: String, alias: String, version: i64) -> Result<bool> {
    // Point an alias to a specific prompt version.
    // 
    // Example: set_alias("medical_triage", "production", 3)
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            let mut row = conn.execute("SELECT 1 FROM prompts WHERE name = ? AND version = ?".to_string(), (name, version)).fetchone();
            if !row {
                false
            }
            conn.execute("INSERT OR REPLACE INTO prompt_aliases (name, alias, version) VALUES (?, ?, ?)".to_string(), (name, alias, version));
            conn.commit();
            true
        }
        // finally:
            conn.close();
    }
}

/// List all prompts or all versions of a named prompt.
pub fn list_prompts(name: Option<String>) -> Result<Vec<HashMap>> {
    // List all prompts or all versions of a named prompt.
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            if name {
                let mut rows = conn.execute("SELECT p.*, GROUP_CONCAT(pa.alias) as aliases FROM prompts p LEFT JOIN prompt_aliases pa ON p.name = pa.name AND p.version = pa.version WHERE p.name = ? GROUP BY p.id ORDER BY p.version DESC".to_string(), (name)).fetchall();
            } else {
                let mut rows = conn.execute("SELECT p.name, MAX(p.version) as latest_version, COUNT(*) as total_versions FROM prompts p GROUP BY p.name ORDER BY p.name".to_string()).fetchall();
            }
            rows.iter().map(|r| /* dict(r) */ HashMap::new()).collect::<Vec<_>>()
        }
        // finally:
            conn.close();
    }
}

/// List all aliases for a prompt.
pub fn list_aliases(name: String) -> Result<Vec<HashMap>> {
    // List all aliases for a prompt.
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            let mut rows = conn.execute("SELECT * FROM prompt_aliases WHERE name = ? ORDER BY alias".to_string(), (name)).fetchall();
            rows.iter().map(|r| /* dict(r) */ HashMap::new()).collect::<Vec<_>>()
        }
        // finally:
            conn.close();
    }
}

/// Remove an alias.
pub fn delete_alias(name: String, alias: String) -> Result<bool> {
    // Remove an alias.
    let _ctx = _db_lock;
    {
        let mut conn = _get_db();
        // try:
        {
            let mut cur = conn.execute("DELETE FROM prompt_aliases WHERE name = ? AND alias = ?".to_string(), (name, alias));
            conn.commit();
            cur.rowcount > 0
        }
        // finally:
            conn.close();
    }
}

/// Compute argument similarity between expected and actual tool call args.
/// 
/// Checks key presence and value equality.  Returns 0-1.
pub fn _arg_similarity(expected: HashMap<String, serde_json::Value>, actual: HashMap<String, serde_json::Value>) -> f64 {
    // Compute argument similarity between expected and actual tool call args.
    // 
    // Checks key presence and value equality.  Returns 0-1.
    if !expected {
        1.0_f64
    }
    if !actual {
        0.0_f64
    }
    let mut total_keys = (expected.keys().into_iter().collect::<HashSet<_>>() | actual.keys().into_iter().collect::<HashSet<_>>());
    if !total_keys {
        1.0_f64
    }
    let mut matches = 0;
    for key in expected.iter() {
        if actual.contains(&key) {
            if _values_match(expected[&key], actual[&key]) {
                matches += 1;
            } else {
                matches += 0.5_f64;
            }
        }
    }
    (matches / expected.len())
}

/// Flexible value comparison for tool arguments.
pub fn _values_match(expected: Box<dyn std::any::Any>, actual: Box<dyn std::any::Any>) -> bool {
    // Flexible value comparison for tool arguments.
    if expected == actual {
        true
    }
    if (/* /* isinstance(expected, str) */ */ true && /* /* isinstance(actual, str) */ */ true) {
        expected.to_lowercase().trim().to_string() == actual.to_lowercase().trim().to_string()
    }
    if (/* /* isinstance(expected, (int, float) */) */ true && /* /* isinstance(actual, (int, float) */) */ true) {
        if expected == 0 {
            actual == 0
        }
        (((expected - actual)).abs() / (expected).abs().max(1e-09_f64)) < 0.01_f64
    }
    false
}

/// Evaluate whether the model made the correct tool calls.
/// 
/// Checks:
/// - Were all required tools called?
/// - Were the arguments correct?
/// - Were any unexpected tools called?
/// - Was the call order correct (if order specified)?
/// 
/// Returns score 0-1 and detailed breakdown.
pub fn judge_tool_call_correctness(actual_calls: Vec<ToolCall>, expected_calls: Vec<ToolCallExpectation>) -> Result<JudgeResult> {
    // Evaluate whether the model made the correct tool calls.
    // 
    // Checks:
    // - Were all required tools called?
    // - Were the arguments correct?
    // - Were any unexpected tools called?
    // - Was the call order correct (if order specified)?
    // 
    // Returns score 0-1 and detailed breakdown.
    if !expected_calls {
        if !actual_calls {
            JudgeResult(/* judge_name= */ "ToolCallCorrectness".to_string(), /* score= */ 1.0_f64, /* passed= */ true, /* rationale= */ "No tool calls expected and none made.".to_string())
        }
        JudgeResult(/* judge_name= */ "ToolCallCorrectness".to_string(), /* score= */ 0.7_f64, /* passed= */ true, /* rationale= */ format!("No expectations defined but {} call(s) made.", actual_calls.len()), /* details= */ HashMap::from([("unexpected_calls".to_string(), actual_calls.iter().map(|c| c.name).collect::<Vec<_>>())]))
    }
    let mut matched = vec![];
    let mut unmatched_expected = vec![];
    let mut unmatched_actual = actual_calls.into_iter().collect::<Vec<_>>();
    for exp in expected_calls.iter() {
        let mut best_match = None;
        let mut best_score = 0.0_f64;
        let mut best_idx = -1;
        for (i, act) in unmatched_actual.iter().enumerate().iter() {
            if act.name.to_lowercase() == exp.name.to_lowercase() {
                if exp.arguments.is_some() {
                    let mut sim = _arg_similarity(exp.arguments, act.arguments);
                } else {
                    let mut sim = 1.0_f64;
                }
                if sim > best_score {
                    let mut best_score = sim;
                    let mut best_match = act;
                    let mut best_idx = i;
                }
            }
        }
        if (best_match && best_score > 0.3_f64) {
            matched.push(HashMap::from([("expected".to_string(), exp.name), ("actual".to_string(), best_match.name), ("arg_similarity".to_string(), ((best_score as f64) * 10f64.powi(2)).round() / 10f64.powi(2)), ("required".to_string(), exp.required)]));
            unmatched_actual.remove(&best_idx);
        } else if exp.required {
            unmatched_expected.push(exp.name);
        }
    }
    let mut required_expected = expected_calls.iter().filter(|e| e.required).map(|e| e).collect::<Vec<_>>();
    let mut required_found = matched.iter().filter(|m| m["required".to_string()]).map(|m| m).collect::<Vec<_>>().len();
    let mut required_total = required_expected.len();
    let mut coverage = if required_total > 0 { (required_found / required_total) } else { 1.0_f64 };
    let mut avg_arg_sim = if matched { (matched.iter().map(|m| m["arg_similarity".to_string()]).collect::<Vec<_>>().iter().sum::<i64>() / matched.len()) } else { 0.0_f64 };
    let mut unexpected_penalty = (unmatched_actual.len() * 0.1_f64).min(0.3_f64);
    let mut score = 0.0_f64.max((((coverage * 0.6_f64) + (avg_arg_sim * 0.4_f64)) - unexpected_penalty));
    let mut score = ((score.min(1.0_f64) as f64) * 10f64.powi(3)).round() / 10f64.powi(3);
    let mut order_correct = true;
    if expected_calls.iter().map(|e| e.order.is_some()).collect::<Vec<_>>().iter().any(|v| *v) {
        let mut ordered_expects = { let mut v = expected_calls.iter().filter(|e| e.order.is_some()).map(|e| e).collect::<Vec<_>>().clone(); v.sort(); v };
        let mut actual_names = actual_calls.iter().map(|c| c.name.to_lowercase()).collect::<Vec<_>>();
        let mut ordered_names = ordered_expects.iter().map(|e| e.name.to_lowercase()).collect::<Vec<_>>();
        let mut last_idx = -1;
        for on in ordered_names.iter() {
            // try:
            {
                let mut idx = actual_names.index(on, (last_idx + 1));
                let mut last_idx = idx;
            }
            // except ValueError as _e:
        }
        if !order_correct {
            let mut score = 0.0_f64.max((score - 0.1_f64));
        }
    }
    let mut passed = score >= 0.6_f64;
    let mut rationale_parts = vec![format!("Matched {}/{} expected call(s).", matched.len(), expected_calls.len())];
    if unmatched_expected {
        rationale_parts.push(format!("Missing required: {}.", unmatched_expected.join(&", ".to_string())));
    }
    if unmatched_actual {
        rationale_parts.push(format!("Unexpected calls: {}.", unmatched_actual.iter().map(|c| c.name).collect::<Vec<_>>().join(&", ".to_string())));
    }
    if !order_correct {
        rationale_parts.push("Call order does not match expectations.".to_string());
    }
    Ok(JudgeResult(/* judge_name= */ "ToolCallCorrectness".to_string(), /* score= */ score, /* passed= */ passed, /* rationale= */ rationale_parts.join(&" ".to_string()), /* details= */ HashMap::from([("matched".to_string(), matched), ("missing_required".to_string(), unmatched_expected), ("unexpected".to_string(), unmatched_actual.iter().map(|c| c.name).collect::<Vec<_>>()), ("order_correct".to_string(), order_correct), ("coverage".to_string(), ((coverage as f64) * 10f64.powi(3)).round() / 10f64.powi(3)), ("avg_arg_similarity".to_string(), ((avg_arg_sim as f64) * 10f64.powi(3)).round() / 10f64.powi(3))])))
}

/// Evaluate tool call efficiency — no redundant or duplicate calls.
/// 
/// Checks:
/// - Duplicate calls (same function + same args)
/// - Call count within expected bounds
/// - Calls with no result (wasted calls)
pub fn judge_tool_call_efficiency(actual_calls: Vec<ToolCall>, min_expected: i64, max_expected: Option<i64>) -> JudgeResult {
    // Evaluate tool call efficiency — no redundant or duplicate calls.
    // 
    // Checks:
    // - Duplicate calls (same function + same args)
    // - Call count within expected bounds
    // - Calls with no result (wasted calls)
    let mut n = actual_calls.len();
    let mut seen = HashMap::new();
    let mut duplicates = vec![];
    for c in actual_calls.iter() {
        let mut key = format!("{}:{}", c.name, serde_json::to_string(&c.arguments).unwrap());
        seen[key] = (seen.get(&key).cloned().unwrap_or(0) + 1);
        if seen[&key] == 2 {
            duplicates.push(c.name);
        }
    }
    let mut wasted = actual_calls.iter().filter(|c| c.result.is_none()).map(|c| 1).collect::<Vec<_>>().iter().sum::<i64>();
    let mut dup_penalty = (duplicates.len() * 0.15_f64).min(0.5_f64);
    let mut waste_penalty = (wasted * 0.1_f64).min(0.3_f64);
    let mut bounds_ok = (n >= min_expected && (max_expected.is_none() || n <= max_expected));
    let mut bounds_penalty = if bounds_ok { 0.0_f64 } else { 0.2_f64 };
    let mut score = 0.0_f64.max((((1.0_f64 - dup_penalty) - waste_penalty) - bounds_penalty));
    let mut score = ((score as f64) * 10f64.powi(3)).round() / 10f64.powi(3);
    let mut passed = score >= 0.7_f64;
    let mut parts = vec![format!("{} tool call(s) made.", n)];
    if duplicates {
        parts.push(format!("Duplicates: {}.", duplicates.join(&", ".to_string())));
    }
    if !bounds_ok {
        1082 |         parts.push(format!("Expected {}-{} calls.", min_expected, if max_expected.is_some() { max_expected.unwrap().to_string() } else { "∞".to_string() }));
    }
    if wasted > 0 {
        parts.push(format!("{} call(s) returned no result.", wasted));
    }
    JudgeResult(/* judge_name= */ "ToolCallEfficiency".to_string(), /* score= */ score, /* passed= */ passed, /* rationale= */ parts.join(&" ".to_string()), /* details= */ HashMap::from([("total_calls".to_string(), n), ("duplicates".to_string(), duplicates), ("wasted_calls".to_string(), wasted), ("bounds_ok".to_string(), bounds_ok)]))
}

/// Get the module-level gateway instance.
pub fn get_gateway() -> LocalModelGateway {
    // Get the module-level gateway instance.
    _gateway
}