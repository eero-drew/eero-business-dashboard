# 🌐 eero Business Dashboard

**Your networks. Your locations. Your metrics. One screen.**

A custom multi-location monitoring portal built on the **eero Business API** that puts the power of your network data in your hands. Build the dashboard that makes sense for *your* business — correlate WiFi usage with road traffic, overlay weather data, track uptime across every location, and spot problems before your customers do.

Built with Flask, Chart.js, Leaflet.js, and vanilla JS. Runs locally in minutes or deploys to Docker/AWS.

---

## 🚀 Why This Exists

This dashboard was built to show what's possible when you combine the eero Business API with the data sources that matter to *you*. Out-of-the-box dashboards give you generic metrics. This one lets a business owner ask questions like:

> *"Is my store busy because the road outside is busy?"*
> *"Which location has the worst WiFi health this week?"*
> *"Did that rainstorm yesterday kill foot traffic at my Pasadena location?"*

The answer to all of those is now one glance away. 👀

---

## ✨ Feature Highlights

### 🗺️ Interactive Location Map
All your locations on a single map with **color-coded health markers** — green for healthy, yellow for degraded, red for offline. Click any pin for a popup with details and a link to the full view. Powered by Leaflet.js + OpenStreetMap (no API key needed).

### 📸 Google Street View on Every Card
Each network card shows a **real photo of the building** pulled from Google Street View. No more guessing which "Network 20478317" is which — you can *see* it.

### 🚗 TomTom Traffic + WiFi Correlation
This is the one that gets people excited. Pull **real-time road traffic data** from TomTom and see it right alongside your WiFi client counts. Busy highway = busy store? Now you can actually prove it. Traffic dots show conditions throughout the day so you can spot patterns.

### 🌡️ Live Weather Per Location
Current weather from Weather Underground displayed right on each card. Because weather drives foot traffic, and now you can see the connection.

### 💚 Health Score Gauges
A **composite health score (0–100)** per location computed from eero node status, signal strength, uptime, and bandwidth. Displayed as a half-doughnut gauge with green/yellow/red coloring. One number tells you everything.

### ⏱️ Offline Countdown Timer
When a location goes down, a **big red countdown timer** appears on the card showing exactly how long it's been offline. Inspired by drive-thru timers — impossible to ignore, impossible to forget.

### 🔥 Peak Usage Heatmap
A 7-day × 24-hour heatmap showing **when each location is busiest**. Perfect for staffing decisions, capacity planning, or just understanding your business rhythms.

### 📊 Cross-Location Comparison
Side-by-side horizontal bar chart comparing **device count, signal strength, uptime, and bandwidth** across all your sites. Instantly spot the underperformer.

### 🎓 Network Report Card
Letter grades — **A, B, C, D, F** — for every location based on the past week's metrics. Weighted scoring across uptime (40%), signal (25%), incidents (20%), and bandwidth (15%). Universal language, no networking degree required.

### 📈 Uptime Timeline & Incident History
Visual bars showing **online/offline segments** over the last 24 hours. Plus a 7-day incident timeline on the detail page with color-coded events and hover tooltips.

### 🔔 Alert System with Sparkline
Automatic alerts when networks go offline, degrade, or hit bandwidth thresholds. A **mini sparkline** next to the bell icon shows the 7-day alert trend at a glance. Optional email notifications via SMTP.

### 🌗 Dark + Light Themes
Toggle between a polished dark theme (the default) and a clean light theme. All charts, maps, and UI elements adapt automatically.

### 📱 Mobile Responsive
Works on your phone, tablet, and desktop. Cards reflow, charts resize, maps support touch gestures. Monitor your network from anywhere.

---

## ⚡ Quick Start

```bash
git clone https://github.com/eero-drew/eero-business-dashboard.git
cd eero-business-dashboard
chmod +x run_local.sh
./run_local.sh
```

Dashboard runs at **http://localhost:5000** 🎉

The script creates a virtual environment, installs dependencies, and starts Flask. You're up in under a minute.

### 🔧 First-Time Setup

1. Open the dashboard → click **Admin**
2. **Manage Networks** → add your eero network ID, name, and email
3. **Authenticate** → enter the verification code sent to your email
4. Set a **physical address** for each location (unlocks map, Street View, traffic, and weather)
5. *(Optional)* Add a **TomTom API key** under Admin → Traffic API Key for road traffic data

---

## ⚙️ Configuration

