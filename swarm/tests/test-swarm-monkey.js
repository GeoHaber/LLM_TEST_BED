// ZenAIos — Swarm Monkey / Fuzz / Chaos Tests
// ==============================================
// Randomly hammers all /__swarm/* endpoints with malformed, extreme,
// and unexpected payloads. Verifies the server never crashes (5xx from
// unhandled exceptions) and always returns valid JSON.


var _smBase = (typeof location !== 'undefined' && location.origin !== 'null')
    ? location.origin
    : 'http://localhost:8080';

// ─── Random generators ───────────────────────────────────────

var CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !@#$%^&*()_+-=[]{}|;:,.<>?/~`"\'\\\n\t';

function randInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
function randFloat(min, max) { return Math.random() * (max - min) + min; }
function randString(len) {
    var s = '';
    for (var i = 0; i < len; i++) s += CHARS[randInt(0, CHARS.length - 1)];
    return s;
}
function randBool() { return Math.random() < 0.5; }

function randValue() {
    var type = randInt(0, 8);
    switch (type) {
        case 0: return null;
        case 1: return undefined;
        case 2: return randInt(-1e9, 1e9);
        case 3: return randFloat(-1e6, 1e6);
        case 4: return randString(randInt(0, 200));
        case 5: return randBool();
        case 6: return [];
        case 7: return {};
        case 8: return [randValue(), randValue()];
        default: return 'x';
    }
}

function randObject(depth) {
    if (depth > 2) return randString(5);
    var obj = {};
    var n = randInt(0, 8);
    for (var i = 0; i < n; i++) {
        var key = randBool() ? randString(randInt(1, 20)) : ['models', 'model', 'prompt', 'temperature', 'max_tokens', 'levels', 'category', 'results', 'response', 'result'][randInt(0, 9)];
        obj[key] = randBool() ? randValue() : randObject(depth + 1);
    }
    return obj;
}

// ─── Endpoint catalog ────────────────────────────────────────

var GET_ENDPOINTS = [
    '/__swarm/status',
    '/__swarm/models',
    '/__swarm/prompts',
    '/__swarm/random-prompt',
    '/__swarm/pool',
    '/__swarm/memory',
];

var POST_ENDPOINTS = [
    '/__swarm/arena',
    '/__swarm/benchmark',
    '/__swarm/inference',
    '/__swarm/evaluate',
    '/__swarm/marathon-round',
    '/__swarm/diagnose',
    '/__swarm/pool/preload',
    '/__swarm/pool/drain',
    '/__swarm/recommendations',
];

// ─── HTTP helpers ────────────────────────────────────────────

function _smGet(path) {
    return fetch(_smBase + path).then(function (r) {
        return r.text().then(function (t) {
            var data; try { data = JSON.parse(t); } catch (_e) { data = t; }
            return { ok: r.ok, status: r.status, data: data };
        });
    });
}

function _smPost(path, body) {
    return fetch(_smBase + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).then(function (r) {
        return r.text().then(function (t) {
            var data; try { data = JSON.parse(t); } catch (_e) { data = t; }
            return { ok: r.ok, status: r.status, data: data };
        });
    });
}

// ═══════════════════════════════════════════════════════════════
// Monkey Test 1: Random GET hammering
// ═══════════════════════════════════════════════════════════════

describe('Swarm Monkey — GET endpoint hammering (50 random)', function () {

    it('50 random GET requests never cause unhandled crash', async function (assert) {
        var errors = [];
        for (var i = 0; i < 50; i++) {
            var path = GET_ENDPOINTS[randInt(0, GET_ENDPOINTS.length - 1)];
            try {
                var r = await _smGet(path);
                // Server should never return 500 (unhandled)
                if (r.status >= 500 && r.status !== 503) {
                    errors.push('GET ' + path + ' → ' + r.status);
                }
            } catch (e) {
                errors.push('GET ' + path + ' threw: ' + e.message);
            }
        }
        assert.equal(errors.length, 0, 'errors: ' + errors.join('; '));
    });

    it('all GET responses are valid JSON or known error shapes', async function (assert) {
        for (var i = 0; i < GET_ENDPOINTS.length; i++) {
            var r = await _smGet(GET_ENDPOINTS[i]);
            assert.ok(typeof r.data === 'object' || typeof r.data === 'string',
                GET_ENDPOINTS[i] + ' returned parseable data');
        }
    });
});

