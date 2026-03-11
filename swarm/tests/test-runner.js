// ═══════════════════════════════════════════════════════════════
// TestRunner — minimal async test framework for browser-based tests
// ═══════════════════════════════════════════════════════════════
// API:   describe(name, fn)        — register a suite
//        it(name, fn)              — register a test (fn receives assert)
//        TestRunner.runAll()       — run everything, returns {passed, failed}
//
// Assert methods passed to each test:
//   ok(val, msg)                   — truthy check
//   equal(a, b, msg)               — strict equality
//   notEqual(a, b, msg)            — strict inequality
//   hasProperty(obj, key, msg)     — key in obj
//   typeOf(val, type, msg)         — typeof val === type
//   isArray(val, msg)              — Array.isArray
//   greaterThan(a, b, msg)         — a > b

(function (root) {
    'use strict';

    var _suites = [];
    var _currentSuite = null;

    // ── Public registration API ──────────────────────────────

    root.describe = function (name, fn) {
        var suite = { name: name, tests: [] };
        _currentSuite = suite;
        fn();                        // synchronous — registers it() calls
        _suites.push(suite);
        _currentSuite = null;
    };

    root.it = function (name, fn) {
        if (!_currentSuite) throw new Error('it() must be inside describe()');
        _currentSuite.tests.push({ name: name, fn: fn });
    };

    // ── Assert helpers ───────────────────────────────────────

    function _fail(msg) { throw new Error(msg || 'assertion failed'); }

    function makeAssert() {
        return {
            ok: function (val, msg) {
                if (!val) _fail(msg || 'expected truthy, got ' + val);
            },
            equal: function (a, b, msg) {
                if (a !== b) _fail(msg || 'expected ' + JSON.stringify(b) + ', got ' + JSON.stringify(a));
            },
            notEqual: function (a, b, msg) {
                if (a === b) _fail(msg || 'expected not ' + JSON.stringify(b));
            },
            hasProperty: function (obj, key, msg) {
                if (!obj || !(key in obj)) _fail(msg || 'missing property: ' + key);
            },
            typeOf: function (val, type, msg) {
                if (typeof val !== type) _fail(msg || 'expected typeof ' + type + ', got ' + typeof val);
            },
            isArray: function (val, msg) {
                if (!Array.isArray(val)) _fail(msg || 'expected array');
            },
            greaterThan: function (a, b, msg) {
                if (!(a > b)) _fail(msg || 'expected ' + a + ' > ' + b);
            }
        };
    }

    // ── DOM renderer ─────────────────────────────────────────

    var _container = null;

    function getContainer() {
        if (!_container) {
            _container = document.getElementById('test-results');
            if (!_container) {
                _container = document.createElement('div');
                _container.id = 'test-results';
                document.body.appendChild(_container);
            }
        }
        return _container;
    }

    function renderSuite(suite, results) {
        var allPassed = results.every(function (r) { return r.passed; });
        var div = document.createElement('div');
        div.className = 'test-suite';

        var header = document.createElement('div');
        header.className = 'suite-header ' + (allPassed ? 'passed' : 'failed');
        header.textContent = (allPassed ? '✓ ' : '✗ ') + suite.name;
        header.onclick = function () {
            var body = div.querySelector('.suite-body');
            body.style.display = body.style.display === 'none' ? '' : 'none';
        };
        div.appendChild(header);

        var body = document.createElement('div');
        body.className = 'suite-body';
        results.forEach(function (r) {
            var row = document.createElement('div');
            row.className = 'test-row ' + (r.passed ? 'passed' : 'failed');
            row.innerHTML =
                '<span class="test-icon">' + (r.passed ? '✓' : '✗') + '</span>' +
                '<span class="test-name">' + _esc(r.name) + '</span>' +
                '<span class="test-ms">' + r.ms + ' ms</span>';
            body.appendChild(row);
            if (!r.passed) {
                var err = document.createElement('div');
                err.className = 'test-error';
                err.textContent = r.error;
                body.appendChild(err);
            }
        });
        div.appendChild(body);
        getContainer().appendChild(div);
    }

    function renderSummary(passed, failed, ms) {
        var div = document.createElement('div');
        div.className = 'test-summary ' + (failed > 0 ? 'failed' : 'passed');
        div.innerHTML =
            '<span class="test-count">' + passed + ' passed</span>' +
            (failed > 0 ? '<span class="test-count fail">' + failed + ' failed</span>' : '') +
            '<span class="test-time">' + ms + ' ms</span>';
        getContainer().insertBefore(div, getContainer().firstChild);
    }

    function _esc(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    // ── Runner ───────────────────────────────────────────────

    root.TestRunner = {
        runAll: async function () {
            var totalPassed = 0;
            var totalFailed = 0;
            var t0 = performance.now();

            for (var si = 0; si < _suites.length; si++) {
                var suite = _suites[si];
                var results = [];

                for (var ti = 0; ti < suite.tests.length; ti++) {
                    var test = suite.tests[ti];
                    var t1 = performance.now();
                    try {
                        var ret = test.fn(makeAssert());
                        if (ret && typeof ret.then === 'function') await ret;
                        results.push({ name: test.name, passed: true, ms: Math.round(performance.now() - t1) });
                        totalPassed++;
                    } catch (e) {
                        results.push({ name: test.name, passed: false, error: e.message || String(e), ms: Math.round(performance.now() - t1) });
                        totalFailed++;
                    }
                }
                renderSuite(suite, results);
            }

            var elapsed = Math.round(performance.now() - t0);
            renderSummary(totalPassed, totalFailed, elapsed);
            return { passed: totalPassed, failed: totalFailed, total: totalPassed + totalFailed, ms: elapsed };
        }
    };

})(typeof window !== 'undefined' ? window : globalThis);
