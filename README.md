# TrueX — Real-time Media Risk Intelligence System

> **Monitor threats. Verify facts. Stay ahead of misinformation.**

TrueX is an AI-powered, real-time media risk intelligence platform built for India. It aggregates live news from across the country, classifies threats by category and severity, and lets users instantly verify any news headline or claim using AI fact-checking (Perplexity + NewsAPI).

---

## 🚀 Features

- 🗺️ **Live Risk Map** — Interactive map of India showing city-level news hotspots, color-coded by severity (Red 🔴 / Blue 🔵 flags)
- ✅ **News Verification Engine** — Paste any headline or claim to get an AI-powered verdict: `VERIFIED`, `MISLEADING`, `FALSE`, or `UNCLEAR`
- 📡 **Real-time Alerts** — Categorized alert feed covering Weather, Terrorism, Crime, and Migration/Displacement events
- 📊 **Analytics Dashboard** — Bar/Line charts showing trends by source, city, and alert category
- 🔐 **User Authentication** — Secure email/password login with SHA-256 + salt hashing (stored locally in `users.json`)
- 🌐 **City-level News Drill-down** — Click on any city on the map to see its latest local news feed
- 🔄 **Auto-refresh** — Dashboard data refreshes every 30 seconds automatically

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 18, Leaflet.js, Recharts, Chart.js, Mapbox GL |
| **Backend** | FastAPI (Python), Uvicorn, httpx |
| **News Sources** | NewsAPI, Google News RSS |
| **AI Fact-checking** | Perplexity API (llama-3.1-sonar-large-128k-online) |
| **Auth** | SHA-256 + salt password hashing (hashlib) |
| **Data Storage** | Local JSON file (`users.json`) |

---

## 📁 Project Structure

```
InsideGrid/
├── back.py                  # FastAPI backend — all API routes & logic
├── requirements.txt         # Python dependencies
├── .env                     # API keys (NEWSAPI_KEY, PERPLEXITY_API_KEY)
├── users.json               # User accounts (auto-created)
│
├── frontend/
│   ├── src/
│   │   ├── App.js                  # Root component
│   │   ├── MediaRiskSystem.js      # Main dashboard component
│   │   ├── GlobalRiskMap.js        # Interactive India map (Leaflet)
│   │   ├── VerificationResult.js   # News verification result UI
│   │   ├── MediaRiskSystem.css     # Main styles
│   │   └── AnalyticsStyles.css     # Analytics chart styles
│   └── package.json
│
├── start_all.ps1            # One-click launch: backend + frontend
├── start_backend.ps1        # Launch backend only
├── start_frontend.ps1       # Launch frontend only
└── check_status.ps1         # Check if services are running
```

---

## ⚙️ Setup & Installation

### Prerequisites

- Python 3.9+
- Node.js 18+
- API Keys (see [Environment Variables](#-environment-variables))

---

### 1. Clone the Repository

```bash
git clone <your-repo-url>
cd InsideGrid
```

### 2. Backend Setup

```bash
# Create virtual environment
python -m venv venv

# Activate (Windows)
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Frontend Setup

```bash
cd frontend
npm install
```

### 4. Configure Environment Variables

Create a `.env` file in the root `InsideGrid/` directory:

```env
NEWSAPI_KEY=your_newsapi_key_here
PERPLEXITY_API_KEY=your_perplexity_api_key_here
```

> **Get your keys:**
> - NewsAPI: [https://newsapi.org](https://newsapi.org)
> - Perplexity API: [https://www.perplexity.ai/settings/api](https://www.perplexity.ai/settings/api)

---

## 🏃 Running the App

### Option A — One-Click Launch (Recommended)

```powershell
.\start_all.ps1
```

This opens three separate PowerShell windows for Backend, Frontend, and Landing page.

To skip the landing page:
```powershell
.\start_all.ps1 -NoLanding
```

---

### Option B — Manual Launch

**Terminal 1 — Backend:**
```bash
# From root InsideGrid/ directory
.\venv\Scripts\activate
uvicorn back:app --reload --host 127.0.0.1 --port 8000
```

**Terminal 2 — Frontend:**
```bash
cd frontend
npm start
```

The app will open at **[http://localhost:3000](http://localhost:3000)**
Backend API runs at **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/hotspots` | City-level news hotspots with severity scores |
| `GET` | `/risk-map` | India city risk data for map visualization |
| `GET` | `/alerts` | Categorized national alert counts |
| `GET` | `/alerts/by-region?place=<city>` | Alerts filtered by city/region |
| `GET` | `/analytics` | Full analytics data (categories, trends, sources) |
| `GET` | `/live-streams` | Live stream feed metadata |
| `GET` | `/city/news?place=<city>&limit=10` | Live local news for a city |
| `POST` | `/api/verify-news` | Verify a news claim via AI |
| `POST` | `/api/signup` | Register a new user |
| `POST` | `/api/login` | Authenticate an existing user |

---

## 🔍 How News Verification Works

1. User pastes a headline or claim into the **Verify News** panel
2. Backend extracts key terms and queries **NewsAPI** for relevant articles
3. Optionally, **Perplexity AI** (with web search) cross-references the claim against live sources
4. Sensitive/controversial claims (political, religious) go through a stricter verification pipeline with lower max confidence
5. Final verdict returned: `VERIFIED` / `MISLEADING` / `FALSE` / `UNCLEAR` with confidence score and explanation

---

## 🗺️ How the Risk Map Works

- Backend fetches live news for **29 major Indian cities** every 5 minutes (cached)
- Each city is scored by article volume, recency, and severity keywords
- Articles are classified into **Weather**, **Terrorism**, **Crime**, **Migration/Displacement**, or **General Alert**
- **Red Flag 🔴** — City has Weather, Terrorism, or Crime alerts
- **Blue Flag 🔵** — City has general news (no high-priority alerts)
- Clicking a city shows its local news feed in the dashboard

---

## 🔐 Authentication

- Passwords are hashed using **SHA-256 with a random 16-byte salt**
- User data is stored in `users.json` in the project root
- Minimum password length: 8 characters
- No JWT tokens — session is managed in React state (frontend only)

> ⚠️ **This is a development setup.** For production, use a proper database and JWT-based auth.

---

## 🧑‍💻 Development Notes

- Backend CORS is set to `allow_origins=["*"]` — restrict this in production
- News data is cached for **5 minutes** (city hotspot cache), **3 minutes** (local news cache)
- Frontend polls backend every **30 seconds** for fresh data
- Perplexity API is **optional** — the app works without it (uses NewsAPI + RSS only)
- Google News RSS is used as a fallback when NewsAPI quota is exhausted

---

## 📦 Dependencies

**Python (`requirements.txt`)**
```
fastapi==0.121.1
uvicorn[standard]==0.38.0
python-multipart==0.0.9
httpx==0.27.0
```

**Node.js (`package.json`)**
```
react, react-dom, react-scripts
leaflet, react-leaflet
mapbox-gl, @react-google-maps/api
chart.js, react-chartjs-2
recharts
```

---

## 📸 Screenshots

> Add screenshots of the login page, dashboard, risk map, and verification panel here.

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit changes: `git commit -m 'Add your feature'`
4. Push: `git push origin feature/your-feature`
5. Open a Pull Request

---

## 📄 License

This project is for educational/demo purposes. Add your preferred license here.

---

*Built with ❤️ for real-time media transparency in India.*
