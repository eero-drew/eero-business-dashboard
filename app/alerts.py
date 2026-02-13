#!/usr/bin/env python3
"""
eero Business Dashboard - Alert System
Detects health status transitions and generates alerts.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from app.database import insert_alert, get_alerts, acknowledge_alert, get_db_session, Alert
from app.notifications import notify_alert

logger = logging.getLogger(__name__)

# Track previous health status per network for transition detection
_previous_health: dict[str, str] = {}


def check_health_transition(network_id: str, network_name: str, new_status: str) -> Optional[dict]:
    """
    Check if a network's health status has changed and generate an alert if needed.
    
    Returns the alert dict if one was generated, None otherwise.
    """
    old_status = _previous_health.get(network_id)
    _previous_health[network_id] = new_status

    if old_status is None or old_status == new_status:
        return None

    alert = None

    # Transition to offline -> critical alert
    if new_status == "offline":
        alert = {
            "network_id": network_id,
            "alert_type": "offline",
            "severity": "critical",
            "message": f"{network_name} has gone offline — all devices are unreachable.",
        }

    # Transition to degraded -> warning alert
    elif new_status == "degraded" and old_status == "healthy":
        alert = {
            "network_id": network_id,
            "alert_type": "degraded",
            "severity": "warning",
            "message": f"{network_name} is degraded — 50% or fewer devices are online.",
        }

    # Recovery from offline/degraded -> info (no DB alert, just log)
    elif new_status == "healthy" and old_status in ("offline", "degraded"):
        logger.info("Network %s (%s) recovered to healthy", network_id, network_name)
        return None

    if alert:
        try:
            insert_alert(
                network_id=alert["network_id"],
                alert_type=alert["alert_type"],
                severity=alert["severity"],
                message=alert["message"],
            )
            logger.warning("Alert generated: %s", alert["message"])
            notify_alert(alert)
        except Exception as e:
            logger.error("Failed to persist alert: %s", e)
        return alert

    return None


def check_bandwidth_alert(network_id: str, network_name: str, utilization: float) -> Optional[dict]:
    """
    Generate a critical alert if bandwidth utilization exceeds 95%.
    Simple threshold check — the 5-minute sustained check would require
    historical tracking which is handled by the metrics collection layer.
    """
    if utilization > 95:
        alert = {
            "network_id": network_id,
            "alert_type": "bandwidth",
            "severity": "critical",
            "message": f"{network_name} bandwidth at {utilization:.1f}% — exceeds 95% threshold.",
        }
        try:
            insert_alert(
                network_id=alert["network_id"],
                alert_type=alert["alert_type"],
                severity=alert["severity"],
                message=alert["message"],
            )
            logger.warning("Bandwidth alert: %s", alert["message"])
            notify_alert(alert)
        except Exception as e:
            logger.error("Failed to persist bandwidth alert: %s", e)
        return alert
    return None


def process_network_alerts(network_id: str, network_name: str, health_status: str, bandwidth_utilization: float = 0.0):
    """
    Run all alert checks for a single network. Called during each cache update cycle.
    Returns list of any alerts generated.
    """
    alerts = []
    
    health_alert = check_health_transition(network_id, network_name, health_status)
    if health_alert:
        alerts.append(health_alert)

    bw_alert = check_bandwidth_alert(network_id, network_name, bandwidth_utilization)
    if bw_alert:
        alerts.append(bw_alert)

    return alerts


def get_recent_alerts(limit: int = 50, network_id: Optional[str] = None) -> list[dict]:
    """
    Fetch recent alerts from the database, formatted for the frontend.
    """
    try:
        alerts = get_alerts(network_id=network_id)
        result = []
        for a in alerts[:limit]:
            result.append({
                "id": a.id,
                "network_id": a.network_id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "message": a.message,
                "created_at": a.created_at,
                "acknowledged": a.acknowledged,
                "acknowledged_at": a.acknowledged_at,
            })
        return result
    except Exception as e:
        logger.error("Failed to fetch alerts: %s", e)
        return []


def get_unacknowledged_count() -> int:
    """Return count of unacknowledged alerts."""
    try:
        alerts = get_alerts(acknowledged=False)
        return len(alerts)
    except Exception:
        return 0


def ack_alert(alert_id: int) -> bool:
    """Acknowledge an alert by ID."""
    try:
        return acknowledge_alert(alert_id)
    except Exception as e:
        logger.error("Failed to acknowledge alert %d: %s", alert_id, e)
        return False


def reset_health_tracking():
    """Reset the in-memory health status tracking. Useful for testing."""
    global _previous_health
    _previous_health = {}
