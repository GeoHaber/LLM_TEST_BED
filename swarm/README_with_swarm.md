<p align="center">
  <img src="https://img.shields.io/badge/ZenAIos-Hospital%20AI-3B5BDB?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjgiIGhlaWdodD0iMjgiIHZpZXdCb3g9IjAgMCAyOCAyOCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjgiIGhlaWdodD0iMjgiIHJ4PSI2IiBmaWxsPSIjRThFRUZGIi8+PHJlY3QgeD0iNSIgeT0iNSIgd2lkdGg9IjgiIGhlaWdodD0iOCIgcng9IjIiIGZpbGw9IiMzQjVCREIiLz48cmVjdCB4PSIxNSIgeT0iNSIgd2lkdGg9IjgiIGhlaWdodD0iOCIgcng9IjIiIGZpbGw9IiMzQjVCREIiLz48cmVjdCB4PSI1IiB5PSIxNSIgd2lkdGg9IjgiIGhlaWdodD0iOCIgcng9IjIiIGZpbGw9IiMzQjVCREIiLz48cmVjdCB4PSIxNSIgeT0iMTUiIHdpZHRoPSI4IiBoZWlnaHQ9IjgiIHJ4PSIyIiBmaWxsPSIjNzQ4RkZDIiBvcGFjaXR5PSIwLjUiLz48L3N2Zz4=&logoColor=white" alt="ZenAIos Badge" />
</p>

# 🏥 ZenAIos — AI Hospital Management Dashboard

> Real-time AI management dashboard for **Spitalul Județean de Urgență Oradea** (Oradea County Emergency Hospital).  
> Beds, staff, emergencies & reports — all in one view, in 5 languages.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 📊 **Live Dashboard** | Real-time bed occupancy, staff on duty, active emergencies |
| 🌍 **5+ Languages** | Dashboard: EN, RO, HU, DE, FR — Swarm UI: + Hebrew (RTL), Japanese |
| 🏗️ **Department View** | Per-section bed stats (Cardiology, Neurology, Surgery, Pediatrics, Orthopedics, ICU) |
| 🔔 **Priority Alerts** | Critical blood shortage, equipment failures, CAS reports |
| 👨‍⚕️ **Staff Tracking** | On-call doctors, attendance percentage |
| 📱 **PWA Ready** | Install on any phone directly from the browser — works offline |
| 🎨 **Responsive** | Desktop 3-column layout → single-column mobile |
| ⚡ **Live Simulation** | Bed numbers fluctuate every 30 seconds with visual counter animation |
| 💾 **Persistent Language** | Language choice saved in localStorage |
| 🔄 **Real-time Data API** | Push live data from any URL or script — no rebuild needed |
| 🖼️ **Doctor Photo** | Doctor avatar supports real photo URL — falls back to initials |
| 💬 **Quick Chat Bar** | Floating input bar on the Dashboard with mic, camera, and **real AI responses** |
| 🤖 **AI Assistant** | Chat bar POSTs to `/__chat` → Local_LLM inference → response bubble appears in real time. On a fresh machine with no model, **Llama-3.2-3B is auto-downloaded from HuggingFace** (~1.9 GB, one-time) |
| 🎤 **Voice Input** | Web Speech API — tap mic, speak, transcript appears in the chat field |
| 📷 **Camera Capture** | Full-screen camera overlay with live preview and one-tap photo capture |
| 🔍 **Image OCR** | Camera captures are scanned client-side with Tesseract.js v5 — extracted text (`eng+ron`) appears in a scrollable chat bubble; no server needed |
| ✅ **Alert Acknowledge** | One-tap ✓ button per alert — logs to DB and greys out the card |
| 📡 **Action Logging** | Every view switch, chat send, dept tap logged to `actions` table via `/__log` |
| 🚨 **Anomaly Detection** | Auto-detects occupancy spikes, alert surges, triage jumps — shows toast + logs to DB |
| 📋 **Shift Handover** | `POST /__handover` generates an 8-hour activity summary report |
| 🔐 **DEV Mode** | Any badge ID + any PIN accepted for testing — one flag to toggle |
| 🔭 **Activity Monitor** | Live admin panel showing every connected device and what they're doing |
| 🗃️ **SQLite Activity DB** | 5-table DB: sessions, hits, actions, alert_acks, anomalies — query-ready for LLMs |
| 🐝 **Swarm Testing** | Full LLM model-comparison dashboard: Arena (head-to-head), Benchmark (multi-level), Marathon (multi-round), Evaluation scoring — with 16-endpoint automated "Run All" suite |
| 🌐 **7-Language Swarm UI** | Swarm test dashboard translated to English, French, German, Romanian, Hungarian, Hebrew (RTL), Japanese — with localStorage persistence |
| 🧵 **ThreadingTCPServer** | Concurrent request handling with daemon threads — no request blocking |