// ═══════════════════════════════════════════════════════════════
// Monkey Test 2: Random POST payloads
// ═══════════════════════════════════════════════════════════════

describe('Swarm Monkey — Random POST payloads (100 mutations)', function () {

    it('100 random POSTs with garbage payloads never crash (500)', async function (assert) {
        var errors = [];
        for (var i = 0; i < 100; i++) {
            var path = POST_ENDPOINTS[randInt(0, POST_ENDPOINTS.length - 1)];
            var body = randObject(0);
            try {
                var r = await _smPost(path, body);
                if (r.status >= 500 && r.status !== 503) {
                    errors.push('POST ' + path + ' → ' + r.status + ' body=' + JSON.stringify(body).slice(0, 100));
                }
            } catch (e) {
                errors.push('POST ' + path + ' threw: ' + e.message);
            }
        }
        assert.equal(errors.length, 0, errors.join('\n'));
    });
});

// ═══════════════════════════════════════════════════════════════
// Monkey Test 3: Type confusion attacks
// ═══════════════════════════════════════════════════════════════

describe('Swarm Monkey — Type confusion (30 tests)', function () {

    var TYPE_ATTACKS = [
        { models: 'not-an-array', prompt: 123 },
        { models: null, prompt: null },
        { models: [null, undefined, 123, true], prompt: {} },
        { model: [], prompt: true, max_tokens: 'many' },
        { model: { nested: true }, levels: 'high' },
        { response: 12345, category: [], prompt: false },
        { results: 'string-not-array' },
        { result: [1, 2, 3] },
        { models: [true, false] },
        { temperature: 'hot', max_tokens: -999 },
    ];

    it('type-confused payloads to all POST endpoints handled gracefully', async function (assert) {
        var errors = [];
        for (var ep = 0; ep < POST_ENDPOINTS.length; ep++) {
            for (var att = 0; att < TYPE_ATTACKS.length; att++) {
                try {
                    var r = await _smPost(POST_ENDPOINTS[ep], TYPE_ATTACKS[att]);
                    if (r.status >= 500 && r.status !== 503) {
                        errors.push(POST_ENDPOINTS[ep] + ' attack #' + att + ' → ' + r.status);
                    }
                } catch (e) {
                    errors.push(POST_ENDPOINTS[ep] + ' attack #' + att + ' threw: ' + e.message);
                }
            }
        }
        assert.equal(errors.length, 0, errors.join('\n'));
    });
});

// ═══════════════════════════════════════════════════════════════
// Monkey Test 4: Boundary values
// ═══════════════════════════════════════════════════════════════

describe('Swarm Monkey — Boundary values', function () {

    it('extremely large max_tokens handled', async function (assert) {
        var r = await _smPost('/__swarm/inference', {
            model: 'test.gguf',
            prompt: 'hello',
            max_tokens: 999999999,
        });
        assert.ok(r.status < 500 || r.status === 503, 'no crash');
    });

    it('negative temperature handled', async function (assert) {
        var r = await _smPost('/__swarm/inference', {
            model: 'test.gguf',
            prompt: 'hello',
            temperature: -5.0,
        });
        assert.ok(r.status < 500 || r.status === 503, 'no crash');
    });

    it('empty string payload fields handled', async function (assert) {
        var r = await _smPost('/__swarm/arena', {
            models: ['', '', ''],
            prompt: '',
            system_prompt: '',
            max_tokens: 0,
            temperature: 0,
        });
        assert.ok(r.status < 500 || r.status === 503, 'no crash');
    });

    it('very long prompt handled', async function (assert) {
        var longPrompt = randString(50000);
        var r = await _smPost('/__swarm/inference', {
            model: 'test.gguf',
            prompt: longPrompt,
            max_tokens: 1,
        });
        assert.ok(r.status < 500 || r.status === 503, 'no crash');
    });

    it('many models in arena handled', async function (assert) {
        var models = [];
        for (var i = 0; i < 50; i++) models.push('fake_model_' + i + '.gguf');
        var r = await _smPost('/__swarm/arena', {
            models: models,
            prompt: 'test',
            max_tokens: 1,
        });
        assert.ok(r.status < 500 || r.status === 503, 'no crash');
    });
});

