// ZenAIos — Chat Bot Tests
// =========================
// Unit tests for generateReply() keyword logic in chat.js.
// Requires window.ChatBot exposed by chat.js (window.ChatBot.generateReply).

// Shared helper — returns the generateReply function
function _gr() {
    return (typeof ChatBot !== 'undefined') ? ChatBot.generateReply : null;
}

describe('ChatBot.generateReply — availability', function () {
    it('ChatBot.generateReply is available', function (assert) {
        assert.ok(typeof ChatBot !== 'undefined', 'ChatBot global exists');
        assert.typeOf(ChatBot.generateReply, 'function', 'generateReply is a function');
    });
});

describe('ChatBot.generateReply — Beds', function () {
    it('"pat" triggers bed info', function (assert) {
        var r = _gr()('câte paturi libere?');
        assert.typeOf(r, 'string');
        assert.ok(r.includes('515'), 'contains total beds');
        assert.ok(r.includes('paturi'), 'contains beds keyword');
    });

    it('"bed" (English) triggers bed info', function (assert) {
        var r = _gr()('how many beds are free?');
        assert.ok(r.includes('515'));
    });

    it('"PAT" is case-insensitive', function (assert) {
        var r = _gr()('PAT disponibil');
        assert.ok(r.includes('515'));
    });
});

describe('ChatBot.generateReply — Alerts', function () {
    it('"alert" triggers alert list', function (assert) {
        var r = _gr()('show me the alerts');
        assert.ok(r.includes('Alerte active') || r.includes('lerte'));
        assert.ok(r.includes('sânge') || r.includes('CT'));
    });

    it('"urgent" triggers alert list', function (assert) {
        var r = _gr()('urgent issue');
        assert.ok(r.includes('lerte') || r.includes('sânge'));
    });

    it('"urgență" triggers alert list', function (assert) {
        var r = _gr()('urgență mare');
        assert.ok(r.includes('lerte'));
    });

    it('"ALERT" is case-insensitive', function (assert) {
        var r = _gr()('ALERT critical');
        assert.ok(r.includes('lerte'));
    });
});

describe('ChatBot.generateReply — Staff', function () {
    it('"doctor" triggers staff info', function (assert) {
        var r = _gr()('câți doctori sunt?');
        assert.ok(r.includes('14') || r.includes('medici'));
        assert.ok(r.includes('312') || r.includes('personal'));
    });

    it('"medic" triggers staff info', function (assert) {
        var r = _gr()('medic de gardă');
        assert.ok(r.includes('14') || r.includes('medici'));
    });

    it('"staff" triggers staff info', function (assert) {
        var r = _gr()('staff on duty');
        assert.ok(r.includes('14') || r.includes('personal'));
    });

    it('"personal" triggers staff info', function (assert) {
        var r = _gr()('info personal');
        assert.ok(r.includes('medici') || r.includes('312'));
    });
});

describe('ChatBot.generateReply — Reports', function () {
    it('"raport" triggers KPI report', function (assert) {
        var r = _gr()('dă-mi un raport');
        assert.ok(r.includes('KPI') || r.includes('28') || r.includes('urgențe'));
    });

    it('"report" (English) triggers KPI report', function (assert) {
        var r = _gr()('show daily report');
        assert.ok(r.includes('KPI') || r.includes('28'));
    });

    it('"kpi" triggers KPI report', function (assert) {
        var r = _gr()('kpi today');
        assert.ok(r.includes('KPI') || r.includes('28'));
    });
});

describe('ChatBot.generateReply — Greetings', function () {
    it('"salut" triggers greeting', function (assert) {
        var r = _gr()('salut');
        assert.ok(r.includes('Bună') || r.includes('bun'));
    });

    it('"buna" triggers greeting', function (assert) {
        var r = _gr()('buna ziua');
        assert.ok(r.includes('Bună') || r.includes('bun'));
    });

    it('"hello" triggers greeting', function (assert) {
        var r = _gr()('hello there');
        assert.ok(r.includes('Bună') || r.includes('bun'));
    });

    it('"hi" triggers greeting', function (assert) {
        var r = _gr()('hi');
        assert.ok(r.includes('Bună') || r.includes('bun'));
    });
});

describe('ChatBot.generateReply — Voice & Fallback', function () {
    it('"[voice message]" triggers voice reply', function (assert) {
        var r = _gr()('[voice message]');
        assert.ok(r.includes('vocal') || r.includes('transcriere'));
    });

    it('unknown input returns a non-empty string', function (assert) {
        var r = _gr()('xyzzy nonsense 12345');
        assert.typeOf(r, 'string');
        assert.ok(r.length > 0);
    });

    it('empty string returns a non-empty string', function (assert) {
        var r = _gr()('');
        assert.typeOf(r, 'string');
        assert.ok(r.length > 0);
    });

    it('null/undefined input returns a non-empty string', function (assert) {
        var r = _gr()(null);
        assert.typeOf(r, 'string');
        assert.ok(r.length > 0);
    });

    it('fallback response is one of the 4 known fallback strings', function (assert) {
        var knownFragments = [
            'paturi, personal, alerte sau rapoarte',
            'specializat în datele spitalului',
            'statistici specifice',
            'secție sau indicator',
        ];
        // Run 20 times to hit different random picks
        for (var i = 0; i < 20; i++) {
            var r = _gr()('zzz unknown zzz ' + i);
            var matched = knownFragments.some(function (f) { return r.includes(f); });
            assert.ok(matched, 'fallback[' + i + '] matched known fragment');
        }
    });
});

    // ── Return type ───────────────────────────────────────────────────────────

    it('every branch returns a string', function (assert) {
        var inputs = [
            'paturi', 'alert', 'doctor', 'raport', 'salut', '[voice message]', 'xyz'
        ];
        inputs.forEach(function (inp) {
            assert.typeOf(gr(inp), 'string', inp + ' → string');
        });
    });

    it('every branch returns HTML (contains angle bracket or emoji)', function (assert) {
        var inputs = ['paturi', 'alert', 'doctor', 'raport'];
        inputs.forEach(function (inp) {
            var r = gr(inp);
            var hasMarkup = r.includes('<') || r.includes('•') || r.includes('🛏') ||
                            r.includes('⚠') || r.includes('👨') || r.includes('📊');
            assert.ok(hasMarkup, inp + ' reply contains markup/emoji');
        });
    });
});
