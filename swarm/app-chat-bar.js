// ZenAIos — Dashboard Quick Chat Bar (voice, camera, OCR, AI chat)
// Extracted from app.js to keep files under 600-line threshold.

document.addEventListener('DOMContentLoaded', () => {
    const bar      = document.getElementById('dashChatBar');
    const input    = document.getElementById('chatBarInput');
    const micBtn   = document.getElementById('chatBarMic');
    const camBtn   = document.getElementById('chatBarCamera');
    const sendBtn  = document.getElementById('chatBarSend');
    const overlay  = document.getElementById('chatCameraOverlay');
    const video    = document.getElementById('chatCameraVideo');
    const capBtn   = document.getElementById('chatCaptureBtn');
    const closeBtn = document.getElementById('chatCameraClose');
    const canvas   = document.getElementById('chatCaptureCanvas');
    if (!bar) return;

    // Set initial i18n placeholder
    if (input) input.placeholder = t('chatBarPlaceholder');

    // ── Chat messages area ────────────────────────────────────────────
    const msgArea = document.getElementById('chatMessagesArea');

    function appendMsg(role, text) {
        if (!msgArea) return;
        const div = document.createElement('div');
        div.className = role === 'user' ? 'chat-msg chat-msg-user' : 'chat-msg chat-msg-ai';
        div.textContent = text;
        msgArea.appendChild(div);
        msgArea.scrollTop = msgArea.scrollHeight;
        msgArea.style.display = 'flex';
    }

    function showTyping() {
        if (!msgArea) return null;
        const div = document.createElement('div');
        div.className = 'chat-msg chat-msg-ai chat-msg-typing';
        div.innerHTML = '<span></span><span></span><span></span>';
        msgArea.appendChild(div);
        msgArea.scrollTop = msgArea.scrollHeight;
        msgArea.style.display = 'flex';
        return div;
    }

    function appendPhotoMsg(dataUrl) {
        if (!msgArea) return;
        const div = document.createElement('div');
        div.className = 'chat-msg chat-msg-user';
        const img = document.createElement('img');
        img.src = dataUrl;
        img.style.cssText = 'max-width:160px;max-height:120px;border-radius:8px;display:block;';
        img.alt = 'Photo';
        div.appendChild(img);
        msgArea.appendChild(div);
        msgArea.scrollTop = msgArea.scrollHeight;
        msgArea.style.display = 'flex';
    }

    // Pre-process image for better OCR: scale up + grayscale + contrast boost
    function preprocessForOCR(dataUrl) {
        return new Promise(resolve => {
            const img = new Image();
            img.onload = () => {
                const scale = Math.max(1, Math.min(3, 1800 / Math.max(img.width, img.height)));
                const w = Math.round(img.width * scale);
                const h = Math.round(img.height * scale);
                const cv = document.createElement('canvas');
                cv.width = w; cv.height = h;
                const ctx = cv.getContext('2d');
                ctx.drawImage(img, 0, 0, w, h);
                ctx.filter = 'grayscale(1) contrast(1.6) brightness(1.1)';
                ctx.drawImage(img, 0, 0, w, h);
                resolve(cv.toDataURL('image/png'));
            };
            img.src = dataUrl;
        });
    }

    async function runOCR(dataUrl, typingEl) {
        if (typeof Tesseract === 'undefined') {
            if (typingEl) typingEl.remove();
            appendMsg('ai', '🔍 OCR not loaded yet — please try again in a moment.');
            return;
        }
        try {
            if (typingEl) typingEl.innerHTML = '🔍 Reading image…';
            const processed = await preprocessForOCR(dataUrl);
            const { data } = await Tesseract.recognize(processed, 'eng+ron', {
                logger: () => {},
                tessedit_pageseg_mode: '1',
            });
            if (typingEl) typingEl.remove();
            const extracted = (data.text || '').trim();
            const realWords = (extracted.match(/[A-Za-zÀ-ž]{3,}/g) || []).length;
            const nws = extracted.replace(/\s/g, '').length;
            const letterRatio = nws > 0 ? (extracted.replace(/[^A-Za-zÀ-ž0-9]/g, '').length / nws) : 0;
            if (realWords < 1 || letterRatio < 0.2) {
                appendMsg('ai', '🔍 No readable text found in this image.');
            } else {
                const safe = extracted
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;');
                const div = document.createElement('div');
                div.className = 'chat-msg chat-msg-ai';
                div.innerHTML = '<b>📄 Text extracted:</b>' +
                    '<pre style="white-space:pre-wrap;font-family:inherit;margin:6px 0 0;font-size:12px;max-height:200px;overflow-y:auto;">' +
                    safe + '</pre>';
                msgArea.appendChild(div);
                msgArea.scrollTop = msgArea.scrollHeight;
                msgArea.style.display = 'flex';
            }
        } catch (err) {
            if (typingEl) typingEl.remove();
            appendMsg('ai', '⚠️ OCR error: ' + err.message);
        }
    }

    // ── Send ──────────────────────────────────────────────────────────
    let voiceMode = false;

    function speakReply(text) {
        if (!window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        const utt = new SpeechSynthesisUtterance(text);
        const langMap = { ro: 'ro-RO', en: 'en-US', hu: 'hu-HU', de: 'de-DE', fr: 'fr-FR' };
        utt.lang = langMap[document.documentElement.lang] || 'en-US';
        utt.rate = 1.0;
        utt.pitch = 1.0;
        const voices = window.speechSynthesis.getVoices();
        const match = voices.find(v => v.lang.startsWith(utt.lang.split('-')[0]));
        if (match) utt.voice = match;
        window.speechSynthesis.speak(utt);
    }

    async function doSend() {
        const msg = input.value.trim();
        if (!msg) return;

        const badge = window._zenUserBadge || '';
        const isPhoto = !!input.dataset.photoDataUrl;

        if (isPhoto) {
            const dataUrl = input.dataset.photoDataUrl;
            delete input.dataset.photoDataUrl;
            input.value = '';
            appendPhotoMsg(dataUrl);
            const typing = showTyping();
            runOCR(dataUrl, typing);
            return;
        }

        appendMsg('user', msg);
        input.value = '';
        sendBtn.disabled = true;

        const typing = showTyping();

        try {
            const res = await fetch('/__chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: msg, badge, lang: document.documentElement.lang || 'en' }),
            });
            const data = await res.json();
            if (typing) typing.remove();
            if (data.reply) {
                appendMsg('ai', data.reply);
                if (voiceMode) speakReply(data.reply);
            } else if (data.error) {
                appendMsg('ai', '⚠️ ' + data.error);
            }
        } catch (err) {
            if (typing) typing.remove();
            appendMsg('ai', '⚠️ Could not reach AI assistant. Is the server running?');
        } finally {
            sendBtn.disabled = false;
        }
    }
    sendBtn.addEventListener('click', doSend);
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter') doSend();
        voiceMode = false;
    });

    // ── Microphone (Web Speech API) ───────────────────────────────────
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRec) {
        const rec = new SpeechRec();
        rec.continuous = false;
        rec.interimResults = true;
        let isRecording = false;
        let finalTranscript = '';

        rec.onresult = e => {
            let interim = '', final = '';
            for (let i = e.resultIndex; i < e.results.length; i++) {
                if (e.results[i].isFinal) final += e.results[i][0].transcript;
                else interim += e.results[i][0].transcript;
            }
            if (final) finalTranscript += final;
            input.value = finalTranscript || interim;
        };

        rec.onerror = () => stopMic(false);

        rec.onend = () => {
            const hadText = (finalTranscript || input.value).trim().length > 0;
            stopMic(false);
            if (hadText) {
                voiceMode = true;
                doSend();
            }
        };

        function startMic() {
            isRecording = true;
            finalTranscript = '';
            voiceMode = false;
            micBtn.classList.add('recording');
            micBtn.title = 'Listening… (stops automatically on silence)';
            const langMap = { ro: 'ro-RO', en: 'en-US', hu: 'hu-HU', de: 'de-DE', fr: 'fr-FR' };
            rec.lang = langMap[document.documentElement.lang] || 'en-US';
            if (window.speechSynthesis) window.speechSynthesis.cancel();
            try { rec.start(); } catch (_) {}
        }

        function stopMic(clearVoice = true) {
            isRecording = false;
            if (clearVoice) voiceMode = false;
            micBtn.classList.remove('recording');
            micBtn.title = 'Voice input';
            try { rec.stop(); } catch (_) {}
        }

        micBtn.addEventListener('click', () => {
            if (isRecording) stopMic(true);
            else startMic();
        });
    } else {
        micBtn.setAttribute('title', 'Voice input not supported in this browser');
    }

    // ── Camera (getUserMedia) ─────────────────────────────────────────
    let camStream = null;

    async function openCamera() {
        if (!window.isSecureContext) {
            showToast('⚠️ Camera requires HTTPS or localhost');
            return;
        }
        try {
            camStream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: 'environment' }, audio: false
            });
            video.srcObject = camStream;
            overlay.classList.add('active');
        } catch (_) {
            showToast('⚠️ Camera access denied');
        }
    }

    function closeCamera() {
        overlay.classList.remove('active');
        if (camStream) { camStream.getTracks().forEach(t => t.stop()); camStream = null; }
        video.srcObject = null;
    }

    function capturePhoto() {
        const w = video.videoWidth  || 320;
        const h = video.videoHeight || 240;
        canvas.width  = w;
        canvas.height = h;
        canvas.getContext('2d').drawImage(video, 0, 0, w, h);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
        closeCamera();
        showToast('📷 Photo captured — send to extract text 🔍');
        input.value = '[photo]';
        input.dataset.photoDataUrl = dataUrl;
    }

    camBtn.addEventListener('click', openCamera);
    capBtn.addEventListener('click', capturePhoto);
    closeBtn.addEventListener('click', closeCamera);
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && overlay.classList.contains('active')) closeCamera();
    });
});
