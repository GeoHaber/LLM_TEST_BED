// ZenAIos — Interactive Dashboard Logic

document.addEventListener('DOMContentLoaded', () => {
    // ===== NAV HEIGHT CSS VAR (fixes chat bar above rim on all phones) =====
    (function setNavHeightVar() {
        const nav = document.querySelector('.bottom-nav');
        if (!nav) return;
        const update = () => {
            document.documentElement.style.setProperty('--bottom-nav-h', nav.offsetHeight + 'px');
        };
        update();
        // Re-measure if fonts/layout shift after load
        window.addEventListener('resize', update, { passive: true });
        if (typeof ResizeObserver !== 'undefined') {
            new ResizeObserver(update).observe(nav);
        }
    })();

    // ===== BUILD LANGUAGE SWITCHER =====
    const switcher = document.getElementById('langSwitcher');
    if (switcher) {
        getAvailableLanguages().forEach(({ code, flag, label }) => {
            const btn = document.createElement('button');
            btn.className = 'lang-btn' + (code === currentLang ? ' active' : '');
            btn.dataset.lang = code;
            btn.innerHTML = `<span class="lang-flag">${flag}</span><span class="lang-label">${label}</span>`;
            btn.addEventListener('click', () => setLanguage(code));
            switcher.appendChild(btn);
        });
    }

    // Apply saved language on load
    applyTranslations();

    // ===== LOAD HOSPITAL DATA =====

    function renderAvatar(el, name, initials, photoUrl) {
        if (!el) return;
        el.innerHTML = '';
        if (photoUrl) {
            const img = document.createElement('img');
            img.alt = name || '';
            img.src = photoUrl;
            el.appendChild(img);
        } else {
            el.textContent = initials;
        }
    }

    async function initHospitalDetails() {
        try {
            const resp = await fetch(`hospital-data.json?v=${Date.now()}`);
            const json = await resp.json();
            
            // Populate static brand/doctor UI
            const hospName = document.getElementById('hospName');
            const hospSub = document.getElementById('hospSub');
            const docName = document.getElementById('docName');
            const docRole = document.getElementById('docRole');
            const docAvatar = document.getElementById('docAvatar');
            
            if (hospName) hospName.textContent = json.hospital.name;
            if (hospSub) hospSub.textContent = json.hospital.sub;
            if (docName) docName.textContent = json.doctor.name;
            renderAvatar(docAvatar, json.doctor.name, json.doctor.avatar, json.doctor.photoUrl);

            // Update hero if present
            const heroTitle = document.querySelector('.hero-title');
            const heroDesc = document.querySelector('.hero-desc');
            if (heroTitle) heroTitle.innerHTML = json.hospital.heroTitle;
            if (heroDesc) heroDesc.textContent = json.hospital.heroDesc;

            // This will fill the i18n placeholder {role} · {hospital}
            window.addEventListener('languageChanged', () => {
                const roleEl = document.getElementById('docRole');
                if (roleEl) {
                    roleEl.textContent = tpl('doctorRole', {
                        role: json.doctor.role,
                        hospital: json.hospital.name
                    });
                }
            });
            // Initial call for role
            if (docRole) {
                docRole.textContent = tpl('doctorRole', {
                    role: json.doctor.role,
                    hospital: json.hospital.name
                });
            }

        } catch (err) {
            console.error('Failed to init hospital details:', err);
        }
    }
    initHospitalDetails();

    // ===== LIVE DATA (from data-sync.js) =====
    // This replaces the old hardcoded hospitalData.
    // data-sync.js fires 'dataUpdated' with fresh data from API or cache.
    let hospitalData = null;

    // Listen for data updates from the sync engine
    window.addEventListener('dataUpdated', (e) => {
        const { data, source, online } = e.detail;
        hospitalData = data;
        window._zenHospitalData = data;
        updateDashboardUI(data);
    });

    // ===== OFFLINE / ONLINE BANNER =====
    const offlineBanner = document.getElementById('offlineBanner');
    const offlineAge = document.getElementById('offlineAge');

    window.addEventListener('connectivityChanged', (e) => {
        if (e.detail.online) {
            offlineBanner.style.display = 'none';
        } else {
            offlineBanner.style.display = 'block';
            updateOfflineAge();
        }
    });

    window.addEventListener('dataSyncError', () => {
        if (!navigator.onLine && offlineBanner) {
            offlineBanner.style.display = 'block';
            updateOfflineAge();
        }
    });

    function updateOfflineAge() {
        if (!hospitalData || !hospitalData._fetchedAt) return;
        const ago = Math.round((Date.now() - hospitalData._fetchedAt) / 60000);
        if (offlineAge) {
            offlineAge.textContent = ago < 1 ? '(< 1 min)' : `(${ago} min în urmă)`;
        }
    }

    // Show banner on load if already offline
    if (!navigator.onLine && offlineBanner) {
        offlineBanner.style.display = 'block';
    }

    // ===== COUNTER ANIMATION =====
    function animateCounter(el, target, duration = 1200) {
        const start = 0;
        const startTime = performance.now();

        function update(currentTime) {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            const current = Math.round(start + (target - start) * eased);
            el.textContent = current;
            if (progress < 1) requestAnimationFrame(update);
        }

        requestAnimationFrame(update);
    }

    // Animate all stat values on load
    document.querySelectorAll('.stat-value').forEach(el => {
        const val = parseInt(el.textContent);
        if (!isNaN(val)) animateCounter(el, val, 1500);
    });

    document.querySelectorAll('.kpi-value').forEach(el => {
        const val = parseInt(el.textContent);
        if (!isNaN(val)) animateCounter(el, val, 1800);
    });

    document.querySelectorAll('.section-stat-val').forEach(el => {
        const val = parseInt(el.textContent);
        if (!isNaN(val)) animateCounter(el, val, 1200);
    });

    // ===== UPDATE DASHBOARD FROM DATA =====
    let _prevData = null;   // previous snapshot for anomaly comparison

    function updateDashboardUI(data) {
        if (!data) return;

        // ── Anomaly detection (compare vs last snapshot) ──────────────────
        if (_prevData) {
            const THRESHOLDS = { occupancyRate: 95, activeAlerts: 5, urgentDelta: 3 };
            if (data.occupancyRate >= THRESHOLDS.occupancyRate && _prevData.occupancyRate < THRESHOLDS.occupancyRate) {
                showToast(`🚨 Bed occupancy critical: ${data.occupancyRate}%`);
                fetch('/__anomaly', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'beds_spike', message: `Occupancy at ${data.occupancyRate}%`,
                        value: data.occupancyRate, threshold: THRESHOLDS.occupancyRate }) }).catch(() => {});
            }
            if (data.activeAlerts > THRESHOLDS.activeAlerts && _prevData.activeAlerts <= THRESHOLDS.activeAlerts) {
                showToast(`⚠️ Alert surge: ${data.activeAlerts} active alerts`);
                fetch('/__anomaly', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'alerts_surge', message: `${data.activeAlerts} active alerts`,
                        value: data.activeAlerts, threshold: THRESHOLDS.activeAlerts }) }).catch(() => {});
            }
            const prevUrgent = (_prevData.sections || []).reduce((s, x) => s + (x.urgent || 0), 0);
            const curUrgent  = (data.sections || []).reduce((s, x) => s + (x.urgent || 0), 0);
            if (curUrgent - prevUrgent >= THRESHOLDS.urgentDelta) {
                showToast(`🚑 Triage spike: +${curUrgent - prevUrgent} urgent cases`);
                fetch('/__anomaly', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'triage_high', message: `Urgent cases up by ${curUrgent - prevUrgent}`,
                        value: curUrgent, threshold: prevUrgent + THRESHOLDS.urgentDelta }) }).catch(() => {});
            }
        }
        _prevData = data;

        // Phone stats
        const statValues = document.querySelectorAll('.stats-grid .stat-value');
        if (statValues[0]) statValues[0].textContent = data.occupiedBeds;
        if (statValues[1]) statValues[1].textContent = data.freeBeds;
        if (statValues[2]) statValues[2].textContent = data.activeDoctors;
        if (statValues[3]) statValues[3].textContent = data.activeAlerts;

        // Badge
        const badges = document.querySelectorAll('.stat-badge');
        if (badges[0]) {
            badges[0].innerHTML = `<span class="badge-icon">🔥</span> ${data.occupancyRate}%`;
        }

        // KPI sidebar
        const kpiValues = document.querySelectorAll('.kpi-value');
        if (kpiValues[0]) kpiValues[0].textContent = data.totalBeds;
        if (kpiValues[1]) kpiValues[1].textContent = data.totalStaff;
        if (kpiValues[2]) kpiValues[2].textContent = data.activeAlerts;

        // KPI detail (translated)
        const kpiDetail1 = document.querySelector('[data-i18n-key="kpiDetail1"]');
        if (kpiDetail1) {
            kpiDetail1.textContent = tpl('kpiDetail1', {
                occ: data.occupiedBeds,
                free: data.freeBeds,
                pct: data.occupancyRate
            });
        }

        // Update static section cards (first 2 departments shown on dashboard)
        const sectionCards = document.querySelectorAll('.section-card:not(.dynamic)');
        data.sections.slice(0, sectionCards.length).forEach((sec, i) => {
            const card = sectionCards[i];
            if (!card) return;
            const vals = card.querySelectorAll('.section-stat-val');
            if (vals[0]) vals[0].textContent = sec.occupied;
            if (vals[1]) vals[1].textContent = sec.free;
            if (vals[2]) vals[2].textContent = sec.urgent;
            const pctEl = card.querySelector('.section-pct');
            if (pctEl) pctEl.textContent = sec.pct + '%';
        });

        // Flash effect
        statValues.forEach(el => {
            el.style.transition = 'color 0.3s';
            el.style.color = '#3B5BDB';
            setTimeout(() => el.style.color = '', 600);
        });

        // Update identity if available
        if (data.hospital && data.doctor) {
            const hospName = document.getElementById('hospName');
            const hospSub = document.getElementById('hospSub');
            const docName = document.getElementById('docName');
            const docRole = document.getElementById('docRole');
            const docAvatar = document.getElementById('docAvatar');
            
            if (hospName) hospName.textContent = data.hospital.name;
            if (hospSub) hospSub.textContent = data.hospital.sub;
            if (docName) docName.textContent = data.doctor.name;
            renderAvatar(docAvatar, data.doctor.name, data.doctor.avatar, data.doctor.photoUrl);

            if (docRole) {
                docRole.textContent = tpl('doctorRole', {
                    role: data.doctor.role,
                    hospital: data.hospital.name
                });
            }

            // Hero section
            const heroTitle = document.querySelector('.hero-title');
            const heroDesc = document.querySelector('.hero-desc');
            if (heroTitle) heroTitle.innerHTML = data.hospital.heroTitle;
            if (heroDesc) heroDesc.textContent = data.hospital.heroDesc;
        }

        // Refresh active view if not on dashboard
        if (currentView !== 'dashboard') {
            switchView(currentView);
        }
    }

    // ===== NAV / VIEW ROUTING =====
    const navItems = document.querySelectorAll('.nav-item');
    const dashboardEls = document.querySelectorAll('.phone-header, .alert-banner, .stats-grid, .sections-header, .section-card:not(.dynamic)');
    const viewPages = {
        beds: document.getElementById('viewBeds'),
        alerts: document.getElementById('viewAlerts'),
        staff: document.getElementById('viewStaff'),
        reports: document.getElementById('viewReports'),
        chat: document.getElementById('viewChat'),
    };
    let currentView = 'dashboard';

    function switchView(viewName) {
        currentView = viewName;
        logAction('switchView', { view: viewName });

        // Toggle dashboard elements
        const showDash = viewName === 'dashboard';
        dashboardEls.forEach(el => el.style.display = showDash ? '' : 'none');

        // Hide all dynamic section cards too
        document.querySelectorAll('.section-card.dynamic').forEach(el => el.style.display = showDash ? '' : 'none');

        // Show quick-chat bar only on dashboard
        const chatBar = document.getElementById('dashChatBar');
        if (chatBar) chatBar.style.display = showDash ? 'flex' : 'none';
        const chatMsgs = document.getElementById('chatMessagesArea');
        if (chatMsgs) chatMsgs.style.display = (showDash && chatMsgs.children.length) ? 'flex' : 'none';

        // Toggle view pages
        Object.entries(viewPages).forEach(([key, page]) => {
            if (page) page.style.display = key === viewName ? 'block' : 'none';
        });

        // Update nav active state
        navItems.forEach(n => {
            const isActive = n.dataset.view === viewName;
            n.classList.toggle('active', isActive);
            if (isActive) n.setAttribute('aria-current', 'page');
            else n.removeAttribute('aria-current');
        });

        // Populate view data
        if (hospitalData) {
            if (viewName === 'beds') renderBedsView(hospitalData);
            if (viewName === 'alerts') renderAlertsView(hospitalData);
            if (viewName === 'staff') renderStaffView(hospitalData);
            if (viewName === 'reports') renderReportsView(hospitalData);
        }

        // Scroll to top of phone content
        const phoneContent = document.querySelector('.phone-content');
        if (phoneContent) phoneContent.scrollTop = 0;
    }

    navItems.forEach(item => {
        function activate() {
            const view = item.dataset.view || 'dashboard';
            switchView(view);
        }
        item.addEventListener('click', activate);
    });

    // ===== "SEE ALL" SECTIONS =====
    const seeAll = document.querySelector('.see-all');
    if (seeAll) {
        seeAll.addEventListener('click', () => {
            toggleAllSections();
        });
    }

    let showingAll = false;
    function toggleAllSections() {
        const container = document.querySelector('.sections-header').parentElement;
        const existing = container.querySelectorAll('.section-card.dynamic');

        if (showingAll) {
            existing.forEach(el => {
                el.style.animation = 'fadeInUp 0.3s ease-out reverse';
                setTimeout(() => el.remove(), 300);
            });
            seeAll.textContent = t('seeAll');
            showingAll = false;
            return;
        }

        const extraSections = hospitalData.sections.slice(2);
        const bottomNav = container.querySelector('.bottom-nav');

        extraSections.forEach((sec, i) => {
            const card = document.createElement('div');
            card.className = 'section-card dynamic';
            card.style.animation = `fadeInUp 0.4s ease-out ${i * 0.1}s both`;
            card.innerHTML = `
                <div class="section-top">
                    <span class="section-dot" style="background:${sec.color};"></span>
                    <span class="section-name" data-i18n="${sec.nameKey}">${t(sec.nameKey)}</span>
                    <span class="section-pct" style="background:#e8f5e8;color:#2b8a3e;">${sec.pct}%</span>
                </div>
                <div class="section-stats">
                    <div class="section-stat">
                        <div class="section-stat-val">${sec.occupied}</div>
                        <div class="section-stat-lbl" data-i18n="occupied">${t('occupied')}</div>
                    </div>
                    <div class="section-stat">
                        <div class="section-stat-val" style="color:#2b8a3e;">${sec.free}</div>
                        <div class="section-stat-lbl" data-i18n="free">${t('free')}</div>
                    </div>
                    <div class="section-stat">
                        <div class="section-stat-val" style="color:#e03131;">${sec.urgent}</div>
                        <div class="section-stat-lbl" data-i18n="emergency">${t('emergency')}</div>
                    </div>
                </div>
            `;
            card.addEventListener('click', () => {
                showToast(tpl('toastSection', { name: t(sec.nameKey) }));
            });
            bottomNav.parentElement.insertBefore(card, bottomNav);
        });

        seeAll.textContent = t('seeLess');
        showingAll = true;
    }

    // ===== STAT CARD CLICK =====
    document.querySelectorAll('.stat-card').forEach(card => {
        function activate() {
            const label = card.querySelector('.stat-label').textContent;
            showToast(tpl('toastDetail', { label }));
        }
        card.addEventListener('click', activate);
    });

    // ===== SECTION CARD CLICK =====
    document.querySelectorAll('.section-card:not(.dynamic)').forEach(card => {
        card.setAttribute('role', 'button');
        card.setAttribute('tabindex', '0');
        function activate() {
            const name = card.querySelector('.section-name').textContent;
            if (hospitalData) {
                const sec = hospitalData.sections.find(s => t(s.nameKey) === name);
                if (sec) { showDeptDetail(sec); return; }
            }
            showToast(tpl('toastSection', { name }));
        }
        card.addEventListener('click', activate);
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); }
        });
    });

    // ===== ALERT CLICK =====
    document.querySelectorAll('.alert-item').forEach(item => {
        item.addEventListener('click', () => {
            const name = item.querySelector('.alert-name').textContent;
            showToast(tpl('toastAlert', { name }));
        });
    });

    // View renderers are in views.js (renderBedsView, renderAlertsView, etc.)

    // Department modal and toast are in views.js
    initDeptModal();

    // ===== CLOCK UPDATE =====
    const localeMap = { ro: 'ro-RO', en: 'en-GB', hu: 'hu-HU', de: 'de-DE', fr: 'fr-FR' };

    function updateClock() {
        const now = new Date();
        const locale = localeMap[currentLang] || 'ro-RO';
        const timeStr = now.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
        const clockEl = document.querySelector('.phone-time');
        if (clockEl) clockEl.textContent = timeStr;
    }

    updateClock();
    setInterval(updateClock, 30000);

    // ===== GREETING UPDATE =====
    function updateGreeting() {
        const hour = new Date().getHours();
        const greetEl = document.querySelector('.greeting');
        if (!greetEl) return;

        if (hour < 12) greetEl.textContent = t('greetMorning');
        else if (hour < 18) greetEl.textContent = t('greetAfternoon');
        else greetEl.textContent = t('greetEvening');
    }

    updateGreeting();

    // ===== UPDATE KPI DETAIL ON LOAD =====
    function updateKpiDetail() {
        if (!hospitalData) return;
        const kpiDetail1 = document.querySelector('[data-i18n-key="kpiDetail1"]');
        if (kpiDetail1) {
            kpiDetail1.textContent = tpl('kpiDetail1', {
                occ: hospitalData.occupiedBeds,
                free: hospitalData.freeBeds,
                pct: hospitalData.occupancyRate
            });
        }
    }
    updateKpiDetail();

    // ===== ACTION BEACONING — log significant user actions to server =========
    function logAction(action, detail = {}) {
        const badge = window._zenUserBadge || '';
        fetch('/__log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ badge, action, detail }),
        }).catch(() => {});   // fire-and-forget, silently ignore offline
    }

    // ===== DASHBOARD QUICK CHAT BAR — see app-chat-bar.js =====

    // ===== REACT TO LANGUAGE CHANGES =====
    window.addEventListener('languageChanged', () => {
        updateGreeting();
        updateClock();
        updateKpiDetail();
        const chatInput = document.getElementById('chatBarInput');
        if (chatInput) chatInput.placeholder = t('chatBarPlaceholder');

        // Re-translate dynamic sections if showing
        if (showingAll) {
            const container = document.querySelector('.sections-header').parentElement;
            container.querySelectorAll('.section-card.dynamic').forEach(el => el.remove());
            showingAll = false;
            toggleAllSections();
        }

        // Update see-all button text
        if (seeAll) {
            seeAll.textContent = showingAll ? t('seeLess') : t('seeAll');
        }
    });

});
