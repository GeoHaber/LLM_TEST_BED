// ZenAIos — Swarm Integration Tests
// ====================================
// Tests every /__swarm/* endpoint exposed by server.py + swarm_bridge.py.
// Requires the server to be running on BASE_URL (default: http://localhost:8080).
// Uses the same TestRunner describe/it/assert pattern as the rest of the suite.

var _swarmBase = (typeof location !== 'undefined' && location.origin !== 'null')
? location.origin
: 'http://localhost:8080';

function _swarmApi(method, path, body) {
var opts = { method: method, headers: {} };
if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
}
return fetch(_swarmBase + path, opts).then(function (r) {
    return r.text().then(function (t) {
        var data;
        try { data = JSON.parse(t); } catch (_e) { data = t; }
        return { ok: r.ok, status: r.status, data: data };
    });
});
}

// ═══════════════════════════════════════════════════════════════
// GET endpoints
// ═══════════════════════════════════════════════════════════════

describe('Swarm — GET /__swarm/status', function () {
    it('returns 200 with bridge & engine fields', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/status');
        assert.equal(r.status, 200, 'status code');
        assert.hasProperty(r.data, 'bridge', 'has bridge');
        assert.hasProperty(r.data, 'engine', 'has engine');
    });

    it('bridge field is boolean', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/status');
        assert.typeOf(r.data.bridge, 'boolean', 'bridge is bool');
    });
});

describe('Swarm — GET /__swarm/models', function () {
    it('returns 200 with models array', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/models');
        assert.equal(r.status, 200, 'status code');
        assert.hasProperty(r.data, 'models', 'has models');
        assert.isArray(r.data.models, 'models is array');
    });

    it('each model has at least path or name', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/models');
        if (r.data.models && r.data.models.length > 0) {
            r.data.models.forEach(function (m, i) {
                var hasId = m.path || m.name || (typeof m === 'string');
                assert.ok(hasId, 'model ' + i + ' has identifier');
            });
        }
        // If no models, that's OK — pass silently
    });
});

describe('Swarm — GET /__swarm/prompts', function () {
    it('returns 200', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/prompts');
        assert.equal(r.status, 200, 'status code');
    });

    it('data is object or array', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/prompts');
        assert.ok(typeof r.data === 'object', 'data is object/array');
    });
});

describe('Swarm — GET /__swarm/random-prompt', function () {
    it('returns 200 with prompt field', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/random-prompt');
        assert.equal(r.status, 200, 'status code');
        assert.hasProperty(r.data, 'prompt', 'has prompt');
        assert.typeOf(r.data.prompt, 'string', 'prompt is string');
    });

    it('prompt is non-empty', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/random-prompt');
        assert.greaterThan(r.data.prompt.length, 0, 'prompt non-empty');
    });

    it('two random prompts can differ', async function (assert) {
        // Get 5 random prompts — at least 2 should differ
        var prompts = [];
        for (var i = 0; i < 5; i++) {
            var r = await _swarmApi('GET', '/__swarm/random-prompt');
            prompts.push(r.data.prompt);
        }
        var unique = prompts.filter(function (v, i, a) { return a.indexOf(v) === i; });
        assert.greaterThan(unique.length, 1, 'should have variety');
    });
});

describe('Swarm — GET /__swarm/pool', function () {
    it('returns 200', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/pool');
        assert.equal(r.status, 200, 'status code');
    });

    it('response is JSON object', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/pool');
        assert.ok(typeof r.data === 'object' && r.data !== null, 'is object');
    });
});

describe('Swarm — GET /__swarm/memory', function () {
    it('returns 200 with memory data', async function (assert) {
        var r = await _swarmApi('GET', '/__swarm/memory');
        assert.equal(r.status, 200, 'status code');
        assert.ok(typeof r.data === 'object', 'is object');
    });
});

