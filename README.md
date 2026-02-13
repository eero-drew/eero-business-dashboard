# eero Business Dashboard

Multi-location network monitoring dashboard for businesses running eero networks. Monitor health, bandwidth, uptime, and device status across all your locations from a single interface.

## Quick Start

```bash
cd eero-business-dashboard
chmod +x run_local.sh
./run_local.sh
```

Dashboard runs at http://localhost:5000

## Features

- Card-based multi-network overview with health status indicators
- Interactive location map (OpenStreetMap/Leaflet.js)
- Google Street View images for each location
- Real-time device monitoring with signal strength
- Bandwidth utilization and uptime tracking
- Chart.js visualizations (device count, signal strength over time)
- Alert system with email notifications
- CSV and PDF report export
- Mobile-responsive design with eero branding

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `EERO_PORT` | Server port | `5000` |
| `EERO_ENV` | Environment (development/production) | `development` |
| `EERO_SMTP_HOST` | SMTP server for email notifications | _(optional)_ |
| `EERO_SMTP_PORT` | SMTP port | `587` |
| `EERO_SMTP_USER` | SMTP username | _(optional)_ |
| `EERO_SMTP_PASS` | SMTP password | _(optional)_ |
| `EERO_NOTIFY_ENABLED` | Enable email notifications | `false` |

### Adding Networks

1. Open the dashboard and click "Admin"
2. Click "Manage Networks"
3. Add network ID, name, and email
4. Authenticate with eero verification code
5. Set physical address for map display

## API Endpoints

- `GET /api/networks` — List configured networks
- `GET /api/network-stats` — Detailed stats per network
- `GET /api/devices` — All devices across networks
- `GET /api/map-data` — Location data for map
- `GET /api/alerts` — Recent alerts
- `GET /api/reports/csv` — Export CSV report
- `GET /api/reports/pdf` — Export PDF report

## Tech Stack

- Flask (Python)
- SQLAlchemy + SQLite
- Chart.js, Leaflet.js, Font Awesome
- Google Maps APIs (geocoding, Street View)
- reportlab (PDF generation)