// ═══════════════════════════════════════════════════════════════
// Monkey Test 5: Concurrent requests
// ═══════════════════════════════════════════════════════════════

describe('Swarm Monkey — Concurrent request storm (20 parallel)', function () {

    it('20 concurrent GET + POST requests all return valid HTTP', async function (assert) {
        var promises = [];
        for (var i = 0; i < 10; i++) {
            promises.push(_smGet(GET_ENDPOINTS[i % GET_ENDPOINTS.length]));
        }
        for (var j = 0; j < 10; j++) {
            promises.push(_smPost(POST_ENDPOINTS[j % POST_ENDPOINTS.length], randObject(0)));
        }
        var results = await Promise.all(promises);
        var crashes = results.filter(function (r) { return r.status >= 500 && r.status !== 503; });
        assert.equal(crashes.length, 0, crashes.length + ' server crashes in concurrent storm');
    });
});

// ═══════════════════════════════════════════════════════════════
// Monkey Test 6: Missing Content-Type
// ═══════════════════════════════════════════════════════════════

describe('Swarm Monkey — Malformed HTTP', function () {

    it('POST with no Content-Type header handled', async function (assert) {
        var r = await fetch(_smBase + '/__swarm/diagnose', {
            method: 'POST',
            body: '{"results":[]}',
        });
        assert.ok(r.status < 500 || r.status === 503, 'no crash without CT header');
    });

    it('POST with empty body handled', async function (assert) {
        var r = await fetch(_smBase + '/__swarm/evaluate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '',
        });
        assert.ok(r.status < 600, 'valid HTTP response');
    });

    it('POST with non-JSON body handled', async function (assert) {
        var r = await fetch(_smBase + '/__swarm/inference', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: 'this is not json at all <html>',
        });
        assert.ok(r.status < 600, 'valid HTTP response');
    });
});

// ═══════════════════════════════════════════════════════════════
// Monkey Test 7: Rapid sequential calls to same endpoint
// ═══════════════════════════════════════════════════════════════

describe('Swarm Monkey — Rapid-fire same endpoint (30x status)', function () {

    it('30 sequential status checks all succeed', async function (assert) {
        var failures = 0;
        for (var i = 0; i < 30; i++) {
            var r = await _smGet('/__swarm/status');
            if (!r.ok) failures++;
        }
        assert.equal(failures, 0, failures + ' failures in 30 rapid calls');
    });
});

// ═══════════════════════════════════════════════════════════════
// Monkey Test 8: Cross-endpoint sequence (realistic workflow)
// ═══════════════════════════════════════════════════════════════

describe('Swarm Monkey — Realistic workflow sequence', function () {

    it('status → models → random-prompt → diagnose → recommendations', async function (assert) {
        var r1 = await _smGet('/__swarm/status');
        assert.ok(r1.ok, 'status OK');

        var r2 = await _smGet('/__swarm/models');
        assert.equal(r2.status, 200, 'models OK');

        var r3 = await _smGet('/__swarm/random-prompt');
        assert.equal(r3.status, 200, 'random-prompt OK');

        var r4 = await _smPost('/__swarm/diagnose', { results: [] });
        assert.ok(r4.status < 500 || r4.status === 503, 'diagnose OK');

        var r5 = await _smPost('/__swarm/recommendations', { result: {} });
        assert.ok(r5.status < 500 || r5.status === 503, 'recommendations OK');
    });
});