---

## 📸 Preview

The ZenAIos Dashboard is presented as a **centered, isolated monitoring interface** optimized for both management workstations and mobile devices.

### 🖥️ Focused Dashboard Mode
```
┌─────────────────────────────────┐
│                                 │
│        (Centered View)          │
│                                 │
│        ┌───────────────┐        │
│        │               │        │
│        │   ZenAIos     │        │
│        │  Dashboard    │        │
│        │ (Phone Frame) │        │
│        │               │        │
│        └───────────────┘        │
│                                 │
└─────────────────────────────────┘
```
- **Isolated View**: Secondary sidebars and navigation panels are hidden to focus on the core "Tablou" (Dashboard).
- **Responsive Centering**: On desktop, the app is centered with a clean background; on mobile, it fills the screen as a PWA.
- **Safe-area aware**: Respects iPhone notch and home indicator when installed as an app.

### Dashboard features:
- Greeting (time-aware: morning/afternoon/evening)
- 4 stat cards (occupied beds, free beds, on-call doctors, active alerts)
- Department sections with expandable "See All"
- **Quick Chat Bar** — mic, camera and send buttons just above the nav bar
- Bottom navigation bar

---

## 🗂️ Project Structure

```
ZenAIos/
├── hospital-data.json  # CENTRAL DATA FILE — Edit names, doctor, and stats here
├── index.html         # Main HTML — layout, phone mockup, KPI sidebar
├── login.html         # Badge + PIN + face-recognition login
├── styles.css         # All styling — layout, phone, cards, responsive, lang switcher
├── i18n.js            # Translation engine — 5 languages, 60+ keys each
├── app.js             # Interactivity — counters, live data, nav, toasts, i18n wiring
├── chat.js            # Quick Chat Bar — AI chat, voice input, camera OCR
├── api-config.js      # REST API config, auth headers, data contract
├── data-sync.js       # Stale-while-revalidate polling, IndexedDB cache, offline fallback
├── logic.js           # Pure business logic extracted for testability
├── views.js           # Nav page routing (Beds, Alerts, Staff, Reports)
├── server.py          # Smart server — activity tracking, SQLite DB, admin panel, LLM, Swarm API
├── swarm_bridge.py    # Swarm testing engine — model loading, inference, arena, benchmark, marathon
├── swarm-test.html    # Swarm test dashboard UI — 8 tabs, 7 languages, "Run All" suite
├── manifest.json      # PWA manifest — app name, icons, theme
├── sw.js              # Service Worker — offline caching
├── Run_me.bat         # One-click start (Windows) — opens app + admin panel
├── icons/             # App icons (72–512px)
├── tests/             # Browser test suites (52 suites, 323+ tests) + swarm tests
└── README.md          # This file

# Generated at runtime (gitignored)
└── zenai_activity.db  # SQLite activity database
```

---

## 🚀 Quick Start

### Just open it
No build tools, no server, no dependencies. Just:

```bash
# Clone the repo
git clone https://github.com/GeoHaber/ZenAIos-Dashboard.git
cd ZenAIos-Dashboard

# Open in browser
start index.html        # Windows
open index.html         # macOS
xdg-open index.html     # Linux
```

### With the smart server (recommended — enables activity tracking)

```bash
python server.py
```

or double-click **`Run_me.bat`** on Windows — it opens the app and admin panel automatically.

| URL | What it is |
|-----|------------|
| `http://localhost:8777/index.html` | The app |
| `http://localhost:8777/__admin` | Live activity monitor |
| `http://localhost:8777/__admin/db-stats` | DB summary as JSON |
| `http://localhost:8777/__admin/actions` | Recent user actions as JSON |

### With plain Python (no tracking)

```bash
python -m http.server 8080
```

### 📱 Mobile Rendering & Testing
To see the app on a real mobile device while developing:
1.  **Run the test server**: `python mobile-test.py`
2.  **Follow the guide**: See [HOW_TO_TEST.md](./HOW_TO_TEST.md) for step-by-step instructions on connecting via Wi-Fi, VS Code, or ngrok.

---

## 🔍 Smart Server & Activity Database

### Starting the server

```bash
python server.py
# or on Windows: double-click Run_me.bat
```

### Admin panel — `/__admin`

Auto-refreshes every 3 seconds. For every device that connects you see:

| Field | What it tells you |
|-------|-------------------|
| IP address | Which device / client |
| Device / Browser / OS | e.g. `iPhone · Safari · iOS` |
| ● Active / ◌ Idle / ○ Offline | Active = request in last 60 s |
| First seen / Last active | When they connected and last did something |
| Request log | Every page loaded with timestamp and HTTP status |

### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/__admin` | Live activity monitor UI |
| GET | `/__admin/data` | Session/hit data as JSON |
| GET | `/__admin/db-stats` | Top pages, clients, hits by day |
| GET | `/__admin/actions` | Recent in-app user actions (last 200) |
| POST | `/__log` | Receive action beacon from frontend |
| POST | `/__chat` | AI chat — proxies to Local_LLM, returns `{reply}` |
| POST | `/__ack` | Record alert acknowledgement |
| POST | `/__anomaly` | Record anomaly detected on frontend |
| POST | `/__handover` | Generate 8-hour shift summary report |
| GET | `/__swarm/status` | Swarm bridge health check |
| GET | `/__swarm/models` | List all available GGUF models |
| GET | `/__swarm/pool` | Model pool status (loaded/queued) |
| GET | `/__swarm/memory` | System RAM & VRAM snapshot |
| GET | `/__swarm/prompts` | Prompt categories for testing |
| GET | `/__swarm/random-prompt` | Random prompt from a category |
| POST | `/__swarm/inference` | Single-model inference |
| POST | `/__swarm/arena` | Head-to-head model comparison |
| POST | `/__swarm/benchmark` | Multi-level benchmark (temperature sweep) |
| POST | `/__swarm/marathon-round` | Multi-round tournament round |
| POST | `/__swarm/evaluate` | Score a response (1–10 rubric) |
| POST | `/__swarm/diagnose` | Analyse benchmark results |
| POST | `/__swarm/recommendations` | Model recommendations based on results |
| POST | `/__swarm/pool/preload` | Preload models into memory |
| POST | `/__swarm/pool/drain` | Drain model pool |

### SQLite database — `zenai_activity.db`

Created automatically next to `server.py` the first time you run it. Five tables:

```sql
-- One row per unique IP
sessions:    ip, device, browser, os, first_seen, last_seen, total_hits

-- One row per HTTP request
hits:        id, ip, path, status, timestamp, time_str, ua

-- In-app user actions (view switches, chat sends, dept taps, acks…)
actions:     id, ip, badge, action, detail(JSON), timestamp, time_str

-- Alert acknowledgements
alert_acks:  id, alert_key, badge, ip, timestamp, time_str

-- Anomalies detected by the frontend
anomalies:   id, type, message, value, threshold, timestamp, time_str
```

**Quick queries:**

```sql
-- Who connected and how many times?
SELECT ip, device, browser, total_hits FROM sessions ORDER BY total_hits DESC;

-- Most visited pages
SELECT path, COUNT(*) n FROM hits GROUP BY path ORDER BY n DESC LIMIT 10;

-- Full history for one IP
SELECT time_str, path, status FROM hits WHERE ip='192.168.1.5' ORDER BY timestamp DESC;

-- Activity by day
SELECT date(timestamp,'unixepoch','localtime') day, COUNT(*) n
FROM hits GROUP BY day ORDER BY day DESC;

-- All chat messages sent through the dashboard
SELECT time_str, badge, detail FROM actions WHERE action='chatSend' ORDER BY id DESC;

-- All acknowledged alerts
SELECT time_str, badge, alert_key FROM alert_acks ORDER BY id DESC;

-- Recent anomalies
SELECT time_str, type, message, value, threshold FROM anomalies ORDER BY id DESC LIMIT 20;

-- What did iPhone users visit?
SELECT h.path, COUNT(*) n FROM hits h
JOIN sessions s ON s.ip = h.ip
WHERE s.device='iPhone' GROUP BY h.path ORDER BY n DESC;
```

**DB summary endpoint (JSON):** `http://localhost:8777/__admin/db-stats`  
Returns: top pages, top clients, hits per day — ready to feed into an LLM query interface.

**User actions endpoint (JSON):** `http://localhost:8777/__admin/actions`  
Returns last 200 in-app actions — view switches, chat sends, alert acks — with badge and timestamp.

> `zenai_activity.db` is gitignored and never committed — your session data stays local.

---

## 🌍 Languages

| Flag | Code | Language | Where |
|------|------|----------|-------|
| 🇬🇧 | `en` | English | Dashboard + Swarm |
| 🇷🇴 | `ro` | Română | Dashboard + Swarm |
| 🇭🇺 | `hu` | Magyar | Dashboard + Swarm |
| 🇩🇪 | `de` | Deutsch | Dashboard + Swarm |
| 🇫🇷 | `fr` | Français | Dashboard + Swarm |
| 🇮🇱 | `he` | עברית (Hebrew, RTL) | Swarm only |
| 🇯🇵 | `ja` | 日本語 (Japanese) | Swarm only |

**To add a new language**, edit `i18n.js` and add a new object to the `translations` dictionary with all keys. The language switcher auto-generates buttons from available translations.

---

## 📱 Install as Mobile App (PWA)

