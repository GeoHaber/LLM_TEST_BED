"""
Simulates the Run All engine from swarm-test.html — hits every endpoint
3 times in random order with random params, capturing all errors.
"""
import json
import random
import time
import urllib.request
import urllib.error
import traceback
import socket

import os as _os
BASE = _os.environ.get("TEST_BASE_URL", "http://localhost:8777")
CATEGORIES = ["reasoning", "coding", "creative", "knowledge", "math"]

def api(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode()
            try:
                return {"status": resp.status, "data": json.loads(text)}
            except json.JSONDecodeError:
                return {"status": resp.status, "data": text}
    except urllib.error.HTTPError as e:
        text = e.read().decode() if e.fp else ""
        try:
            return {"status": e.code, "data": json.loads(text)}
        except:
            return {"status": e.code, "data": text}
    except socket.timeout as e:
        return {"status": 0, "data": f"timeout: {e}"}
    except Exception as e:
        return {"status": 0, "data": str(e)}

# Discover models first
print("Discovering models...")
mr = api("GET", "/__swarm/models")
models = []
if isinstance(mr["data"], dict) and "models" in mr["data"]:
    models = mr["data"]["models"]
elif isinstance(mr["data"], list):
    models = mr["data"]
print(f"  Found {len(models)} models")

def rand_model():
    if not models:
        return "test.gguf"
    m = random.choice(models)
    return m.get("path", m) if isinstance(m, dict) else m

def rand_models(n):
    if not models:
        return ["a.gguf", "b.gguf"]
    picked = random.sample(models, min(n, len(models)))
    return [m.get("path", m) if isinstance(m, dict) else m for m in picked]

def rand_cat():
    return random.choice(CATEGORIES)

MAX_TOK = 32

# Define all tests
def build_tests():
    return [
        ("GET /status",         lambda: check(api("GET", "/__swarm/status"), 200)),
        ("GET /memory",         lambda: check(api("GET", "/__swarm/memory"), 200)),
        ("GET /pool",           lambda: check(api("GET", "/__swarm/pool"), 200)),
        ("GET /models",         lambda: check(api("GET", "/__swarm/models"), 200)),
        ("GET /prompts",        lambda: check(api("GET", "/__swarm/prompts"), 200)),
        ("GET /random-prompt",  lambda: check(api("GET", "/__swarm/random-prompt"), 200)),
        ("POST /arena",         lambda: check(api("POST", "/__swarm/arena", {
            "models": rand_models(2), "prompt": get_prompt(), "max_tokens": MAX_TOK, "temperature": 0.7
        }), 200)),
        ("POST /benchmark",     lambda: check(api("POST", "/__swarm/benchmark", {
            "model": rand_model(), "prompt": "Explain quicksort briefly.", "levels": [1], "max_tokens": MAX_TOK, "temperature": 0.7
        }), 200)),
        ("POST /inference",     lambda: check(api("POST", "/__swarm/inference", {
            "model": rand_model(), "prompt": get_prompt(), "max_tokens": MAX_TOK, "temperature": 0.7
        }), 200)),
        ("POST /evaluate",      lambda: check(api("POST", "/__swarm/evaluate", {
            "response": "The answer is 42.", "category": rand_cat(), "prompt": "What is 6*7?"
        }), 200)),
        ("POST /marathon-round", lambda: check(api("POST", "/__swarm/marathon-round", {
            "models": rand_models(2), "category": rand_cat(), "max_tokens": MAX_TOK, "temperature": 0.7
        }), 200)),
        ("POST /diagnose",      lambda: check(api("POST", "/__swarm/diagnose", {
            "results": [{"model": "test.gguf", "tok_per_sec": 10, "elapsed": 1.5}]
        }), 200)),
        ("POST /pool/preload",  lambda: check(api("POST", "/__swarm/pool/preload", {"models": []}), 200)),
        ("POST /pool/drain",    lambda: check(api("POST", "/__swarm/pool/drain", {}), 200)),
        ("POST /recommendations", lambda: check(api("POST", "/__swarm/recommendations", {
            "result": {"tok_per_sec": 5, "elapsed": 10, "model": "test.gguf"}
        }), 200)),
        ("POST /nonexistent",   lambda: check_any(api("POST", "/__swarm/nonexistent", {}), [400, 404])),
    ]

def get_prompt():
    r = api("GET", "/__swarm/random-prompt")
    if isinstance(r["data"], dict) and r["data"].get("prompt"):
        return r["data"]["prompt"]
    return "Hello, explain something interesting."

def check(r, expected_status):
    ok = r["status"] == expected_status
    detail = str(r["data"])[:100] if not ok else "ok"
    return ok, f"status={r['status']} {detail}"

def check_any(r, statuses):
    ok = r["status"] in statuses
    return ok, f"status={r['status']}"

# Run all tests 3x in random order
REPEATS = 3
tests = []
for i in range(REPEATS):
    for name, fn in build_tests():
        tests.append((name, fn, i+1))
random.shuffle(tests)

passed = failed = 0
errors_detail = []
print(f"\nRunning {len(tests)} tests ({REPEATS} repeats × {len(build_tests())} endpoints)...\n")
t_start = time.time()

try:
  for idx, (name, fn, rnd) in enumerate(tests, 1):
    t0 = time.time()
    try:
        ok, detail = fn()
        ms = int((time.time() - t0) * 1000)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
            errors_detail.append(f"  FAIL #{rnd}: {name} — {detail}")
        print(f"  [{idx:3d}/{len(tests)}] {status}  {name} #{rnd}  ({ms}ms)  {detail[:80]}")
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        failed += 1
        tb = traceback.format_exc()
        errors_detail.append(f"  ERROR #{rnd}: {name} — {e}\n{tb}")
        print(f"  [{idx:3d}/{len(tests)}] ERROR {name} #{rnd}  ({ms}ms)  {e}")
except KeyboardInterrupt:
    print(f"\n  ⚠ Interrupted after {idx-1} tests")

elapsed = time.time() - t_start
print(f"\n{'='*60}")
print(f"  TOTAL: {passed + failed}  |  PASSED: {passed}  |  FAILED: {failed}")
print(f"  Time: {elapsed:.1f}s")
print(f"{'='*60}")
if errors_detail:
    print("\nFailed tests detail:")
    for e in errors_detail:
        print(e)
else:
    print("\n  ALL TESTS PASSED ✅")
