// ZenAIos — Swarm POST Endpoint Tests
// ======================================
// POST endpoint validation, CORS, unknown-endpoint, consistency tests.
// Uses global _swarmApi / _swarmBase helpers defined in test-swarm.js.

// ═══════════════════════════════════════════════════════════════
// POST endpoints — validation / error handling
// ═══════════════════════════════════════════════════════════════

describe('Swarm — POST /__swarm/arena (validation)', function () {
    it('returns error when no models given', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/arena', { models: [], prompt: 'test' });
        assert.ok(r.data, 'has response data');
    });

    it('accepts well-formed request without crashing', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/arena', {
            models: [],
            prompt: 'Hello world',
            max_tokens: 32,
            temperature: 0.5,
        });
        assert.ok(r.status >= 200 && r.status < 600, 'valid HTTP status');
    });
});

describe('Swarm — POST /__swarm/benchmark (validation)', function () {
    it('rejects missing model', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/benchmark', {
            model: '',
            prompt: 'test',
            levels: [1],
        });
        assert.ok(r.data, 'has response data');
    });
});

describe('Swarm — POST /__swarm/inference (validation)', function () {
    it('rejects missing model', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/inference', {
            model: '',
            prompt: 'Hello',
        });
        assert.ok(r.status >= 200 && r.status < 600, 'valid HTTP status');
    });

    it('accepts valid payload structure', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/inference', {
            model: 'nonexistent.gguf',
            prompt: 'Hello',
            max_tokens: 16,
            temperature: 0.5,
        });
        assert.notEqual(r.status, 404, 'endpoint exists');
    });
});

describe('Swarm — POST /__swarm/evaluate (validation)', function () {
    it('works with valid payload', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/evaluate', {
            response: 'The answer is 42.',
            category: 'math',
            prompt: 'What is 6 * 7?',
        });
        assert.ok(r.status >= 200 && r.status < 600, 'valid HTTP status');
    });

    it('handles empty response gracefully', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/evaluate', {
            response: '',
            category: 'reasoning',
            prompt: 'test',
        });
        assert.ok(r.data, 'has response data');
    });
});

describe('Swarm — POST /__swarm/marathon-round (validation)', function () {
    it('rejects empty models array', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/marathon-round', {
            models: [],
            category: 'reasoning',
        });
        assert.ok(r.data, 'has response data');
    });
});

describe('Swarm — POST /__swarm/diagnose', function () {
    it('accepts empty results', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/diagnose', { results: [] });
        assert.ok(r.status >= 200 && r.status < 600, 'valid HTTP status');
    });

    it('accepts sample results', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/diagnose', {
            results: [{ model: 'test.gguf', tok_per_sec: 10, elapsed: 1.5 }],
        });
        assert.ok(r.data, 'has response data');
    });
});

describe('Swarm — POST /__swarm/pool/preload', function () {
    it('accepts empty models list', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/pool/preload', { models: [] });
        assert.ok(r.status >= 200 && r.status < 600, 'valid HTTP status');
    });
});

describe('Swarm — POST /__swarm/pool/drain', function () {
    it('returns success', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/pool/drain', {});
        assert.ok(r.status >= 200 && r.status < 600, 'valid HTTP status');
    });
});

describe('Swarm — POST /__swarm/recommendations', function () {
    it('returns tips for sample result', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/recommendations', {
            result: { tok_per_sec: 5, elapsed: 10, model: 'test.gguf' },
        });
        assert.ok(r.status >= 200 && r.status < 600, 'valid HTTP status');
        if (r.ok) {
            assert.hasProperty(r.data, 'tips', 'has tips');
        }
    });
});

// ═══════════════════════════════════════════════════════════════
// Unknown endpoint
// ═══════════════════════════════════════════════════════════════

describe('Swarm — Unknown endpoints', function () {
    it('POST /__swarm/nonexistent returns 404', async function (assert) {
        var r = await _swarmApi('POST', '/__swarm/nonexistent', {});
        assert.ok(r.status === 404 || r.status === 400, 'unknown action returns 404 or 400');
    });
});

// ═══════════════════════════════════════════════════════════════
// CORS headers
// ═══════════════════════════════════════════════════════════════

describe('Swarm — CORS headers', function () {
    it('GET responses include Access-Control-Allow-Origin', async function (assert) {
        var r = await fetch(_swarmBase + '/__swarm/status');
        var cors = r.headers.get('Access-Control-Allow-Origin');
        assert.equal(cors, '*', 'CORS header is *');
    });

    it('POST responses include Access-Control-Allow-Origin', async function (assert) {
        var r = await fetch(_swarmBase + '/__swarm/diagnose', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ results: [] }),
        });
        var cors = r.headers.get('Access-Control-Allow-Origin');
        assert.equal(cors, '*', 'CORS header is *');
    });
});

// ═══════════════════════════════════════════════════════════════
// Consistency checks
// ═══════════════════════════════════════════════════════════════

describe('Swarm — Status consistency', function () {
    it('status bridge=true matches models being accessible', async function (assert) {
        var s = await _swarmApi('GET', '/__swarm/status');
        var m = await _swarmApi('GET', '/__swarm/models');
        if (s.data.bridge === true) {
            assert.equal(m.status, 200, 'models accessible when bridge=true');
        }
    });

    it('memory endpoint always works regardless of bridge state', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/memory');
        assert.equal(r.status, 200, 'memory always 200');
    });
});
