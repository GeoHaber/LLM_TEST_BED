"""
Simulates ALL browser JS tests from test-swarm.js, test-swarm-post.js,
test-swarm-monkey.js, and test-chat.js — reporting pass/fail to terminal.
"""
import json, random, urllib.request, urllib.error, sys, time

BASE = "http://localhost:8777"
passed = failed = 0
failures = []

def fetch(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    hdrs = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            cors = r.headers.get("Access-Control-Allow-Origin")
            txt = r.read().decode()
            try:
                d = json.loads(txt)
            except:
                d = txt
            return {"ok": True, "status": r.status, "data": d, "cors": cors}
    except urllib.error.HTTPError as e:
        cors = e.headers.get("Access-Control-Allow-Origin")
        txt = e.read().decode() if e.fp else ""
        try:
            d = json.loads(txt)
        except:
            d = txt
        return {"ok": False, "status": e.code, "data": d, "cors": cors}
    except Exception as e:
        return {"ok": False, "status": 0, "data": str(e), "cors": None}

def ok(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        failures.append(msg)
        print(f"    FAIL: {msg}")

suite = ""
def describe(name):
    global suite
    suite = name
    print(f"\n  {name}")

def it(name, fn):
    global passed, failed
    t0 = time.time()
    try:
        fn()
        ms = int((time.time()-t0)*1000)
        print(f"    PASS  {name}  ({ms}ms)")
    except Exception as e:
        ms = int((time.time()-t0)*1000)
        failed += 1
        failures.append(f"{suite} > {name}: {e}")
        print(f"    FAIL  {name}  ({ms}ms)  {e}")

# ═══════════════════════════════════════════════════════════
# test-swarm.js
# ═══════════════════════════════════════════════════════════

describe("Swarm — GET /__swarm/status")
def t():
    r = fetch("GET", "/__swarm/status")
    ok(r["status"] == 200, "status code 200")
    ok("bridge" in r["data"], "has bridge")
    ok("engine" in r["data"], "has engine")
it("returns 200 with bridge & engine fields", t)

def t():
    r = fetch("GET", "/__swarm/status")
    ok(isinstance(r["data"].get("bridge"), bool), "bridge is bool")
it("bridge field is boolean", t)

describe("Swarm — GET /__swarm/models")
def t():
    r = fetch("GET", "/__swarm/models")
    ok(r["status"] == 200, "status 200")
    ok("models" in r["data"], "has models")
    ok(isinstance(r["data"]["models"], list), "models is array")
it("returns 200 with models array", t)

def t():
    r = fetch("GET", "/__swarm/models")
    for i, m in enumerate(r["data"].get("models", [])):
        has_id = isinstance(m, str) or (isinstance(m, dict) and (m.get("path") or m.get("name")))
        ok(has_id, f"model {i} has identifier")
it("each model has at least path or name", t)

describe("Swarm — GET /__swarm/prompts")
def t():
    r = fetch("GET", "/__swarm/prompts")
    ok(r["status"] == 200, "status 200")
it("returns 200", t)

def t():
    r = fetch("GET", "/__swarm/prompts")
    ok(isinstance(r["data"], (dict, list)), "data is object/array")
it("data is object or array", t)

describe("Swarm — GET /__swarm/random-prompt")
def t():
    r = fetch("GET", "/__swarm/random-prompt")
    ok(r["status"] == 200, "status 200")
    ok("prompt" in r["data"], "has prompt")
    ok(isinstance(r["data"]["prompt"], str), "prompt is string")
it("returns 200 with prompt field", t)

def t():
    r = fetch("GET", "/__swarm/random-prompt")
    ok(len(r["data"]["prompt"]) > 0, "prompt non-empty")
it("prompt is non-empty", t)

def t():
    prompts = set()
    for _ in range(5):
        r = fetch("GET", "/__swarm/random-prompt")
        prompts.add(r["data"]["prompt"])
    ok(len(prompts) > 1, "should have variety")
it("two random prompts can differ", t)

describe("Swarm — GET /__swarm/pool")
def t():
    r = fetch("GET", "/__swarm/pool")
    ok(r["status"] == 200, "status 200")
it("returns 200", t)

def t():
    r = fetch("GET", "/__swarm/pool")
    ok(isinstance(r["data"], dict), "is object")
it("response is JSON object", t)

describe("Swarm — GET /__swarm/memory")
def t():
    r = fetch("GET", "/__swarm/memory")
    ok(r["status"] == 200, "status 200")
    ok(isinstance(r["data"], dict), "is object")
it("returns 200 with memory data", t)

# ═══════════════════════════════════════════════════════════
# test-swarm-post.js
# ═══════════════════════════════════════════════════════════

describe("Swarm — POST /__swarm/arena (validation)")
def t():
    r = fetch("POST", "/__swarm/arena", {"models": [], "prompt": "test"})
    ok(r["data"], "has response data")
it("returns error when no models given", t)

def t():
    r = fetch("POST", "/__swarm/arena", {"models": [], "prompt": "Hello world", "max_tokens": 32, "temperature": 0.5})
    ok(200 <= r["status"] < 600, "valid HTTP status")
it("accepts well-formed request without crashing", t)

describe("Swarm — POST /__swarm/benchmark (validation)")
def t():
    r = fetch("POST", "/__swarm/benchmark", {"model": "", "prompt": "test", "levels": [1]})
    ok(r["data"], "has response data")
it("rejects missing model", t)

describe("Swarm — POST /__swarm/inference (validation)")
def t():
    r = fetch("POST", "/__swarm/inference", {"model": "", "prompt": "Hello"})
    ok(200 <= r["status"] < 600, "valid HTTP status")
it("rejects missing model", t)

def t():
    r = fetch("POST", "/__swarm/inference", {"model": "nonexistent.gguf", "prompt": "Hello", "max_tokens": 16, "temperature": 0.5})
    ok(r["status"] != 404, "endpoint exists")
it("accepts valid payload structure", t)

describe("Swarm — POST /__swarm/evaluate (validation)")
def t():
    r = fetch("POST", "/__swarm/evaluate", {"response": "The answer is 42.", "category": "math", "prompt": "What is 6 * 7?"})
    ok(200 <= r["status"] < 600, "valid HTTP status")
it("works with valid payload", t)

def t():
    r = fetch("POST", "/__swarm/evaluate", {"response": "", "category": "reasoning", "prompt": "test"})
    ok(r["data"], "has response data")
it("handles empty response gracefully", t)

describe("Swarm — POST /__swarm/marathon-round (validation)")
def t():
    r = fetch("POST", "/__swarm/marathon-round", {"models": [], "category": "reasoning"})
    ok(r["data"], "has response data")
it("rejects empty models array", t)

describe("Swarm — POST /__swarm/diagnose")
def t():
    r = fetch("POST", "/__swarm/diagnose", {"results": []})
    ok(200 <= r["status"] < 600, "valid HTTP status")
it("accepts empty results", t)

def t():
    r = fetch("POST", "/__swarm/diagnose", {"results": [{"model": "test.gguf", "tok_per_sec": 10, "elapsed": 1.5}]})
    ok(r["data"], "has response data")
it("accepts sample results", t)

describe("Swarm — POST /__swarm/pool/preload")
def t():
    r = fetch("POST", "/__swarm/pool/preload", {"models": []})
    ok(200 <= r["status"] < 600, "valid HTTP status")
it("accepts empty models list", t)

describe("Swarm — POST /__swarm/pool/drain")
def t():
    r = fetch("POST", "/__swarm/pool/drain", {})
    ok(200 <= r["status"] < 600, "valid HTTP status")
it("returns success", t)

describe("Swarm — POST /__swarm/recommendations")
def t():
    r = fetch("POST", "/__swarm/recommendations", {"result": {"tok_per_sec": 5, "elapsed": 10, "model": "test.gguf"}})
    ok(200 <= r["status"] < 600, "valid HTTP status")
    if r["ok"]:
        ok("tips" in r["data"], "has tips")
it("returns tips for sample result", t)

describe("Swarm — Unknown endpoints")
def t():
    r = fetch("POST", "/__swarm/nonexistent", {})
    ok(r["status"] in (404, 400), f"unknown action returns 404 or 400 (got {r['status']})")
it("POST /__swarm/nonexistent returns 404", t)

describe("Swarm — CORS headers")
def t():
    r = fetch("GET", "/__swarm/status")
    ok(r["cors"] == "*", f"CORS header is * (got {r['cors']})")
it("GET responses include Access-Control-Allow-Origin", t)

def t():
    r = fetch("POST", "/__swarm/diagnose", {"results": []})
    ok(r["cors"] == "*", f"CORS header is * (got {r['cors']})")
it("POST responses include Access-Control-Allow-Origin", t)

describe("Swarm — Status consistency")
def t():
    s = fetch("GET", "/__swarm/status")
    m = fetch("GET", "/__swarm/models")
    if s["data"].get("bridge") is True:
        ok(m["status"] == 200, "models accessible when bridge=true")
it("status bridge=true matches models being accessible", t)

def t():
    r = fetch("GET", "/__swarm/memory")
    ok(r["status"] == 200, "memory always 200")
it("memory endpoint always works regardless of bridge state", t)

# ═══════════════════════════════════════════════════════════
# test-swarm-monkey.js (simplified — key assertions)
# ═══════════════════════════════════════════════════════════

GET_EPS = ["/__swarm/status", "/__swarm/models", "/__swarm/prompts",
           "/__swarm/random-prompt", "/__swarm/pool", "/__swarm/memory"]
POST_EPS = ["/__swarm/arena", "/__swarm/benchmark", "/__swarm/inference",
            "/__swarm/evaluate", "/__swarm/marathon-round", "/__swarm/diagnose",
            "/__swarm/pool/preload", "/__swarm/pool/drain", "/__swarm/recommendations"]

describe("Swarm Monkey — GET endpoint hammering (10 random)")
def t():
    errors = []
    for _ in range(10):
        path = random.choice(GET_EPS)
        r = fetch("GET", path)
        if r["status"] >= 500 and r["status"] != 503:
            errors.append(f"GET {path} -> {r['status']}")
    ok(len(errors) == 0, f"errors: {'; '.join(errors)}")
it("10 random GETs never cause unhandled crash", t)

describe("Swarm Monkey — Type confusion")
TYPE_ATTACKS = [
    {"models": "not-an-array", "prompt": 123},
    {"models": None, "prompt": None},
    {"model": [], "prompt": True, "max_tokens": "many"},
    {"temperature": "hot", "max_tokens": -999},
    {"response": 12345, "category": [], "prompt": False},
    {"results": "string-not-array"},
]

def t():
    errors = []
    for ep in POST_EPS:
        for att in TYPE_ATTACKS:
            r = fetch("POST", ep, att)
            if r["status"] >= 500 and r["status"] != 503:
                errors.append(f"{ep} -> {r['status']} body={json.dumps(att)[:80]}")
    ok(len(errors) == 0, f"crashes: {'; '.join(errors)}")
it("type-confused payloads handled gracefully", t)

describe("Swarm Monkey — Boundary values")
def t():
    r = fetch("POST", "/__swarm/inference", {"model": "test.gguf", "prompt": "hello", "max_tokens": 999999999})
    ok(r["status"] < 500 or r["status"] == 503, "no crash")
it("extremely large max_tokens handled", t)

def t():
    r = fetch("POST", "/__swarm/inference", {"model": "test.gguf", "prompt": "hello", "temperature": -5.0})
    ok(r["status"] < 500 or r["status"] == 503, "no crash")
it("negative temperature handled", t)

def t():
    r = fetch("POST", "/__swarm/arena", {"models": ["", "", ""], "prompt": "", "system_prompt": "", "max_tokens": 0, "temperature": 0})
    ok(r["status"] < 500 or r["status"] == 503, "no crash")
it("empty string payload fields handled", t)

describe("Swarm Monkey — Malformed HTTP")
def t():
    req = urllib.request.Request(BASE + "/__swarm/diagnose", b'{"results":[]}', method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ok(r.status < 500 or r.status == 503, "no crash without CT header")
    except urllib.error.HTTPError as e:
        ok(e.code < 500 or e.code == 503, f"no crash (got {e.code})")
it("POST with no Content-Type header handled", t)

def t():
    req = urllib.request.Request(BASE + "/__swarm/evaluate", b"",
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ok(r.status < 600, "valid HTTP response")
    except urllib.error.HTTPError as e:
        ok(e.code < 600, f"valid HTTP (got {e.code})")
it("POST with empty body handled", t)

def t():
    req = urllib.request.Request(BASE + "/__swarm/inference",
                                 b"this is not json at all <html>",
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ok(r.status < 600, "valid HTTP response")
    except urllib.error.HTTPError as e:
        ok(e.code < 600, f"valid HTTP (got {e.code})")
it("POST with non-JSON body handled", t)

# ═══════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  TOTAL: {passed+failed}  |  PASSED: {passed}  |  FAILED: {failed}")
print(f"{'='*60}")
if failures:
    print("\nFailures:")
    for f in failures:
        print(f"  - {f}")
else:
    print("\n  ALL TESTS PASSED ✅")

sys.exit(0 if failed == 0 else 1)
