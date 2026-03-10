// ZenAIos — Chat Interface
// =========================
// Handles messaging, file attachments, and voice recording.

(function () {
    'use strict';

    // DOM refs
    const chatMessages = document.getElementById('chatMessages');
    const chatInput = document.getElementById('chatInput');
    const chatSendBtn = document.getElementById('chatSendBtn');
    const chatMicBtn = document.getElementById('chatMicBtn');
    const chatAttachBtn = document.getElementById('chatAttachBtn');
    const chatFileInput = document.getElementById('chatFileInput');
    const chatAttachPreview = document.getElementById('chatAttachPreview');
    const chatInputArea = document.getElementById('chatInputArea');
    const chatRecordingBar = document.getElementById('chatRecordingBar');
    const chatRecordTime = document.getElementById('chatRecordTime');
    const chatRecordCancel = document.getElementById('chatRecordCancel');
    const chatRecordSend = document.getElementById('chatRecordSend');

    // Expose pure logic for unit tests (functions are hoisted)
    window.ChatBot = {
        generateReply:  generateReply,
        escapeHtml:     escapeHtml,
        formatSize:     formatSize,
        formatDuration: formatDuration,
        formatTime:     formatTime,
        getFileIcon:    getFileIcon,
        clearConversation: clearConversation,
    };

    if (!chatMessages) return; // Not on this page

    let pendingFiles = [];
    let mediaRecorder = null;
    let audioChunks = [];
    let recordingTimer = null;
    let recordingSeconds = 0;

    // ===== TEXT INPUT =====

    // Auto-resize textarea
    chatInput.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        toggleSendMic();
    });

    chatInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    chatSendBtn.addEventListener('click', sendMessage);

    function toggleSendMic() {
        const hasContent = chatInput.value.trim().length > 0 || pendingFiles.length > 0;
        chatSendBtn.style.display = hasContent ? 'flex' : 'none';
        chatMicBtn.style.display = hasContent ? 'none' : 'flex';
    }

    // ===== SEND MESSAGE =====

    function sendMessage() {
        const text = chatInput.value.trim();
        const files = [...pendingFiles];

        if (!text && files.length === 0) return;

        // Build message with attachments
        const bubble = createBubble('user');

        files.forEach(function (f) {
            if (f.type.startsWith('image/')) {
                var img = document.createElement('img');
                img.src = f.dataUrl;
                img.className = 'chat-bubble-image';
                img.alt = f.name;
                bubble.content.appendChild(img);
            } else {
                var fileEl = document.createElement('div');
                fileEl.className = 'chat-bubble-file';
                fileEl.innerHTML =
                    '<span class="chat-bubble-file-icon">' + getFileIcon(f.name) + '</span>' +
                    '<div><div class="chat-bubble-file-name">' + escapeHtml(f.name) + '</div>' +
                    '<div class="chat-bubble-file-size">' + formatSize(f.size) + '</div></div>';
                bubble.content.appendChild(fileEl);
            }
        });

        if (text) {
            var textNode = document.createElement('span');
            textNode.textContent = text;
            bubble.content.appendChild(textNode);
        }

        chatMessages.appendChild(bubble.el);

        // Reset
        chatInput.value = '';
        chatInput.style.height = 'auto';
        clearAttachments();
        toggleSendMic();
        scrollToBottom();

        // Bot response
        simulateBotReply(text);
    }

    // ===== FILE ATTACHMENTS =====

    chatAttachBtn.addEventListener('click', function () {
        chatFileInput.click();
    });

    chatFileInput.addEventListener('change', function () {
        Array.from(this.files).forEach(function (file) {
            if (file.size > 10 * 1024 * 1024) return; // 10MB limit
            var reader = new FileReader();
            reader.onload = function (e) {
                pendingFiles.push({
                    name: file.name,
                    size: file.size,
                    type: file.type,
                    dataUrl: e.target.result
                });
                renderAttachmentPreviews();
                toggleSendMic();
            };
            reader.readAsDataURL(file);
        });
        this.value = '';
    });

    function renderAttachmentPreviews() {
        chatAttachPreview.innerHTML = '';
        if (pendingFiles.length === 0) {
            chatAttachPreview.style.display = 'none';
            return;
        }
        chatAttachPreview.style.display = 'flex';
        pendingFiles.forEach(function (f, i) {
            var thumb = document.createElement('div');
            thumb.className = 'chat-attach-thumb';

            if (f.type.startsWith('image/')) {
                thumb.innerHTML = '<img src="' + f.dataUrl + '" alt="' + escapeHtml(f.name) + '">';
            } else {
                thumb.innerHTML = '<div class="chat-attach-thumb-file">' + getFileIcon(f.name) + '</div>';
            }

            var removeBtn = document.createElement('button');
            removeBtn.className = 'chat-attach-remove';
            removeBtn.textContent = '×';
            removeBtn.setAttribute('type', 'button');
            removeBtn.addEventListener('click', function () {
                pendingFiles.splice(i, 1);
                renderAttachmentPreviews();
                toggleSendMic();
            });
            thumb.appendChild(removeBtn);
            chatAttachPreview.appendChild(thumb);
        });
    }

    function clearAttachments() {
        pendingFiles = [];
        chatAttachPreview.innerHTML = '';
        chatAttachPreview.style.display = 'none';
    }

    // ===== VOICE RECORDING =====

    chatMicBtn.addEventListener('click', function () {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            stopRecording(false);
            return;
        }
        startRecording();
    });

    chatRecordCancel.addEventListener('click', function () {
        stopRecording(false);
    });

    chatRecordSend.addEventListener('click', function () {
        stopRecording(true);
    });

    function startRecording() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            showChatToast('Microfonul nu este disponibil');
            return;
        }

        navigator.mediaDevices.getUserMedia({ audio: true })
            .then(function (stream) {
                audioChunks = [];
                mediaRecorder = new MediaRecorder(stream);
                mediaRecorder.ondataavailable = function (e) {
                    if (e.data.size > 0) audioChunks.push(e.data);
                };
                mediaRecorder.start();

                // UI
                chatMicBtn.classList.add('recording');
                chatInputArea.style.display = 'none';
                chatRecordingBar.style.display = 'flex';
                recordingSeconds = 0;
                chatRecordTime.textContent = '0:00';
                recordingTimer = setInterval(function () {
                    recordingSeconds++;
                    var m = Math.floor(recordingSeconds / 60);
                    var s = recordingSeconds % 60;
                    chatRecordTime.textContent = m + ':' + (s < 10 ? '0' : '') + s;
                }, 1000);
            })
            .catch(function () {
                showChatToast('Acces microfon refuzat');
            });
    }

    function stopRecording(send) {
        if (!mediaRecorder) return;

        mediaRecorder.onstop = function () {
            // Stop all tracks
            mediaRecorder.stream.getTracks().forEach(function (t) { t.stop(); });

            if (send && audioChunks.length > 0) {
                var blob = new Blob(audioChunks, { type: 'audio/webm' });
                sendVoiceMessage(blob);
            }

            // Reset UI
            mediaRecorder = null;
            audioChunks = [];
            chatMicBtn.classList.remove('recording');
            chatInputArea.style.display = 'flex';
            chatRecordingBar.style.display = 'none';
            clearInterval(recordingTimer);
        };

        mediaRecorder.stop();
    }

    function sendVoiceMessage(blob) {
        var bubble = createBubble('user');
        var audioEl = document.createElement('div');
        audioEl.className = 'chat-audio-msg';
        audioEl.innerHTML = '🎤';
        var audio = document.createElement('audio');
        audio.controls = true;
        audio.src = URL.createObjectURL(blob);
        audioEl.appendChild(audio);
        bubble.content.appendChild(audioEl);

        var dur = document.createElement('span');
        dur.style.cssText = 'font-size:11px;color:rgba(255,255,255,0.7);';
        dur.textContent = formatDuration(recordingSeconds);
        bubble.content.appendChild(dur);

        chatMessages.appendChild(bubble.el);
        scrollToBottom();

        // Bot response
        simulateBotReply('[voice message]');
    }

    // ===== BOT REPLY — SSE streaming via /__chat-stream, fallback to /__chat =====

    function simulateBotReply(userText) {
        // Show typing indicator immediately
        var typing = document.createElement('div');
        typing.className = 'chat-typing';
        typing.innerHTML = '<span></span><span></span><span></span>';
        chatMessages.appendChild(typing);
        scrollToBottom();

        var badge = (window._zenUserBadge || '');
        var lang  = (localStorage.getItem('zenAIos-lang') || 'ro');

        // Try SSE streaming first
        _streamChat(userText, badge, lang, typing)
            .catch(function () {
                // Streaming failed — fall back to non-streaming /__chat
                _nonStreamChat(userText, badge, lang, typing);
            });
    }

    function _streamChat(userText, badge, lang, typing) {
        return fetch('/__chat-stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: userText, badge: badge, lang: lang })
        }).then(function (res) {
            if (!res.ok || !res.body) throw new Error('no stream');

            // Remove typing indicator, create bot bubble for progressive rendering
            if (chatMessages.contains(typing)) chatMessages.removeChild(typing);
            var bubble = createBubble('bot');
            chatMessages.appendChild(bubble.el);
            scrollToBottom();

            var reader = res.body.getReader();
            var decoder = new TextDecoder();
            var fullReply = '';
            var buffer = '';

            function pump() {
                return reader.read().then(function (result) {
                    if (result.done) return;
                    buffer += decoder.decode(result.value, { stream: true });

                    // Parse SSE lines
                    var lines = buffer.split('\n');
                    buffer = lines.pop(); // keep incomplete line in buffer
                    for (var i = 0; i < lines.length; i++) {
                        var line = lines[i].trim();
                        if (line === 'data: [DONE]') return;
                        if (line.indexOf('data: ') === 0) {
                            try {
                                var data = JSON.parse(line.substring(6));
                                if (data.token) {
                                    fullReply += data.token;
                                    bubble.content.innerHTML = escapeHtml(fullReply).replace(/\n/g, '<br>');
                                    scrollToBottom();
                                }
                                if (data.error) {
                                    fullReply += ' ❌ ' + data.error;
                                    bubble.content.innerHTML = escapeHtml(fullReply).replace(/\n/g, '<br>');
                                }
                            } catch (e) { /* skip malformed */ }
                        }
                    }
                    return pump();
                });
            }
            return pump();
        });
    }

    function _nonStreamChat(userText, badge, lang, typing) {
        fetch('/__chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: userText, badge: badge, lang: lang })
        })
        .then(function (res) { return res.json(); })
        .then(function (data) {
            if (chatMessages.contains(typing)) chatMessages.removeChild(typing);
            var bubble = createBubble('bot');
            if (data.reply) {
                bubble.content.innerHTML = escapeHtml(data.reply).replace(/\n/g, '<br>');
            } else {
                bubble.content.innerHTML = generateReply(userText);
            }
            chatMessages.appendChild(bubble.el);
            scrollToBottom();
        })
        .catch(function () {
            // Server unreachable — fall back to local keyword reply
            if (chatMessages.contains(typing)) chatMessages.removeChild(typing);
            var bubble = createBubble('bot');
            bubble.content.innerHTML = generateReply(userText);
            chatMessages.appendChild(bubble.el);
            scrollToBottom();
        });
    }

    function generateReply(text) {
        var lower = (text || '').toLowerCase();

        if (lower.includes('pat') || lower.includes('bed')) {
            return '🛏️ <b>Situație paturi:</b><br>' +
                '• Total: 515 paturi<br>' +
                '• Ocupate: 487 (94%)<br>' +
                '• Libere: 28<br><br>' +
                'Secția cu cele mai multe paturi libere: <b>Pediatrie</b> (8 libere).';
        }
        if (lower.includes('alert') || lower.includes('urgent') || lower.includes('urgență')) {
            return '⚠️ <b>Alerte active:</b><br>' +
                '1. 🩸 Lipsă sânge O Rh– (critic)<br>' +
                '2. 🔧 CT defect Radiologie<br>' +
                '3. 📋 Raport CAS aprobat<br><br>' +
                'Doriți detalii despre vreo alertă?';
        }
        if (lower.includes('doctor') || lower.includes('medic') || lower.includes('personal') || lower.includes('staff')) {
            return '👨‍⚕️ <b>Personal de gardă:</b><br>' +
                '• 14 medici activi<br>' +
                '• 312 personal total<br>' +
                '• Prezență: 98.1%<br><br>' +
                'Gardă completă pe toate secțiile.';
        }
        if (lower.includes('raport') || lower.includes('report') || lower.includes('kpi')) {
            return '📊 <b>Raport KPI zilnic:</b><br>' +
                '• 28 urgențe procesate azi<br>' +
                '• Timp mediu triaj: 18 min<br>' +
                '• Raport CAS: Aprobat ✅<br><br>' +
                'Raportul complet este disponibil în secțiunea Rapoarte.';
        }
        if (lower.includes('salut') || lower.includes('buna') || lower.includes('hello') || lower.includes('hi')) {
            return 'Bună ziua! 👋 Cum vă pot ajuta astăzi?';
        }
        if (lower === '[voice message]') {
            return '🎧 Am primit mesajul vocal. ' +
                'Momentan procesez doar text, dar funcționalitatea de transcriere vine în curând! ' +
                'Vă pot ajuta cu ceva anume?';
        }

        var responses = [
            'Înțeleg. Vă pot oferi informații despre paturi, personal, alerte sau rapoarte. Ce anume doriți?',
            'Mulțumesc pentru mesaj! 📋 Sunt specializat în datele spitalului. Întrebați-mă despre orice secție sau indicator.',
            'Am notat. Dacă aveți nevoie de statistici specifice, nu ezitați să întrebați!',
            'Verifico datele... Puteți specifica despre ce secție sau indicator doriți informații?',
        ];
        return responses[Math.floor(Math.random() * responses.length)];
    }

    // ===== HELPERS =====

    function createBubble(who) {
        var el = document.createElement('div');
        el.className = 'chat-bubble ' + who;
        var content = document.createElement('div');
        content.className = 'chat-bubble-content';
        el.appendChild(content);
        var time = document.createElement('div');
        time.className = 'chat-bubble-time';
        time.textContent = formatTime(new Date());
        el.appendChild(time);
        return { el: el, content: content };
    }

    function scrollToBottom() {
        requestAnimationFrame(function () {
            chatMessages.scrollTop = chatMessages.scrollHeight;
        });
    }

    function formatTime(d) {
        return (d.getHours() < 10 ? '0' : '') + d.getHours() + ':' +
               (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
    }

    function formatDuration(secs) {
        var m = Math.floor(secs / 60);
        var s = secs % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function getFileIcon(name) {
        var ext = (name || '').split('.').pop().toLowerCase();
        var icons = {
            pdf: '📕', doc: '📘', docx: '📘', xls: '📗', xlsx: '📗',
            csv: '📗', txt: '📄', zip: '📦', rar: '📦',
        };
        return icons[ext] || '📎';
    }

    function escapeHtml(str) {
        var d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    }

    function showChatToast(msg) {
        var toast = document.createElement('div');
        toast.style.cssText = 'position:fixed;bottom:100px;left:50%;transform:translateX(-50%);' +
            'background:#1a1a2e;color:#fff;padding:8px 20px;border-radius:20px;font-size:13px;' +
            'z-index:9999;opacity:0;transition:opacity 0.3s;font-family:Inter,sans-serif;';
        toast.textContent = msg;
        document.body.appendChild(toast);
        requestAnimationFrame(function () { toast.style.opacity = '1'; });
        setTimeout(function () {
            toast.style.opacity = '0';
            setTimeout(function () { toast.remove(); }, 300);
        }, 2500);
    }

    function clearConversation() {
        var badge = (window._zenUserBadge || '');
        fetch('/__chat-clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ badge: badge })
        }).catch(function () { /* ignore */ });
        // Clear local chat UI
        if (chatMessages) chatMessages.innerHTML = '';
    }

    // Initial state
    toggleSendMic();


})();
