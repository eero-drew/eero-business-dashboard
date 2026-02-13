# eero Business Dashboard

A custom multi-location network monitoring portal built on the **eero Business API**. Designed to demonstrate how business owners can build tailored dashboards that surface the metrics that matter most to them — including correlating WiFi usage with external data like road traffic and weather.

Built with Flask, Chart.js, Leaflet.js, and vanilla JavaScript. Runs locally or in Docker/AWS.

---

## What It Does

Monitor all your eero network locations from a single dashboard. Each location gets a card showing real-time health, device counts, bandwidth, and more. Click into any location for deep-dive metrics.

### At a Glance

- **Multi-network card grid** — One card per location with health status color coding (green/yellow/red)
- **Health score gauges** — Composite score (0–100) per location based on node status, signal strength, uptime, and bandwidth
- **Bandwidth utilization gauges** — See how close each location is to ISP capacity
- **Interactive location map** — All locations plotted on an OpenStreetMap/Leaflet.js map with color-coded health markers
- **Google Street View** — Photo of each location's building right on the card
- **Offline countdown timer** — When a location goes down, a prominent red timer shows how long it's been offline

### Insights & Analytics

- **Peak usage heatmap** — 7-day × 24-hour grid showing when locations are busiest
- **Cross-location comparison** — Side-by-side bar chart comparing device count, signal, uptime, and bandwidth across all sites
- **Uptime timeline** — Visual 24-hour bar per location showing online (green) and offline (red) segments
- **Incident timeline** — 7-day history of offline events, degraded periods, and recoveries on the detail page
- **Alert sparkline** — 7-day alert trend line in the header next to the bell icon
- **Network report card** — Letter grades (A/B/C/D/F) per location based on weighted metrics over the past week

### External Data Integrations

- **TomTom Traffic** — Real-time road traffic conditions near each location, correlated with WiFi client counts. See whether busy roads mean busy stores.
- **Weather Underground** — Current weather displayed on each card. Understand how weather impacts foot traffic.

### Operations

- **Alert system** — Automatic alerts on network offline, degraded, or bandwidth threshold events. Optional email notifications via SMTP.
- **CSV and PDF reports** — Export network metrics for stakeholders or offline analysis
- **Device detail modal** — Full device list with OS detection, signal strength, frequency band, and connection type
- **eero node monitoring** — Signal bars, firmware version tracking, and firmware consistency checks

### Design

- **Dark and light themes** — Toggle between themes; dark is the default
- **Mobile responsive** — Works on phone, tablet, and desktop
- **eero brand styling** — Color palette, typography, and iconography aligned with eero design language

---

## Quick Start

```bash
git clone https://github.com/eero-drew/eero-business-dashboard.git
cd eero-business-dashboard
chmod +x run_local.sh
./run_local.sh
```

Dashboard runs at **http://localhost:5000**

The `run_local.sh` script creates a Python virtual environment, installs dependencies, and starts the Flask server.

### First-Time Setup

1. Open the dashboard and click **Admin**
2. Click **Manage Networks** → add your eero network ID, a name, and your email
3. Click **Authenticate** → enter the verification code sent to your email
4. Set a **physical address** for each location (enables map, Street View, traffic, and weather)
5. Optionally add a **TomTom API key** (Admin → Traffic API Key) for road traffic data

---

## Configuration

All sensitive configuration is stored locally and never committed to the repo.

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `EERO_PORT` | Server port | `5000` |
| `EERO_ENV` | `development` or `production` | `development` |
| `EERO_TIMEZONE` | Timezone for timestamps | `UTC` |
| `GOOGLE_MAPS_API_KEY` | Google Geocoding + Street View | _(optional)_ |
| `TOMTOM_API_KEY` | TomTom Traffic API | _(optional, also configurable in Admin UI)_ |
| `EERO_SMTP_HOST` | SMTP server for email alerts | _(optional)_ |
| `EERO_SMTP_PORT` | SMTP port | `587` |
| `EERO_SMTP_USER` | SMTP username | _(optional)_ |
| `EERO_SMTP_PASS` | SMTP password | _(optional)_ |
| `EERO_NOTIFY_ENABLED` | Enable email notifications | `false` |

### Local Data Files (gitignored)

| File | Contents |
|---|---|
| `config.json` | Network IDs, names, emails, addresses, API keys |
| `data_cache.json` | Cached device/metric data for fast page loads |
| `.eero_token_*` | Per-network eero API authentication tokens |
| `dashboard.db` | SQLite database with historical metrics, alerts, uptime incidents |
| `streetview_cache/` | Cached Street View images |
| `static/uploads/` | Custom logo uploads |

---

## Docker Deployment

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

Or use the included deploy script:

```bash
chmod +x deploy.sh
./deploy.sh
```

---

## API Endpoints

### Dashboard Data
| Endpoint | Description |
|---|---|
| `GET /api/networks` | List configured networks |
| `GET /api/network-stats` | Stats per network (devices, health, bandwidth, OS distribution) |
| `GET /api/network/<id>/detail` | Full detail for a single network (eero nodes, devices, alerts) |
| `GET /api/devices` | All devices across all networks |
| `GET /api/map-data` | Location coordinates and health for map rendering |
| `GET /api/weather` | Current weather per location |
| `GET /api/traffic` | TomTom road traffic per location |
| `GET /api/store-activity` | Store activity metrics with traffic correlation |

### Insights
| Endpoint | Description |
|---|---|
| `GET /api/insights/heatmap` | 7x24 peak usage heatmap data |
| `GET /api/insights/uptime-timeline` | Per-network online/offline segments (24h) |
| `GET /api/alerts/trend` | Daily alert counts for last 7 days |
| `GET /api/reports/scorecard` | Letter grades and metric breakdowns per network |

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
| `POST /api/admin/networks/<id>/auth` | Authenticate a network (send/verify code) |
| `PUT /api/admin/networks/<id>/address` | Set/update location address |
| `GET /api/streetview/<id>` | Street View image proxy (cached) |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Flask, SQLAlchemy, SQLite, Gunicorn |
| Frontend | Vanilla JS, Chart.js, Leaflet.js, Font Awesome |
| APIs | eero Business API, Google Maps (Geocoding + Street View), TomTom Traffic, Weather Underground |
| Reports | reportlab (PDF), csv (CSV) |
| Testing | pytest, Hypothesis (property-based testing) |
| Deployment | Docker, AWS Lightsail |

---

## Project Structure

```
eero-business-dashboard/
├── app/
│   ├── dashboard.py        # Flask app, API routes, data cache, background refresh
│   ├── database.py         # SQLAlchemy models, metrics/alerts/uptime storage
│   ├── alerts.py           # Alert generation and management
│   ├── computations.py     # Health score, scorecard, signal bars, firmware checks
│   ├── geocoding.py        # Google + OpenStreetMap geocoding
│   ├── notifications.py    # Email notification system
│   ├── reports.py          # CSV and PDF report generation
│   └── session_manager.py  # Email-based auth and session management
├── templates/
│   └── index.html          # Single-page dashboard (HTML + CSS + JS)
├── tests/
│   ├── test_computations.py
│   └── test_session_manager.py
├── static/
│   ├── css/
│   └── js/
├── Dockerfile
├── deploy.sh
├── run_local.sh
├── requirements.txt
└── .gitignore
```

---

## License

Internal use. Not for redistribution.