All sensitive data stays local — nothing is committed to the repo.

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `EERO_PORT` | Server port | `5000` |
| `EERO_ENV` | `development` or `production` | `development` |
| `EERO_TIMEZONE` | Timezone for timestamps | `UTC` |
| `GOOGLE_MAPS_API_KEY` | Google Geocoding + Street View | _(optional)_ |
| `TOMTOM_API_KEY` | TomTom Traffic API | _(optional — also configurable in Admin UI)_ |
| `EERO_SMTP_HOST` | SMTP server for email alerts | _(optional)_ |
| `EERO_SMTP_PORT` | SMTP port | `587` |
| `EERO_SMTP_USER` | SMTP username | _(optional)_ |
| `EERO_SMTP_PASS` | SMTP password | _(optional)_ |
| `EERO_NOTIFY_ENABLED` | Enable email notifications | `false` |

### 🔒 Local Data Files (gitignored)

| File | Contents |
|---|---|
| `config.json` | Network IDs, names, emails, addresses, API keys |
| `data_cache.json` | Cached device/metric data for fast page loads |
| `.eero_token_*` | Per-network eero API authentication tokens |
| `dashboard.db` | SQLite database with historical metrics, alerts, uptime incidents |
| `streetview_cache/` | Cached Street View images |
| `static/uploads/` | Custom logo uploads |

---

## 🐳 Docker Deployment

```bash
docker build -t eero-business-dashboard .
docker run -d \
  --name eero-dashboard \
  -p 5000:5000 \
  -v $(pwd)/config.json:/app/config.json \
  -v $(pwd)/dashboard.db:/app/dashboard.db \
  -e GOOGLE_MAPS_API_KEY="your-key" \
  --restart unless-stopped \
  eero-business-dashboard
```

Or just run the deploy script:

```bash
chmod +x deploy.sh
./deploy.sh
```

---

## 🔌 API Endpoints

### Dashboard Data
| Endpoint | Description |
|---|---|
| `GET /api/networks` | List configured networks |
| `GET /api/network-stats` | Stats per network (devices, health, bandwidth, OS breakdown) |
| `GET /api/network/<id>/detail` | Full detail view (eero nodes, devices, alerts) |
| `GET /api/devices` | All devices across all networks |
| `GET /api/map-data` | Location coordinates + health for map rendering |
| `GET /api/weather` | Current weather per location |
| `GET /api/traffic` | TomTom road traffic per location |
| `GET /api/store-activity` | Store activity with traffic correlation |

### Insights
| Endpoint | Description |
|---|---|
| `GET /api/insights/heatmap` | 7×24 peak usage heatmap data |
| `GET /api/insights/uptime-timeline` | Per-network online/offline segments (24h) |
| `GET /api/alerts/trend` | Daily alert counts for last 7 days |
| `GET /api/reports/scorecard` | Letter grades + metric breakdowns per network |

### Reports & Alerts
| Endpoint | Description |
|---|---|
| `GET /api/reports/csv` | Export CSV report |
| `GET /api/reports/pdf` | Export PDF report |
| `GET /api/alerts` | Recent alerts |
| `POST /api/alerts/<id>/ack` | Acknowledge an alert |

### Admin
| Endpoint | Description |
|---|---|
| `POST /api/admin/networks` | Add a network |
| `DELETE /api/admin/networks/<id>` | Remove a network |
| `POST /api/admin/networks/<id>/auth` | Authenticate (send/verify code) |
| `PUT /api/admin/networks/<id>/address` | Set/update location address |
| `GET /api/streetview/<id>` | Street View image proxy (cached) |

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Flask, SQLAlchemy, SQLite, Gunicorn |
| Frontend | Vanilla JS, Chart.js, Leaflet.js, Font Awesome |
| APIs | eero Business API, Google Maps (Geocoding + Street View), TomTom Traffic, Weather Underground |
| Reports | reportlab (PDF), csv (CSV) |
| Testing | pytest, Hypothesis (property-based testing) |
| Deployment | Docker, AWS Lightsail |

---

## 📁 Project Structure

```
eero-business-dashboard/
├── app/
│   ├── dashboard.py        # Flask app, routes, cache, background refresh
│   ├── database.py         # SQLAlchemy models, metrics/alerts/uptime
│   ├── alerts.py           # Alert generation and management
│   ├── computations.py     # Health score, scorecard, signal bars
│   ├── geocoding.py        # Google + OpenStreetMap geocoding
│   ├── notifications.py    # Email notification system
│   ├── reports.py          # CSV and PDF report generation
│   └── session_manager.py  # Email-based auth and sessions
├── templates/
│   └── index.html          # Single-page dashboard (HTML + CSS + JS)
├── tests/
│   ├── test_computations.py
│   └── test_session_manager.py
├── static/
├── Dockerfile
├── deploy.sh
├── run_local.sh
├── requirements.txt
└── .gitignore
```

---

## 📄 License

Internal use. Not for redistribution.