### iOS (Safari)
1. Open the dashboard URL in Safari
2. Tap the **Share** button (⬆️)
3. Tap **"Add to Home Screen"**
4. Tap **Add** — the app icon appears on your home screen

### Android (Chrome)
1. Open the dashboard URL in Chrome
2. Tap the **⋮ menu** → **"Install app"** or **"Add to Home Screen"**
3. Confirm — the app appears in your app drawer

> The PWA works offline after the first visit thanks to the Service Worker.

---

## 🔧 Generate App Icons

You need PNG icons at these sizes: `72, 96, 128, 144, 152, 192, 384, 512`

Create an `icons/` folder and generate them from a source image:

```bash
mkdir icons

# Using ImageMagick (if installed):
for size in 72 96 128 144 152 192 384 512; do
    magick icon-source.png -resize ${size}x${size} icons/icon-${size}.png
done

# Or use https://realfavicongenerator.net — upload a 512x512 PNG
```

**Suggested icon design:** Blue (#3B5BDB) background, white hospital cross + "Z" logo.

---

## 📲 Native App Builds (iOS / Android)

For App Store / Play Store distribution, use **Capacitor** to wrap the web app:

### Prerequisites
- [Node.js](https://nodejs.org/) 18+
- [Android Studio](https://developer.android.com/studio) (for Android)
- [Xcode](https://developer.apple.com/xcode/) (for iOS, macOS only)

### Setup

```bash
# Initialize npm project
npm init -y

# Install Capacitor
npm install @capacitor/core @capacitor/cli
npx cap init ZenAIos com.zenaios.dashboard --web-dir .

# Add platforms
npm install @capacitor/android @capacitor/ios
npx cap add android
npx cap add ios

# Sync web files → native projects
npx cap sync

# Open in native IDE
npx cap open android    # Opens Android Studio
npx cap open ios        # Opens Xcode (macOS only)
```

### Build & Run

```bash
# Android — build APK
npx cap run android

# iOS — build in Xcode
npx cap run ios
```

---

## 🧪 Simulated Data

The dashboard uses simulated hospital data for demo purposes:

| Metric | Value |
|--------|-------|
| Total beds | 515 |
| Occupied | 487 (fluctuates ±1 every 30s) |
| Free | 28 |
| Occupancy rate | 94% |
| Active staff | 312 |
| On-call doctors | 14 |
| Active emergencies | 3 |
| Triage time | 18 min |

**6 departments:** Cardiology, Neurology, Surgery, Pediatrics, Orthopedics, ICU

See the **Real-time Data API** section below to connect live data.

---

## � Quick Chat Bar

A floating input bar sits at the bottom of the **Dashboard view**, directly above the navigation bar. It hides automatically when you switch to Beds / Alerts / Staff / Reports / Chat views.

### Buttons

| Button | What it does |
|--------|--------------|
| 📷 **Camera** | Opens a full-screen live camera overlay (rear camera preferred on mobile). Tap the shutter 📷 to capture a JPEG frame — it appears as `[photo]` in the input ready to send. Press **ESC** or ✕ to close without capturing. |
| 🎤 **Microphone** | Activates the Web Speech API. Tap once to start recording — the button turns red and pulses. Your spoken words appear in the text field in real time. Tap again to stop. |
| ➤ **Send** | Sends the current text (or photo note) as a toast notification. **Enter** key also triggers send. |

### Requirements
- **Camera** — requires `localhost` or **HTTPS**. The same `isSecureContext` guard used by the login face-scan applies here. Running via `python -m http.server` on `localhost` is fine.
- **Microphone / Speech** — supported in Chrome and Edge. Firefox does not currently support the Web Speech API.

### Placeholder text
The input placeholder (`Message ZenAI...`) is fully translated across all 5 languages and updates instantly when the language switcher is used.

---

## �🔄 Real-time Data API

All data updates flow through `api-config.js` → `data-sync.js` → the UI.  
No server rebuild or code changes needed — everything is controlled at runtime.

### Data flow overview

```
Your REST API  ──►  data-sync.js (fetch + cache)  ──►  app.js (render UI)
                          │
                    IndexedDB (offline fallback)
```

`data-sync.js` polls your API every **30 seconds** (15 s in production). Each successful response fires a `dataUpdated` custom event that `app.js` listens to and re-renders the entire dashboard instantly.

---

### Method 1 — Point at a live REST API

Paste this in the browser console (or call it from your own code).  
The URL is **saved in `localStorage`** and restored automatically on every reload.

```js
// Connect to your hospital backend
ZenAIosConfig.setDataSource('https://api.myhospital.com/dashboard')

// The dashboard polls that URL every 15–30 s and updates the UI automatically.
// To remove:
ZenAIosConfig.clearDataSource()
```

#### Adding authentication headers

If your API requires a token or API key, open [`api-config.js`](./api-config.js) and add it to the `headers` object:

```js
headers: {
    'Accept': 'application/json',
    'Authorization': 'Bearer YOUR_TOKEN_HERE',
    // or: 'X-Api-Key': 'YOUR_KEY'
},
```

#### CORS — the most common gotcha

The browser will block the fetch if your API does not allow cross-origin requests.  
Your API server must respond with:

```
Access-Control-Allow-Origin: *
```
or specifically allow the hostname where the dashboard is hosted (e.g. `https://yourhospital.com`).

#### Offline & error handling

`data-sync.js` handles failures automatically:
- Falls back to the **last good snapshot** stored in IndexedDB (survives page reloads)
- Shows the **offline banner** in the UI
- **Retries every 5 seconds** until reconnected
- Keeps a rolling **24-hour history** of snapshots (288 × 5-min intervals) used for sparkline charts

#### Your API must return JSON matching this shape:

```json
{
  "totalBeds": 515,
  "occupiedBeds": 487,
  "freeBeds": 28,
  "occupancyRate": 94,
  "activeDoctors": 14,
  "totalStaff": 312,
  "staffPresence": 98.1,
  "activeAlerts": 3,
  "processedToday": 28,
  "triageTime": 18,
  "sections": [
    { "nameKey": "sectionCardiology", "color": "#e03131", "occupied": 62, "free": 2, "urgent": 3, "pct": 97 }
  ]
}
```

### Method 2 — Push data directly (no API needed)

Perfect for scripting, testing, or injecting data from any source (local file, WebSocket, manual entry).  
Data is **saved in `localStorage`** and restored on page reload until you clear it.

```js
ZenAIosConfig.pushLocalData({
  totalBeds: 520,
  occupiedBeds: 495,
  freeBeds: 25,
  occupancyRate: 95,
  activeDoctors: 16,
  totalStaff: 318,
  staffPresence: 97.5,
  activeAlerts: 5,
  processedToday: 32,
  triageTime: 14,
  sections: [ /* same shape as above */ ],
  // Optional: override doctor identity
  doctor: { name: 'Dr. Popescu', avatar: 'DP', photoUrl: 'https://example.com/photo.jpg' },
  hospital: { name: 'SJUO Oradea', sub: 'SISTEM AI SPITALICESC' }
})

// To remove the override and revert to simulator:
ZenAIosConfig.clearLocalOverride()
```

### Method 3 — Edit `hospital-data.json`

For permanent static changes (hospital name, doctor, baseline numbers) edit the JSON file directly.  
The simulator runs micro-fluctuations on top of these values.

---

## 🤖 Local LLM Integration

The AI chat backend runs **entirely in-process** — no Ollama, no API keys, no network calls. It uses the [Local_LLM](https://github.com/GeoHaber/Local_LLM) project's `FIFOLlamaCppInference` engine to load a GGUF model directly into Python memory via `llama-cpp-python`.

### Setup

1. **Clone Local_LLM** as a sibling folder (default) or anywhere you like:
   ```
   C:\Users\you\GitHub\GeorgeHaber\
   ├── ZenAIos-Dashboard\   ← this repo
   └── Local_LLM\           ← inference engine (default location)
   ```

2. **Install dependencies:**
   ```bash
   pip install llama-cpp-python   # CPU build
   # GPU build (CUDA): CMAKE_ARGS="-DLLAMA_CUDA=on" pip install llama-cpp-python
   ```

3. **Model — zero config required.**  
   The engine first looks for a `.gguf` file ≥ 1.5 GB in `C:\AI\Models\` (or `SWARM_MODELS_DIR`).  
   **If no model is found it automatically downloads one from HuggingFace** on first chat message:

   | Download order | Model | Size |
   |---|---|---|
   | 1st | Llama-3.2-3B-Instruct-Q4_K_M | ~1.9 GB |
   | 2nd | Phi-3.5-mini-instruct-Q4_K_M | ~2.2 GB |
   | 3rd | Mistral-7B-Instruct-v0.3-Q4_K_M | ~4.1 GB |

   The file is saved to `SWARM_MODELS_DIR` and reused on every subsequent restart — no re-download.  
   Requires `pip install huggingface-hub` (already in `requirements.txt`) and internet access the first time.

4. **Start the server** — the model loads on the first chat message (lazy init):
   ```bash
   pip install -r ../Local_LLM/requirements.txt   # first time only
   python server.py
   ```

### Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `LOCAL_LLM_PATH` | `../Local_LLM` (sibling) | Path to the Local_LLM project root |
| `SWARM_MODELS_DIR` | `C:\AI\Models` | Directory to scan for GGUF models |

### What happens when you type a message

```
User types message  →  POST /__chat  →  server.py
  → FIFOLlamaCppInference.query(message, system_prompt=HOSPITAL_CONTEXT)
  → chunks collected  →  {reply: "..."}  →  chat bubble rendered
```

The system prompt tells the AI it is **ZenAI**, a medical assistant embedded in a hospital dashboard, so answers are contextually relevant to clinical operations.

### If Local_LLM is not available

The server starts without errors — the LLM engine is **lazy-loaded on first use**. If the model or project cannot be found, `/__chat` returns a `503` with a descriptive error message shown in the chat bubble.

---

## 🔄 Adaptive FIFO Buffers & Admission Control

Ported from Local_LLM's `AdaptiveFIFOBuffer`, the server uses a dual-buffer architecture for request/response coordination and admission control.

### Architecture

```
HTTP Handler                          LLM Engine
     │                                     │
     ├── _admit_request() ──→ ┌────────────────────┐
     │     (put to request    │  _request_buffer    │
     │      buffer)           │  ThreadSafeFIFOBuffer│
     │                        │  backpressure=True  │
     │  ◄── 503 if full      └────────────────────┘
     │                                     │
     ├── _query_with_retry() ──────────────┤
     │     (retries + metrics)             │
     │                                     │
     ├── _release_request() ◄──────────────┤
     │     (get from request buffer)       │
     │                                     │
     │     response published ──→ ┌────────────────────┐
     │                            │  _response_buffer   │
     │                            │  ThreadSafeFIFOBuffer│
     │                            │  backpressure=False │
     │                            └────────────────────┘
     │                                     │
     └──── /health, /__admin/data ◄── stats from both buffers
```

### Adaptive Sizing

Both buffers self-adjust based on load and system pressure:

| Trigger | Action | Factor |
|---------|--------|--------|
| Fill > 80% | **Grow** buffer capacity | × 1.5 (up to `max_size`) |
| Fill < 20% + RAM > 80% | **Shrink** buffer capacity | × 0.8 (down to `min_size`) |
| Fill < 10% (no psutil) | **Shrink** if above initial | × 0.8 (down to `initial_size`) |

### Priority Queues

Requests can be tagged with priority (LOW → NORMAL → HIGH → CRITICAL). Higher-priority items are dequeued first via a `heapq` priority queue.

### Backpressure

When `_request_buffer` is full, `_admit_request()` returns `False` and the handler returns **503 "Server busy"** instead of piling unbounded work on the engine. Callers can also use `raise_on_timeout=True` to get a `BackpressureTimeoutError` exception.

### Observability

`GET /health` and `GET /__admin/data` both return live stats:

```json
{
  "request_buffer": {
    "buffer_name": "inference_requests",
    "current_size": 0,
    "max_size": 10,
    "fill_percent": 0.0,
    "total_added": 42,
    "total_retrieved": 42,
    "peak_size": 3,
    "backpressure_events": 0
  },
  "response_buffer": { ... },
  "inference": {
    "total_requests": 42,
    "avg_latency_s": 8.3,
    "p95_latency_s": 14.1,
    "error_rate_pct": 0.0
  }
}
```

All adaptive sizing events are logged via `logging.getLogger("zenai.server")`.

---

## 🚨 Anomaly Detection

Every time live data arrives from the API (or simulator), `updateDashboardUI()` compares the new snapshot against the previous one. If a threshold is crossed, the user sees a **toast alert** and the event is logged to the `anomalies` table via `POST /__anomaly`.

| Anomaly type | Trigger | Threshold |
|---|---|---|
| `beds_spike` | Occupancy rate crosses upward | ≥ 95% |
| `alerts_surge` | Active alerts increases past limit | > 5 alerts |
| `triage_high` | Sum of urgent cases across all depts jumps | +3 or more in one cycle |

Query anomalies directly:
```sql
SELECT time_str, type, message, value, threshold FROM anomalies ORDER BY id DESC;
```

---

## ✅ Alert Acknowledgement

In the **Alerts view**, each alert card has a **✓ acknowledge button** (green ring on the right side). Tapping it:
1. POSTs `{ alertKey, badge }` to `/__ack`
2. Writes to `alert_acks` + `actions` tables in the DB
3. Greys out the alert card in the UI so it's visually distinct from unacknowledged ones

Query who acknowledged what:
```sql
SELECT time_str, badge, alert_key FROM alert_acks ORDER BY id DESC;
```

---

## 📋 Shift Handover Report

`POST /__handover` (send `{ badge: "DR-1042" }`) generates a plain-text report covering the **last 8 hours**:

```
=== SHIFT HANDOVER — 2026-03-09 14:30 ===
Prepared by: DR-1042

ACTIONS THIS SHIFT (42 events):
  [08:12:34] DR-1042 — switchView: {"view":"alerts"}
  ...

ALERTS ACKNOWLEDGED (3):
  [09:01:22] NR-2201 acked alertBlood
  ...

ANOMALIES DETECTED (1):
  [11:44:05] beds_spike: Occupancy at 96%
```

You can call this from the browser console:
```js
fetch('/__handover', { method:'POST', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({ badge: 'DR-1042' }) })
  .then(r => r.json()).then(d => console.log(d.report));
```

---

## 🤖 Local LLM Integration

The AI chat backend runs **entirely in-process** — no Ollama, no API keys, no network calls. It uses the [Local_LLM](https://github.com/GeoHaber/Local_LLM) project's `FIFOLlamaCppInference` engine to load a GGUF model directly into Python memory via `llama-cpp-python`.

### Setup

1. **Clone Local_LLM** as a sibling folder (default) or anywhere you like:
   ```
   C:\Users\you\GitHub\GeorgeHaber\
   ├── ZenAIos-Dashboard\   ← this repo
   └── Local_LLM\           ← inference engine (default location)
   ```

2. **Install dependency:**
   ```bash
   pip install llama-cpp-python           # CPU build
   # GPU (CUDA): CMAKE_ARGS="-DLLAMA_CUDA=on" pip install llama-cpp-python
   ```

3. **Model — zero config required.**  
   The engine first looks for a `.gguf` file ≥ 1.5 GB in `C:\AI\Models\` (or `SWARM_MODELS_DIR`).  
   **If no model is found it automatically downloads one from HuggingFace** on first chat message:

   | Download order | Model | Size |
   |---|---|---|
   | 1st | Llama-3.2-3B-Instruct-Q4_K_M | ~1.9 GB |
   | 2nd | Phi-3.5-mini-instruct-Q4_K_M | ~2.2 GB |
   | 3rd | Mistral-7B-Instruct-v0.3-Q4_K_M | ~4.1 GB |

   The file is saved to `SWARM_MODELS_DIR` and reused on every subsequent restart — no re-download.  
   Requires `pip install huggingface-hub` (already in `requirements.txt`) and internet access the first time.

4. **Start the server** — the model loads on the first chat message (lazy init):
   ```bash
   python server.py
   ```

### Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `LOCAL_LLM_PATH` | `../Local_LLM` (sibling) | Path to the Local_LLM project root |
| `SWARM_MODELS_DIR` | `C:\AI\Models` | Directory to scan for GGUF models |

### What happens when you send a message

```
User types message  →  POST /__chat  →  server.py
  → FIFOLlamaCppInference.query(message, system_prompt=HOSPITAL_CONTEXT)
  → chunks collected  →  {reply: "..."}  →  chat bubble rendered
```

The system prompt tells the AI it is **ZenAI**, a medical assistant embedded in a hospital operations dashboard, so answers are contextually relevant to clinical operations.

### If Local_LLM is not available

The server starts without errors — the LLM engine is **lazy-loaded on first use**. If the model or project cannot be found, `/__chat` returns a `503` with a descriptive error shown in the chat bubble.

---

## 🚨 Anomaly Detection

Every time live data arrives from the API (or simulator), `updateDashboardUI()` compares the new snapshot against the previous one. If a threshold is crossed, the user sees a **toast alert** and the event is logged to the `anomalies` table via `POST /__anomaly`.

| Anomaly type | Trigger | Threshold |
|---|---|---|
| `beds_spike` | Occupancy rate crosses upward | ≥ 95% |
| `alerts_surge` | Active alerts increases past limit | > 5 alerts |
| `triage_high` | Sum of urgent cases across all depts jumps | +3 or more in one cycle |

Query anomalies directly:
```sql
SELECT time_str, type, message, value, threshold FROM anomalies ORDER BY id DESC;
```

---

## ✅ Alert Acknowledgement

In the **Alerts view**, each alert card has a **✓ acknowledge button** (green ring on the right side). Tapping it:
1. POSTs `{ alertKey, badge }` to `/__ack`
2. Writes to `alert_acks` + `actions` tables in the DB
3. Greys out the alert card so it's visually distinct from unacknowledged ones

Query who acknowledged what:
```sql
SELECT time_str, badge, alert_key FROM alert_acks ORDER BY id DESC;
```

---

## 📋 Shift Handover Report

`POST /__handover` (send `{ badge: "DR-1042" }`) generates a plain-text report covering the **last 8 hours**:

```
=== SHIFT HANDOVER — 2026-03-09 14:30 ===
Prepared by: DR-1042

ACTIONS THIS SHIFT (42 events):
  [08:12:34] DR-1042 — switchView: {"view":"alerts"}
  ...

ALERTS ACKNOWLEDGED (3):
  [09:01:22] NR-2201 acked alertBlood
  ...

ANOMALIES DETECTED (1):
  [11:44:05] beds_spike: Occupancy at 96%
```

Call it from the browser console:
```js
fetch('/__handover', { method:'POST', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({ badge: 'DR-1042' }) })
  .then(r => r.json()).then(d => console.log(d.report));
```

---

## 🛠️ Customization

### ⚙️ Centralized Data (`hospital-data.json`)
Edit the entire identity of the app in one place without touching JavaScript.

**Location:** [`hospital-data.json`](./hospital-data.json)

**Structure:**
```json
{
  "hospital": {
    "name": "SJUO Oradea",
    "sub": "SISTEM AI SPITALICESC",
    "heroTitle": "ZenAIos<br>pentru<br>Oradea...",
    "heroDesc": "Soluție AI de management..."
  },
  "doctor": {
    "name": "Dr. Moldovan",
    "role": "Manager",
    "avatar": "DM",
    "photoUrl": ""     ← set a URL or relative path to show a photo instead of initials
  },
  "stats": {
    "totalBeds": 515,
    "occupiedBeds": 487,
    "activeDoctors": 14,
    ...
  }
}
```

**Doctor avatar photo:**  
Set `"photoUrl"` to any image URL or relative path (e.g. `"icons/doctor.jpg"`).  
The avatar circle will show the photo; when the field is empty it falls back to the initials in `"avatar"`.

### Add a new department
Add to the `sections` array in `hospital-data.json` and add a `sectionXxx` key to each language in `i18n.js`.

### Change theme colors
Main colors in `styles.css`:
- Primary blue: `#3B5BDB`
- Dark navy: `#1a1a2e`
- Success green: `#2b8a3e`
- Danger red: `#e03131`
- Warning orange: `#e67700`

### Connect to a real API
See the **Real-time Data API** section above.  
The quickest path is: `ZenAIosConfig.setDataSource('https://your-api/dashboard')`

### Card layout
All stat cards, KPI cards, report cards, section stat cells, and the department modal use **centered text alignment** — numbers and labels are symmetrically stacked for a clean read at a glance.

---

## 🔐 Authentication & DEV Mode

### Login flow
The login screen (`login.html`) uses a **two-step flow**:
1. **Badge** — type or scan a badge ID
2. **Auth screen (combined)** — face camera (top, if enrolled + HTTPS) **and** 4-digit PIN pad (always visible) on the **same screen**
   - Face matches → redirects immediately, PIN not needed
   - Face fails / no camera → error shown above PIN pad, user types PIN
   - No front camera or permission denied → camera section hidden automatically

> **HTTPS / localhost required for camera** — `getUserMedia` only works in a secure context.  
> Face data is stored in the browser's IndexedDB (origin-scoped — re-enroll once per URL/tunnel).

### Face recognition — technical details
| Property | Value |
|----------|-------|
| Library | `face-api.js@0.22.2` (TensorFlow.js, 100 % client-side) |
| Model source | `cdn.jsdelivr.net/npm/face-api.js@0.22.2/weights` (pinned) |
| Match threshold | Euclidean distance < 0.55 |
| Scan interval | 500 ms, max 30 attempts (~15 s) |
| Model load wait | Up to 12 s polling (200 ms interval) inside `startFaceScan` before camera starts |
| Model load retries | 1 s → 4 s → 9 s, 20 s hard timeout |
| Concurrent scan guard | `scanRunning` flag prevents double-invocation; previous scan cancelled on `showAuth` |
| Storage | Browser IndexedDB (`ZenAIosFaces`) — never sent to server |

### Enrollment flow (first PIN login)
After a successful PIN login with no stored face: enrollment screen opens → user positions face → taps Capture → descriptor saved to IndexedDB → redirects to dashboard. Can be skipped at any time.

### DEV Mode — accept any credentials for testing

`DEV_MODE` is a single flag at the top of the `login.html` inline script:

```js
const DEV_MODE = true;   // ← any badge + any PIN accepted
// const DEV_MODE = false; // ← enforce DEMO_USERS table with hashed PINs
```

When `true`:
- Any badge ID is accepted (if not in `DEMO_USERS`, a temporary user is created from `hospital-data.json`)
- Any 4-digit PIN unlocks the dashboard

> ⚠️ Set `DEV_MODE = false` before deploying to a production environment.

### Built-in demo accounts (when DEV_MODE is false)

| Badge | Role | PIN |
|-------|------|-----|
| `DR-1042` | Dr. Horea Timish — Manager | `1234` |
| `NR-2201` | Nurse Coordinator | `5678` |
| `AD-0001` | Admin | `0000` |

---

## �📄 License

MIT — free for personal and commercial use.

---

## 🤝 Contributing

1. Fork the repo
2. Create a branch (`git checkout -b feature/new-department`)
3. Commit changes (`git commit -m "Add Ophthalmology department"`)
4. Push (`git push origin feature/new-department`)
5. Open a Pull Request

---

<p align="center">
  Built with ❤️ for <strong>Spitalul Județean de Urgență Oradea</strong>
</p>
