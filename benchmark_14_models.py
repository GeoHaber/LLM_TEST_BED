"""Golden Benchmark — 14 models through LLM_TEST_BED comparator backend.

Runs through the real /__comparison endpoint at localhost:8123 with judge
scoring, then formats a ranked results table.

Usage:
    python benchmark_14_models.py
"""

import json
import sys
import time
import urllib.request

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BACKEND = "http://127.0.0.1:8123"

# ── Select 14 models (skip mmproj, ggml-model, duplicates) ──────────────────

SKIP = ("mmproj", "ggml-model-i2", "Mistral-7B.gguf")

def get_models():
    """Fetch models from backend and pick 14 test models + the best judge."""
    req = urllib.request.Request(f"{BACKEND}/__system-info")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    models = data.get("models", [])
    # Filter out non-text models and duplicates
    seen = set()
    filtered = []
    for m in models:
        name = m["name"]
        if any(s in name for s in SKIP):
            continue
        if name in seen:
            continue
        seen.add(name)
        filtered.append(m)
    # Sort by size
    filtered.sort(key=lambda x: x.get("size_mb", 0))
    # Pick 14 smallest as test models, largest overall as judge
    test_models = filtered[:14]
    judge_model = filtered[-1]  # biggest model = best judge
    return test_models, judge_model


# ── Test questions — mix of difficulty ───────────────────────────────────────

QUESTIONS = [
    {
        "id": "math_basic",
        "prompt": "What is 17 * 23? Show only the final number.",
        "expected": "391",
        "category": "math",
        "judge_template": "coding",
    },
    {
        "id": "reasoning_bat",
        "prompt": "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost? Show your reasoning step by step.",
        "expected": "$0.05",
        "category": "reasoning",
        "judge_template": "reasoning",
    },
    {
        "id": "code_bug",
        "prompt": "Find the bug in this Python code and explain the fix:\n\ndef average(nums):\n    total = 0\n    for n in nums:\n        total += n\n    return total / len(nums)\n\nprint(average([]))",
        "expected": "ZeroDivisionError",
        "category": "coding",
        "judge_template": "coding",
    },
    {
        "id": "knowledge",
        "prompt": "What is the capital of Australia? Answer in one word.",
        "expected": "Canberra",
        "category": "general",
        "judge_template": "general",
    },
    {
        "id": "logic",
        "prompt": "If all roses are flowers, and some flowers fade quickly, can we conclude that some roses fade quickly? Answer yes or no and explain why in one sentence.",
        "expected": "No",
        "category": "reasoning",
        "judge_template": "reasoning",
    },
]

# ── Judge templates ──────────────────────────────────────────────────────────

JUDGE_TEMPLATES = {
    "coding": (
        "You are an UNFORGIVING code evaluator. You MUST verify every claim before scoring.\n\n"
        "SCORING RULES (mandatory):\n"
        "- WRONG answer, wrong math, missed the actual bug: overall 0-2. NO exceptions.\n"
        "- Partially correct but missing key insight: overall 3-4\n"
        "- Correct answer but sloppy/verbose explanation: overall 5-6\n"
        "- Correct, clean, well-explained: overall 7-8\n"
        "- Textbook-perfect, concise, covers edge cases: overall 9-10\n"
        "- Garbled output, template tags, or off-topic: overall 0\n\n"
        "VERIFY: Check the actual math/code yourself before scoring.\n"
        "Output ONLY valid JSON: {\"overall\": <0-10>, \"accuracy\": <0-10>, \"reasoning\": <0-10>}"
    ),
    "reasoning": (
        "You are an UNFORGIVING reasoning evaluator. Verify the final answer yourself.\n\n"
        "SCORING RULES (mandatory):\n"
        "- WRONG final answer: overall 0-2. This is absolute. A beautifully reasoned wrong answer is still 0-2.\n"
        "- Right answer but flawed/missing reasoning: overall 3-5\n"
        "- Right answer with solid reasoning: overall 6-8\n"
        "- Perfect logic chain, clear and concise: overall 9-10\n"
        "- Garbled output or off-topic: overall 0\n\n"
        "CRITICAL: For the bat-and-ball problem, the correct answer is $0.05 (5 cents). "
        "If the model says $0.10, that is WRONG (score 0-2).\n"
        "For logic questions, verify syllogistic validity yourself.\n"
        "Output ONLY valid JSON: {\"overall\": <0-10>, \"accuracy\": <0-10>, \"reasoning\": <0-10>}"
    ),
    "general": (
        "You are an UNFORGIVING knowledge evaluator.\n\n"
        "SCORING RULES (mandatory):\n"
        "- WRONG answer: overall 0-2. No partial credit for wrong facts.\n"
        "- Correct but buried in filler/garbled text: overall 3-5\n"
        "- Correct and reasonably concise: overall 6-8\n"
        "- Correct, one-word/one-line as requested: overall 9-10\n"
        "- Garbled output, template tags, or off-topic: overall 0\n\n"
        "Output ONLY valid JSON: {\"overall\": <0-10>, \"accuracy\": <0-10>}"
    ),
}


