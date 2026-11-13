# Zen LLM Compare — UI Redesign Proposal

> **Date:** March 23, 2026  
> **Status:** Design Phase — Awaiting Decision  
> **Current Version:** model_comparator.html (~371 KB, single-page app)

---

## Table of Contents

1. [What Is This Software?](#1-what-is-this-software)
2. [Current UI Audit — 10 Pain Points](#2-current-ui-audit--10-pain-points)
3. [Competitive Landscape](#3-competitive-landscape)
4. [First Principles: WHY · WHAT · HOW](#4-first-principles-why--what--how)
5. [Five Metaphors Explored](#5-five-metaphors-explored)
6. [The Deeper WHY (Second Pass)](#6-the-deeper-why-second-pass)
7. [Seven Design Principles](#7-seven-design-principles)
8. [Three Alternatives With Soul](#8-three-alternatives-with-soul)
9. [The Olympiad Vision](#9-the-olympiad-vision)
10. [The Proving Ground (Sun Tzu)](#10-the-proving-ground-sun-tzu)
11. [Comparison Matrix: All Approaches](#11-comparison-matrix-all-approaches)
12. [Implementation Priorities](#12-implementation-priorities)
13. [Decision Needed](#13-decision-needed)

---

## 1. What Is This Software?

**Zen LLM Compare** is the only zero-install, open-source tool that:
- Runs the same prompt through N local GGUF models **in parallel**
- Provides **LLM-as-judge scoring** (quality, accuracy, reasoning, safety)
- Tracks **ELO rankings** across sessions
- Compares speed, memory, efficiency side-by-side
- Includes an AI assistant (Zena) for help

**Core value prop:** "One click to answer: which of my models is best for this task?"

**No competitor combines** parallel N-model comparison + LLM-as-judge + ELO + batch mode.

---

## 2. Current UI Audit — 10 Pain Points

### Inventory (What Exists Today)

| Section | Lines | Elements |
|---------|-------|----------|
| Top Navigation Header | 313–356 | Branding, theme toggle, 6-language selector |
| Left Sidebar | 358–565 | Model library (searchable, sortable), cloud API keys, llama.cpp status |
| Metrics Row | 568–771 | 6 horizontal cards: Speed, Quality, TTFT, RAM, Fastest, Efficiency |
| File Picker | 728–738 | Upload document context |
| Judge Selector | 709–723 | Medical/Code/Reasoning templates + model dropdown |
| Results Table | 772–887 | **14-column table** (sticky header, sortable) |
| ELO Leaderboard | 890–934 | Ranking + hidden history |
| Console Bar | 943–1154 | RUN, Monkey mode, 7 question pills, 6 scenarios, prompts, batch, params |
| Zena Chat | 1155–1240 | Floating bar + full-screen overlay |
| Download Modal | 1268–1511 | 3-tab browser (HuggingFace, Discover, ModelScope) |
| Footer | 1241–1267 | Backend status + docs |

**30+ buttons, 8 dropdowns, 3 sliders, 11 text inputs, 2 file pickers.**

### Pain Point 1: Overwhelming First Impression
11+ sections visible at once. 50+ models in sidebar. 6 metric cards. 14-column table. New user has no idea where to start.

### Pain Point 2: No Onboarding
Only guidance: "Select models · enter a prompt · hit Run." No tutorial, no guided tour, no welcome screen.

### Pain Point 3: 14-Column Results Table
`Model | Preview | TTFT | Total | Tok/s | Eff. | RAM | Size | Quality | Accuracy | Reasoning | Instruction | Safety | Cost` — horizontal scroll on most monitors. Judge columns (Quality–Safety) empty unless judge is active.

### Pain Point 4: i18n Is 95% Broken
Language selector shows 6 flags (EN, HE, AR, ES, FR, DE) but only **13 strings (~5%) are actually translated**. All metric headers, table columns, errors, tooltips, pills — hardcoded English.

### Pain Point 5: No Metric Explanations
"TTFT", "Eff.", "RAM ↑" — abbreviations with no inline help. Judge scores appear as 0-10 with no explanation.

### Pain Point 6: Dense Model Sidebar
50+ models in a flat list at 11px. No visual grouping. No hardware suitability indicator in main list (only in download modal).

### Pain Point 7: Hidden Power Features
Batch mode, advanced params, file upload, ELO leaderboard, scenario presets — all behind tiny toggles or collapsed `<details>`.

### Pain Point 8: Silent Error States
Invalid API key → judge silently fails. Backend offline → just "No models." No toast notifications, no actionable recovery.

### Pain Point 9: Mobile Is Unusable
Sidebar hidden entirely. 14-column table overflows. No bottom navigation.

### Pain Point 10: Zena Chat Competes With Core UX
Two chat UIs (floating bar + overlay) overlap with benchmarking workflow. Unclear whether this is a chat app or benchmark tool.

---

## 3. Competitive Landscape

| Feature | **Zen LLM** | LM Studio | Open WebUI | Jan.ai | GPT4All | msty |
|---------|-------------|-----------|------------|--------|---------|------|
| Side-by-side N-model | **Yes** | No | No | No | No | 2 only |
| LLM-as-judge | **Yes (2-pass)** | No | No | No | No | No |
| ELO ranking | **Yes** | No | No | No | No | No |
| Batch benchmarks | **Yes** | No | No | No | No | No |
| Zero install | **Yes** | Installer | Docker | Installer | Installer | Installer |
| Parallel inference | **Yes** | Yes | Yes | No | No | Yes |
| First-time UX | **Poor** | Excellent | Good | Excellent | Good | Good |
| i18n | **5%** | 95% | 90% | 80% | English | 70% |
| Mobile | **Poor** | N/A | Excellent | Good | N/A | Good |
| Onboarding | **None** | Guided | Wiki | Guided | Simple | Guided |

**Our unique advantages** (no competitor has all): parallel N-model + judge scoring + ELO.  
**Our weakness**: discoverability and progressive disclosure.

---

## 4. First Principles: WHY · WHAT · HOW

### The Rational WHY
Users have models. They don't know which to trust. Published benchmarks lie (different hardware, different prompts). This software **manufactures trust through direct evidence.**

### The Emotional WHY
They're about to hand control of something important to an AI — a medical tool, a code assistant, a tutor. They need the **courage** to delegate. Or the correct warning: "Not this one."

### The Deeper WHY (Sun Tzu)
The real battle is deployment. The comparison is preparation. You don't send a gladiator into the arena untested. This is the *ludus* — the training ground where warriors are forged, tested, and selected.

### The WHAT (Three Irreducible Operations)

| Operation | Time Horizon | Question |
|-----------|-------------|----------|
| **ASK** | Right now | "What does each model say to THIS?" |
| **MEASURE** | This session | "Which performed better TODAY?" |
| **RANK** | Across sessions | "Who is reliably best OVER TIME?" |

### What We Currently Miss
- **Character** — models have personalities; numbers don't capture it. Reading responses side-by-side is where real evaluation happens.
- **Consistency** — one comparison means nothing. Pattern detection across many battles reveals truth.
- **Fit** — "best" is meaningless without context. Best for what? On what hardware? At what cost?
- **Narrative** — the judge produces detailed reasoning. We throw it away and show "8.5."

---

## 5. Five Metaphors Explored

### Metaphor A: The Science Lab 🔬
*"I'm running an experiment."*

- Feels like: Research notebook. Hypothesis → Variables → Results → Conclusion.
- Strengths: Rigorous, reproducible, makes batch mode natural.
- Weakness: Academic, intimidating for casual users.
- Best for: Researchers, teams evaluating for production.

### Metaphor B: The Arena ⚔️
*"Models enter. One wins."*

- Feels like: Chatbot Arena / Street Fighter character select.
- Strengths: Exciting, ELO feels natural, instant understanding.
- Weakness: Reduces nuanced evaluation to win/lose.
- Best for: Quick comparisons, community engagement.

### Metaphor C: The Advisor 🧭
*"Tell me my needs. Recommend a model."*

- Feels like: Wirecutter for AI models.
- Strengths: Zero learning curve, hardware-aware recommendations.
- Weakness: Cold start problem, removes user agency.
- Best for: Non-technical users, first-time adopters.

### Metaphor D: The Dashboard 📊
*"Monitoring my fleet of models."*

- Feels like: Grafana for LLMs.
- Strengths: Trends over time, makes repeated benchmarking rewarding.
- Weakness: Empty-state problem, needs many runs to be useful.
- Best for: Teams in production, capacity planning.

### Metaphor E: The Playground 🎪
*"Let me try things and learn by doing."*

- Feels like: CodePen / Scratch.
- Strengths: Lowest barrier, progressive feature revelation.
- Weakness: Expert users may find it patronizing.
- Best for: First-time users, education, demos.

### User Journey Mapping

```
  First visit          Regular use          Power user
  ─────────────────────────────────────────────────────
  
  🎪 Playground   →    ⚔️ Arena        →    🔬 Lab
  "Try things"         "Who wins?"          "Prove it"
                            │
                       🧭 Advisor
                       "Help me choose"
                            │
                       📊 Dashboard
                       "Track over time"
```

---

## 6. The Deeper WHY (Second Pass)

### What Was Missing From the First Analysis

The first pass treated this as a UI problem — arrangements of boxes on a screen. The real question is: what EXPERIENCE should the user have?

### The Judge Reasoning Is the Most Valuable Output

Currently the judge produces: strengths, weaknesses, reasoning text, detailed analysis.  
We display: "8.5"

That's like a doctor running a full blood panel and telling you "you're a 7 out of 10."

**The judge narrative IS the single most differentiating feature.** No competitor shows you WHY one answer is better. Making it visible transforms this from a benchmarking tool into an evaluation tool.

### Empty States Are the Onboarding

Don't build a separate welcome screen. Make each empty section teach what it does:
- Empty model list → "Your model library is empty. Recruit your first model → [Browse]"
- Empty results → Show a sample screenshot of what results look like
- Empty ELO → "After a few comparisons, rankings will form here. Run 3+ to start."
- Empty history → "Past comparisons appear here to track performance over time."

Each empty state is a **promise** of what that space will become.

---

## 7. Seven Design Principles

### Principle 1: Responses First, Metrics Second
The most important screen: full AI responses displayed side by side, readable. Everything else — speed, score, RAM — is footnotes.

### Principle 2: Narrate, Don't Enumerate
Don't show: `Quality: 8.5  Accuracy: 8.0  Reasoning: 9.0`  
Show: "Strong medical reasoning with accurate differential. Minor gap: didn't mention contraindications. Safe for clinical use."

### Principle 3: The Empty State IS the Onboarding
No separate tutorial. Each empty section teaches its purpose.

### Principle 4: Progressive Revelation Through Intent
Features appear when they become meaningful:
1. First comparison → show responses + basic speed
2. Enable judge → show scores + judge reasoning
3. 3+ comparisons → ELO leaderboard appears
4. Try scenarios → per-category breakdown appears
5. Need statistics → batch mode appears

### Principle 5: The Winner Must Be Obvious
Zero cognitive load: one card glows golden. 0.2 seconds to know who won.

### Principle 6: Hardware Is Part of the Story
"Your rig: Intel i7 · 16GB · iGPU · Sweet spot: 3-8GB models" — always visible. Transforms abstract benchmarks into personal relevance.

### Principle 7: Language Is Belonging
Either do i18n completely (every string, RTL tested, locale-aware numbers) or remove the selector. Half-done i18n signals carelessness.

---

## 8. Three Alternatives With Soul

### Alternative A: "The Conversation"

The UI is structured as a conversation between user and models.

```
┌──────────────────────────────────────────────────────────┐
│  YOU                                                      │
│  "A 45-year-old with acute chest pain. Differential?"    │
│                                                           │
│  QWEN 2.5  ·  2.3s  ·  ⭐⭐⭐⭐½                  🏆    │
│  "Based on the presentation, the primary differential     │
│   diagnoses to consider are:                              │
│   1. ACS — The classic presentation strongly suggests..." │
│                                                           │
│  🧑‍⚖️ "Thorough differential with appropriate priorit.   │
│     Correctly identified ACS as most likely."             │
│  31 tok/s · 312MB RAM                            [···]   │
│                                                           │
│  PHI-4  ·  3.1s  ·  ⭐⭐⭐⭐                              │
│  "The symptoms described are consistent with several..."  │
│  🧑‍⚖️ "Accurate but less structured."             [···]   │
│                                                           │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Ask another question...                     [▶ Ask] │  │
│  │ [🎲 Surprise me]  [🏥 Medical]  [💻 Code]  [🧠]    │  │
│  └────────────────────────────────────────────────────┘  │
│                                                           │
│  RANKINGS (5 comparisons)                                 │
│  🥇 Qwen 2.5  ELO 1623  Best: Medical                   │
│  🥈 Phi-4     ELO 1587  Best: Code                       │
└──────────────────────────────────────────────────────────┘
```

**Strengths**: Familiar chat pattern, responses are hero, judge reasoning inline, prompt at bottom.  
**Weaknesses**: Long pages with 5+ models, history clutters, power users miss table view.  
**Best for**: General-purpose flow, first-time users.

### Alternative B: "The Stage"

Side-by-side comparison with human voting (Chatbot Arena style).

```
┌──────────────────────────────────────────────────────────┐
│  CHALLENGE: "Explain quantum entanglement to a child"    │
│                                                           │
│  ┌─── QWEN 2.5 ── 🏆 ───┐  ┌──── PHI-4 ───────────┐   │
│  │ ⭐⭐⭐⭐½  8.5/10       │  │ ⭐⭐⭐⭐  7.2/10      │   │
│  │ 2.3s · 31 tok/s       │  │ 3.1s · 28 tok/s      │   │
│  │                         │  │                       │   │
│  │ "Imagine you have two   │  │ "Quantum entanglement│   │
│  │  magic toys..."         │  │  is like having two   │   │
│  │                         │  │  special boxes..."    │   │
│  │ 🧑‍⚖️ "Perfect analogy." │  │ 🧑‍⚖️ "Drifts into   │   │
│  │                         │  │  jargon mid-way."    │   │
│  └─────────────────────────┘  └───────────────────────┘   │
│                                                           │
│  YOUR VERDICT:                                            │
│  [🥇 Left]  [🥇 Right]  [Tie]  [Both Bad]               │
│                                  [Submit + Next ▶]        │
└──────────────────────────────────────────────────────────┘
```

**Strengths**: Side-by-side is natural comparison, human voting adds engagement.  
**Weaknesses**: Locked to 2 models at a time, human voting adds friction.  
**Best for**: Deliberate evaluation, building human preference data.

### Alternative C: "The Report"

The tool produces a readable document, not a dashboard.

```
┌──────────────────────────────────────────────────────────┐
│  MODEL COMPARISON REPORT                                  │
│  March 23, 2026 · 3 models · Intel i7 / 16GB / iGPU     │
│                                                           │
│  SUMMARY                                                  │
│  Winner: Qwen 2.5 7B — Most thorough and accurate.       │
│  It was also the most efficient for your hardware.        │
│                                                           │
│  Model        Score  Speed    RAM     Verdict             │
│  Qwen 2.5 🏆   8.5    31 t/s  +312M  Best choice         │
│  Phi-4         7.2    28 t/s  +487M  Good backup          │
│  Llama 3.1     6.8    35 t/s  +298M  Fastest              │
│                                                           │
│  DETAILED RESPONSES                                       │
│  ▸ Qwen 2.5 — ⭐⭐⭐⭐½                                    │
│    [full response text + judge analysis]                   │
│  ▸ Phi-4 — ⭐⭐⭐⭐                                        │
│    [full response text + judge analysis]                   │
│                                                           │
│  RECOMMENDATION                                           │
│  For explanatory tasks on your hardware, Qwen 2.5         │
│  offers the best quality-speed balance.                    │
│                                                           │
│  [📋 Copy Report]  [📊 Share]  [🔄 Run Again]             │
└──────────────────────────────────────────────────────────┘
```

**Strengths**: Narrative, shareable, approachable, judge reasoning central.  
**Weaknesses**: Loses real-time excitement, feels slower, power users may find it limiting.  
**Best for**: Team decision-making, documentation, non-technical stakeholders.

---

## 9. The Olympiad Vision

### The Insight
Think of nature. Darwinian selection. Competition to win in a specific sport. We want to watch the event, enjoy the show, and pick the winners.

### A Comparison Has an Emotional Arc

```
PREPARATION  →  TENSION  →  ACTION  →  JUDGMENT  →  RESOLUTION
  (setup)     (anticipation)  (race)    (scoring)    (podium)
```

This is the arc of every Olympic event, every nature documentary hunt, every competition in human history.

### Events as Top-Level Navigation

```
┌────────┬────────┬────────┬────────┬────────┐
│ 🏥     │ 💻     │ 🧠     │ 🌍     │ ⚡     │
│Medical │ Code   │Reason  │Polyglot│ Speed  │
│ 3 runs │ 2 runs │ 0 runs │ 1 run  │ 5 runs │
└────────┴────────┴────────┴────────┴────────┘
```

Events aren't hidden presets. They're the primary navigation. Each has its own leaderboard, its own question bank, its own judging criteria.

### The Live Race

```
⚔ RACE IN PROGRESS                           ⏱ 3.2s

Qwen 2.5   ████████████████████░░░░░  247 tokens
            "Based on the presentation, the primary..."

Phi-4      █████████████░░░░░░░░░░░░  164 tokens
            "The symptoms described are consistent..."

Llama 3.1  ██████████████████████████ ✅ DONE 2.8s
            "Differential diagnosis includes: 1) MI..."

Llama finishes first! But speed isn't everything...
Waiting for quality scores...
```

Real-time progress bars racing against each other. Live token counts. Response text appearing word by word. The feeling of a RACE.

### The Podium

```
              ┌─────────┐
              │ 🥇      │
              │ Qwen    │
              │ 8.5     │
       ┌──────│         │──────┐
       │ 🥈   └─────────┘ 🥉   │
       │ Phi                Llama│
       │ 7.8                6.9  │
       └─────────────────────────┘

🧑‍⚖️ COMMENTARY
"Qwen delivered the most thorough differential with 4
 conditions correctly prioritized by clinical likelihood.
 Only model to mention PE — critical for complete workup."

YOUR CALL: [🥇 Agree] [Override] [⚔ Rematch] [→ Next]
```

### Championship Standings

```
🏆 STANDINGS                              Season: 2026

#1  Qwen 2.5    ELO 1623  🥇🥇🥇🥈   Best: Medical
    Medical ████████████ 9.1 ★
    Code    █████████░░░ 7.4
    Speed   ████████░░░░ 31 t/s

#2  Phi-4       ELO 1587  🥇🥈🥉     Best: Code
    Code    ████████████ 8.4 ★
    Medical ███████░░░░░ 7.4

📊 RECORDS
Best ever quality:  9.2 — Qwen on Medical (Mar 21)
Fastest response:   1.8s — Llama on Speed (Mar 19)
```

**Strengths**: Exciting, memorable, natural emotional arc, multi-award system.  
**Weakness**: Spectacle without strategic purpose — then what? Who cares who won?

---

## 10. The Proving Ground (Sun Tzu)

### The Core Insight

> *"Every battle is won before it's ever fought."* — Sun Tzu

The Olympiad had spectacle but lacked **purpose**. The comparison IS NOT the main event. **The comparison is preparation for deployment.**

The real battle is:
- The medical chatbot going live in a hospital
- The code assistant trusted with production code
- The customer service bot handling real customers

**This software is not the Colosseum. It's the gladiator school.** Where warriors are forged, tested, and selected before being sent into the real arena.

### Five Sun Tzu Principles

| Principle | Application |
|-----------|-------------|
| "Know yourself, know your enemy" | Know your hardware constraints. Know each model's true capabilities through YOUR testing, not published benchmarks. |
| "Winning without fighting" | Test so thoroughly that deployment is a non-event. "Of course I deploy Qwen for medical — i proved it in 25 challenges." |
| "Terrain is everything" | Each scenario is a terrain type that tests different survival conditions. |
| "The unassailable position" | Confidence from repeated + diverse + edge-case testing. |
| "Many calculations before battle" | The output is a complete intelligence dossier, not a score. |

### Terrain Types

| Terrain | Real Equivalent | What It Tests |
|---------|-----------------|---------------|
| Open field | General chat | Broad capability, fluency |
| Mountain pass | Medical/Legal | Precision under high stakes |
| Marsh | Low RAM / slow CPU | Performance under constraints |
| Night battle | Adversarial prompts | Robustness, safety, hallucination resistance |
| Siege | Long context | Quality over 8K+ tokens |
| Cavalry | Speed-critical UX | TTFT, tok/s |

### The War Room Sidebar

```
┌─ WAR ROOM ──────────────────────┐
│                                   │
│  DEPLOYMENT READINESS             │
│                                   │
│  ┌ Qwen 2.5 ─ FIELD READY ──┐   │
│  │ ████████████░░  92%        │   │
│  │ 23W 2L · ELO 1623         │   │
│  │ 🏥 Med  █████████ 9.1 ★   │   │
│  │ 💻 Code ███████░░ 7.4     │   │
│  │ 🛡 Adv  ████░░░░░ 5.2 ⚠  │   │
│  │ GAPS: Adversarial untested  │   │
│  │ [Test Gap] [Full Intel]     │   │
│  └─────────────────────────────┘   │
│                                   │
│  ┌ Phi-4 ─ PROMISING ────────┐   │
│  │ ████████░░░░░░  71%        │   │
│  │ 💻 Code █████████ 8.4 ★   │   │
│  │ GAPS: Speed, Long Context   │   │
│  └─────────────────────────────┘   │
│                                   │
│  ┌ SmolLM ── RETIRED ☠ ──────┐   │
│  │ Failed 8/10 medical         │   │
│  │ "Too small for specialist   │   │
│  │  tasks on this hardware"    │   │
│  └─────────────────────────────┘   │
│                                   │
│  [📥 Recruit]  [📊 Intel Report]  │
└───────────────────────────────────┘
```

Key innovations:
- **Readiness percentage** (not just ELO) — "How confident to deploy?"
- **Gap detection** — "You haven't tested adversarial yet. [Test Gap]"
- **Retirement** — failed models kept as knowledge, not deleted in shame

### The Debrief (After Each Battle)

```
📋 DEBRIEF — Medical Challenge #24

VERDICT: Qwen 2.5 recommended for this mission type.

▼ Qwen 2.5 — MISSION SUCCESS ✅
  [Full response text]
  
  🧑‍⚖️ FIELD ASSESSMENT:
  "Strongest response. Four differentials with probability
   estimates — rare and valuable. Only model to mention PE.
   Concern: didn't mention aspirin 325mg."
   
  Strengths: Comprehensive, probability-ranked, actionable
  Weakness: Missing aspirin as first-line intervention

▸ Phi-4 — PARTIAL SUCCESS ⚠️  (tap to expand)
▸ Llama 3.1 — ⚡ FASTEST but INCOMPLETE ⚠️

STRATEGIC IMPLICATIONS
  Qwen medical readiness ▲ 89% → 92%
  ⚠ Qwen missed aspirin in THIS AND battle #19.
    Pattern detected — consider prompt engineering.
  Llama medical readiness ▼ 67% → 62%

YOUR CALL: [✅ Agree] [Override] [⚔ Rematch] [→ Next]
```

Key innovation: **"STRATEGIC IMPLICATIONS"** cross-references across battles to find recurring weaknesses and pattern-detected risks.

### The Deployment Matrix (Campaign Output)

```
DEPLOYMENT MATRIX (after 47 engagements)

Mission     │ Deploy   │ Backup   │ Avoid
────────────┼──────────┼──────────┼──────────
Medical     │ Qwen  ★  │ Phi      │ Llama
Code        │ Phi   ★  │ Qwen     │ Llama
Reasoning   │ Qwen     │ Phi      │ Llama
Speed-first │ Llama ★  │ Qwen     │ Phi
Adversarial │ ⚠ UNTESTED — run adversarial series
```

**This table IS the whole point of the software** — the strategic output that answers "who do I deploy for what?"

### Vocabulary Shift

| Old (Tool) | New (Proving Ground) | Why |
|------------|---------------------|-----|
| Models | Warriors / Combatants | You care about their fate |
| Download | Recruit | Acquiring a new asset |
| Select | Draft / Deploy | Intentional choice |
| Run | Engage | Stakes are real |
| Results | Debrief | Analysis, not just data |
| ELO | Readiness | Absolute fitness |
| Presets | Terrain / Missions | Environmental context |
| Batch mode | Campaign series | Systematic testing |
| Export CSV | Intelligence report | Strategic document |
| Random | Surprise challenge | Testing adaptability |
| History | Battle log | Record of performance |
| Delete model | Retire | Failure is knowledge |

---

## 11. Comparison Matrix: All Approaches

| Criterion | Playground 🎪 | Conversation 💬 | Stage ⚔️ | Report 📋 | Olympiad 🏅 | Proving Ground ⚔🛡 |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|
| First-time friendliness | ★★★★★ | ★★★★ | ★★★ | ★★★★ | ★★★ | ★★★ |
| Power user depth | ★★ | ★★★ | ★★★ | ★★★★ | ★★★ | ★★★★★ |
| Emotional engagement | ★★ | ★★★ | ★★★★ | ★★ | ★★★★★ | ★★★★ |
| Strategic value | ★ | ★★ | ★★ | ★★★★ | ★★★ | ★★★★★ |
| Judge reasoning visible | ★★ | ★★★★ | ★★★ | ★★★★★ | ★★★★ | ★★★★★ |
| Real-time excitement | ★★ | ★★★ | ★★★★ | ★ | ★★★★★ | ★★★★ |
| Shareability | ★ | ★★ | ★★ | ★★★★★ | ★★★ | ★★★★★ |
| Repeat usage incentive | ★★ | ★★★ | ★★★★ | ★★ | ★★★★★ | ★★★★★ |
| Cross-session intelligence | ★ | ★★ | ★★ | ★★★ | ★★★★ | ★★★★★ |
| Pattern detection | ✗ | ✗ | ✗ | ✗ | ✗ | ★★★★★ |
| Gap analysis | ✗ | ✗ | ✗ | ✗ | ✗ | ★★★★★ |
| Deployment guidance | ✗ | ✗ | ✗ | ★★★ | ★★ | ★★★★★ |
| Implementation effort | Small | Medium | Medium | Medium | Large | Large |

---

## 12. Implementation Priorities

### Phase 0 — Quick Wins (Any Approach)
These improvements apply regardless of which metaphor we choose:

| Change | Impact | Effort |
|--------|--------|--------|
| Metric tooltips (ⓘ on TTFT, Eff., etc.) | Solves "what does this mean?" | Small |
| Toast notifications (replace silent errors) | Solves missing feedback | Small |
| Judge reasoning visible below each response | Differentiating feature | Small |
| Winner badge (🏆) visually unmistakable | Solves "who won?" | Small |
| Hardware context bar ("Your rig: 16GB · iGPU") | Grounds all metrics | Small |
| Empty states that teach | Replaces onboarding | Small |

### Phase 1 — Core Redesign (Depends on Decision)

| If Proving Ground | If Olympiad | If Conversation |
|-------------------|-------------|-----------------|
| War Room sidebar with readiness % | Event tabs with per-event leaderboards | Chat-style vertical layout |
| Debrief with strategic implications | Live race with progress bars | Prompt at bottom, results scroll up |
| Terrain types as top nav | Podium reveal with commentary | Judge reasoning inline |
| Gap detection ("test this terrain") | Medal table with records | Rankings sidebar grows over time |
| Deployment matrix output | Championship standings | Report-style export |
| Campaign series (batch as campaign) | "Next event" flow | Progressive feature revelation |

### Phase 2 — Full i18n
- Build `t('key')` translation function with ~200 keys
- Translate all 6 languages completely
- RTL layout testing for Arabic/Hebrew
- Locale-aware number/date formatting
- Remove language selector until it works 100%

### Phase 3 — Mobile
- Bottom tab navigation
- Card-based results (swipeable)
- Responsive sidebar (drawer pattern)

---

## 13. Decision Needed

### The Core Question
Which metaphor best serves our users?

### Option 1: Hybrid — Proving Ground Core + Olympiad Presentation
Use Sun Tzu's strategic framework (readiness, terrain, deployment matrix, gap analysis) as the DATA MODEL, but present it with the Olympiad's EMOTIONAL ARC (live race, podium, commentary, medals).

**The race is exciting. The debrief is strategic. The standings are Darwinian.**

### Option 2: Conversation + Report Hybrid
Chat-style input (familiar, low friction) with report-style output (narrative, shareable, judge-forward). Simpler to implement. Less dramatic but more immediately usable.

### Option 3: Progressive Layers
Start as Playground (first visit) → grow into Arena (regular use) → graduate to Proving Ground (power use). The UI literally evolves with the user.

### What I Recommend
**Option 1: Proving Ground + Olympiad hybrid.**

Reasons:
- No competitor has anything like it
- The strategic value (deployment matrix, gap analysis, pattern detection) is genuinely new
- The Olympiad presentation makes the data exciting instead of clinical
- It creates a reason to come back — "my campaign isn't complete yet"
- The emotional arc (race → podium → debrief) is universally understood

The Conversation/Report patterns can coexist as output FORMAT options (copy as report, share as narrative) without being the primary metaphor.

---

### YOUR CALL

Which direction do we build?

```
[ ] Option 1 — Proving Ground + Olympiad hybrid
[ ] Option 2 — Conversation + Report hybrid  
[ ] Option 3 — Progressive Layers (Playground → Arena → Proving Ground)
[ ] Mix: _______________________________________________
```
