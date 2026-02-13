#!/bin/bash
# eero Business Dashboard - Docker Deployment Script
set -e

IMAGE_NAME="eero-business-dashboard"
CONTAINER_NAME="eero-dashboard"
PORT="${EERO_PORT:-5000}"

echo "Building Docker image..."
docker build -t "$IMAGE_NAME" .

echo "Stopping existing container (if any)..."
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

echo "Starting container..."
docker run -d \
  --name "$CONTAINER_NAME" \
  -p "$PORT:5000" \
  -v "$(pwd)/config.json:/app/config.json" \
  -v "$(pwd)/dashboard.db:/app/dashboard.db" \
  -e EERO_SMTP_HOST="${EERO_SMTP_HOST:-}" \
  -e EERO_SMTP_USER="${EERO_SMTP_USER:-}" \
  -e EERO_SMTP_PASS="${EERO_SMTP_PASS:-}" \
  -e EERO_NOTIFY_ENABLED="${EERO_NOTIFY_ENABLED:-false}" \
  --restart unless-stopped \
  "$IMAGE_NAME"

echo "Dashboard running at http://localhost:$PORT"
