#!/bin/bash
# eero Business Dashboard - Local Development Runner
# Usage: ./run_local.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt -q

# Set development defaults
export EERO_ENV="${EERO_ENV:-development}"
export EERO_PORT="${EERO_PORT:-5000}"
export FLASK_DEBUG="${FLASK_DEBUG:-1}"

# Note: Maps use OpenStreetMap via Leaflet.js (no API key needed)

# Optional: Email notifications
# export EERO_SMTP_HOST="smtp.gmail.com"
# export EERO_SMTP_PORT="587"
# export EERO_SMTP_USER="your-email@gmail.com"
# export EERO_SMTP_PASS="your-app-password"
# export EERO_NOTIFY_ENABLED="true"

echo ""
echo "Starting eero Business Dashboard on http://localhost:${EERO_PORT}"
echo "Environment: ${EERO_ENV}"
echo ""

python -m app.dashboard
