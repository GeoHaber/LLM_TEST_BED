// ZenAIos — API Configuration & Data Contract
// ============================================
//
// This file defines the data contract and API integration settings.
// To connect ZenAIos to a real hospital API:
//
// 1. Set the API_URL below to your endpoint
// 2. Ensure the API returns JSON matching the DATA_CONTRACT shape
// 3. Add any required auth headers in HEADERS
//
// The data-sync layer (data-sync.js) handles:
//   - Stale-while-revalidate caching
//   - IndexedDB persistence
//   - 24h history snapshots
//   - Offline fallback
//   - Tab visibility pausing
//
// DATA CONTRACT — Your API must return this exact shape:
//   totalBeds (number), occupiedBeds (number), freeBeds (number),
//   occupancyRate (number 0-100), activeDoctors (number),
//   totalStaff (number), staffPresence (number 0-100),
//   activeAlerts (number), processedToday (number),
//   triageTime (number, minutes),
//   sections: [{ nameKey, color, occupied, free, urgent, pct }]
//
// EXAMPLE API RESPONSE:
// {
//   "totalBeds": 515, "occupiedBeds": 487, "freeBeds": 28,
//   "occupancyRate": 94, "activeDoctors": 14, "totalStaff": 312,
//   "staffPresence": 98.1, "activeAlerts": 3,
//   "processedToday": 28, "triageTime": 18,
//   "sections": [
//     { "nameKey": "sectionCardiology", "color": "#e03131",
//       "occupied": 62, "free": 2, "urgent": 3, "pct": 97 },
//     ...
//   ]
// }
// ============================================

function _zenValidateData(data) {
    if (!data || typeof data !== 'object') return false;
    const required = ['totalBeds', 'occupiedBeds', 'freeBeds', 'occupancyRate',
                      'activeDoctors', 'totalStaff', 'activeAlerts', 'sections'];
    for (const key of required) {
        if (!(key in data)) return false;
    }
    if (!Array.isArray(data.sections) || data.sections.length === 0) return false;
    return true;
}

const _ZEN_DATA_CONTRACT = {
    totalBeds: 'number',
    occupiedBeds: 'number',
    freeBeds: 'number',
    occupancyRate: 'number',
    activeDoctors: 'number',
    totalStaff: 'number',
    staffPresence: 'number',
    activeAlerts: 'number',
    processedToday: 'number',
    triageTime: 'number',
    sections: [{ nameKey: 'string', color: 'string', occupied: 'number', free: 'number', urgent: 'number', pct: 'number' }],
};

function _zenPersistApiUrl(url) {
    try {
        if (url) localStorage.setItem('zenAIos-api-url', url);
        else localStorage.removeItem('zenAIos-api-url');
    } catch (_) {}
}

function _zenPushLocalData(dataObj) {
    const payload = { ...dataObj, _pushedAt: Date.now() };
    try { localStorage.setItem('zenAIos-local-data', JSON.stringify(payload)); } catch (_) {}
    window.dispatchEvent(new CustomEvent('dataUpdated', {
        detail: { data: { ...payload, _fetchedAt: Date.now() }, source: 'local', online: navigator.onLine }
    }));
}

const ZenAIosConfig = (() => {
    'use strict';

    const isLocalhost = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
    const isFile = location.protocol === 'file:';
    const isDev = isLocalhost || isFile;

    // Restore persisted API URL from localStorage (survives page reloads)
    let savedApiUrl = null;
    try { savedApiUrl = localStorage.getItem('zenAIos-api-url') || null; } catch (_) {}

    const config = {
        apiUrl: savedApiUrl,
        headers: {
            'Accept': 'application/json',
        },
        pollInterval: isDev ? 30000 : 15000,
        snapshotInterval: 300000,
        maxHistory: 288,
        requestTimeout: 10000,
        debug: isDev,
    };

    const DATA_CONTRACT = _ZEN_DATA_CONTRACT;

    function validateData(data) {
        return _zenValidateData(data);
    }

    return {
        get apiUrl() { return config.apiUrl; },
        set apiUrl(url) {
            config.apiUrl = url;
            _zenPersistApiUrl(url);
            if (window.ZenAIosData) window.ZenAIosData.setApiUrl(url);
        },
        get headers() { return { ...config.headers }; },
        get pollInterval() { return config.pollInterval; },
        get snapshotInterval() { return config.snapshotInterval; },
        get maxHistory() { return config.maxHistory; },
        get requestTimeout() { return config.requestTimeout; },
        get debug() { return config.debug; },
        get isDev() { return isDev; },
        validateData,
        DATA_CONTRACT,

        // ── Real-time data helpers ──────────────────────────────────────────────
        //
        // FROM A WEBSITE / REST API:
        //   ZenAIosConfig.setDataSource('https://api.myhospital.com/dashboard')
        //   → persisted in localStorage; polled every 15–30 s automatically.
        //
        // FROM LOCAL STORAGE (manual push / scripting):
        //   ZenAIosConfig.pushLocalData({ totalBeds: 520, occupiedBeds: 495, ... })
        //   → stored in localStorage AND fires dataUpdated immediately.
        //   Paste this object in DevTools console to update the UI on the spot.
        //
        // TO UPDATE DOCTOR PHOTO:
        //   ZenAIosConfig.pushLocalData({ ...existingData, doctor: { name:'Dr. X', avatar:'DX', photoUrl:'https://…/photo.jpg' } })
        //
        // TO RESET TO DEMO SIMULATOR:
        //   ZenAIosConfig.clearLocalOverride(); ZenAIosConfig.clearDataSource();
        // ───────────────────────────────────────────────────────────────────────

        /** Point the dashboard at a live REST API. Persists across reloads. */
        setDataSource(url) {
            this.apiUrl = url;
            if (window.ZenAIosData) window.ZenAIosData.syncNow();
        },

        /** Push a full data object directly into the UI (no API call needed).
         *  Also saves to localStorage so it survives refreshes until cleared. */
        pushLocalData(dataObj) {
            _zenPushLocalData(dataObj);
        },

        /** Remove any manual data override (reverts to API / demo simulator). */
        clearLocalOverride() {
            try { localStorage.removeItem('zenAIos-local-data'); } catch (_) {}
        },

        /** Remove the persisted API URL (reverts to demo simulator on next reload). */
        clearDataSource() {
            try { localStorage.removeItem('zenAIos-api-url'); } catch (_) {}
            config.apiUrl = null;
            if (window.ZenAIosData) window.ZenAIosData.setApiUrl(null);
        },
    };
})();