def run_comparison(models, judge_model, question):
    """Run one question through all models via the comparator backend."""
    model_paths = [m["path"] for m in models]
    # Use the BIGGEST model as judge (separate from test set)
    judge_path = judge_model["path"]

    payload = json.dumps({
        "prompt": question["prompt"],
        "local_models": model_paths,
        "judge_model": judge_path,
        "judge_system_prompt": JUDGE_TEMPLATES[question["judge_template"]],
        "system_prompt": "You are a helpful assistant. Be concise and accurate.",
        "n_ctx": 2048,
        "max_tokens": 256,
        "temperature": 0.1,
        "inference_timeout": 180,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BACKEND}/__comparison/mixed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:
        result = json.loads(resp.read())
    wall = time.perf_counter() - t0
    return result, wall


def check_correctness(response_text, expected):
    """Simple check if expected answer appears in response."""
    if not response_text:
        return False
    return expected.lower() in response_text.lower()


def main():
    print("Fetching models from backend...")
    models, judge = get_models()
    if len(models) < 14:
        print(f"WARNING: Only {len(models)} models found, running with what we have")

    print(f"\n{'='*100}")
    print(f"  LLM TEST BED - GOLDEN BENCHMARK  ({len(models)} models, {len(QUESTIONS)} questions)")
    print(f"  Judge: {judge['name']} ({judge.get('size_mb',0)/1024:.1f} GB)  << SEPARATE from test set")
    print(f"{'='*100}")
    print(f"\n  Models under test:")
    for i, m in enumerate(models):
        print(f"    [{i+1:2d}] {m['name']:55s} {m.get('size_mb',0)/1024:.1f} GB")

    # Accumulate scores per model
    model_scores = {}  # model_name -> {scores: [], times: [], tokens: [], correct: []}
    for m in models:
        model_scores[m["name"]] = {"scores": [], "times": [], "tokens": [], "correct": [], "errors": 0}

    for qi, q in enumerate(QUESTIONS):
        print(f"\n{'─'*100}")
        print(f"  Question {qi+1}/{len(QUESTIONS)}: [{q['id']}] {q['prompt'][:80]}...")
        print(f"  Expected: {q['expected']}  |  Judge template: {q['judge_template']}")
        print(f"{'─'*100}")

        t0 = time.perf_counter()
        try:
            result, wall = run_comparison(models, judge, q)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        responses = result.get("responses", [])
        print(f"\n  Completed in {wall:.1f}s wall time  ({len(responses)} responses)\n")

        # Sort responses by judge score descending
        responses.sort(key=lambda r: r.get("judge_score", 0), reverse=True)

        print(f"  {'Rank':>4}  {'Model':45s}  {'Score':>5}  {'Time':>6}  {'Tok':>5}  {'Tok/s':>6}  {'Correct':>7}  Response preview")
        print(f"  {'─'*4}  {'─'*45}  {'─'*5}  {'─'*6}  {'─'*5}  {'─'*6}  {'─'*7}  {'─'*40}")

        for rank, r in enumerate(responses, 1):
            name = r.get("model", "?")
            score = r.get("judge_score", 0)
            time_ms = r.get("time_ms", 0)
            gen_time = time_ms / 1000  # convert ms to seconds
            tokens = r.get("tokens", 0)
            tok_s = r.get("tokens_per_sec", 0)
            if not tok_s and gen_time > 0 and tokens > 0:
                tok_s = tokens / gen_time
            text = (r.get("response", "") or "")[:50].replace("\n", " ")
            correct = check_correctness(r.get("response", ""), q["expected"])
            size_mb = r.get("model_size_mb", 0)

            print(f"  {rank:4d}  {name:45s}  {score:5.1f}  {gen_time:5.1f}s  {tokens:5d}  {tok_s:5.1f}  {'  YES' if correct else '   NO'}  {text}")

            if name in model_scores:
                model_scores[name]["scores"].append(score)
                model_scores[name]["times"].append(gen_time)
                model_scores[name]["tokens"].append(tokens)
                model_scores[name]["correct"].append(1 if correct else 0)

    # ── Final Rankings ───────────────────────────────────────────────────────
    print(f"\n\n{'='*120}")
    print(f"  FINAL RANKINGS  ({len(QUESTIONS)} questions, judge: {judge['name']} [{judge.get('size_mb',0)/1024:.0f}GB])")
    print(f"{'='*120}")

    rankings = []
    for name, data in model_scores.items():
        if not data["scores"]:
            continue
        n = len(data["scores"])
        avg_score = sum(data["scores"]) / n
        avg_time = sum(data["times"]) / n if data["times"] else 0
        total_tokens = sum(data["tokens"])
        avg_tok_s = total_tokens / sum(data["times"]) if sum(data["times"]) > 0 and total_tokens > 0 else 0
        correct_pct = sum(data["correct"]) / n * 100
        size_gb = next((m.get("size_mb", 0) / 1024 for m in models if m["name"] == name), 0)
        # Efficiency: score per GB
        efficiency = avg_score / size_gb if size_gb > 0 else 0

        rankings.append({
            "name": name,
            "avg_score": round(avg_score, 1),
            "correct_pct": round(correct_pct, 0),
            "avg_time": round(avg_time, 1),
            "total_tokens": total_tokens,
            "avg_tok_s": round(avg_tok_s, 1),
            "size_gb": round(size_gb, 1),
            "efficiency": round(efficiency, 2),
            "questions": n,
        })

    # Sort by avg_score descending, then by correct_pct, then by speed
    rankings.sort(key=lambda r: (-r["avg_score"], -r["correct_pct"], r["avg_time"]))

    header = (f"  {'Rank':>4}  {'Model':45s}  {'Avg':>5}  {'Correct':>7}  {'Avg Time':>8}  "
              f"{'Tokens':>6}  {'Tok/s':>6}  {'Size':>5}  {'Eff':>5}  {'Q':>3}")
    print(header)
    print(f"  {'─'*4}  {'─'*45}  {'─'*5}  {'─'*7}  {'─'*8}  {'─'*6}  {'─'*6}  {'─'*5}  {'─'*5}  {'─'*3}")

    for rank, r in enumerate(rankings, 1):
        medal = ""
        if rank == 1: medal = " <-- WINNER"
        elif rank == 2: medal = " <-- 2nd"
        elif rank == 3: medal = " <-- 3rd"

        print(f"  {rank:4d}  {r['name']:45s}  {r['avg_score']:5.1f}  {r['correct_pct']:6.0f}%  "
              f"{r['avg_time']:7.1f}s  {r['total_tokens']:6d}  {r['avg_tok_s']:5.1f}  "
              f"{r['size_gb']:4.1f}G  {r['efficiency']:5.2f}  {r['questions']:3d}{medal}")

    # Save results to JSON
    output = {
        "timestamp": time.time(),
        "num_models": len(models),
        "num_questions": len(QUESTIONS),
        "judge_model": judge["name"],
        "rankings": rankings,
    }
    with open("benchmark_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Results saved to benchmark_results.json")
    print()


if __name__ == "__main__":
    main()
