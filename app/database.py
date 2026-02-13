#!/usr/bin/env python3
"""
eero Business Dashboard - Database Layer
SQLAlchemy ORM models and database management for historical metrics,
uptime incidents, and alerts.
"""
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "dashboard.db")
DB_PATH = os.environ.get("EERO_DB_PATH", DEFAULT_DB_PATH)


# ---------------------------------------------------------------------------
# SQLAlchemy Base & Engine
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


def _build_engine(db_path=None):
    path = db_path or DB_PATH
    return create_engine(f"sqlite:///{path}", echo=False)


_engine = None
_SessionFactory = None


def _get_engine(db_path=None):
    global _engine
    if _engine is None:
        _engine = _build_engine(db_path)
    return _engine


def _get_session_factory(db_path=None):
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=_get_engine(db_path))
    return _SessionFactory


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class Network(Base):
    __tablename__ = "networks"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    address_street = Column(String, nullable=True)
    address_city = Column(String, nullable=True)
    address_state = Column(String, nullable=True)
    address_zip = Column(String, nullable=True)
    address_country = Column(String, nullable=True)
    address_formatted = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    network_id = Column(String, ForeignKey("networks.id"), nullable=False)
    timestamp = Column(String, nullable=False)
    total_devices = Column(Integer, nullable=True)
    wireless_devices = Column(Integer, nullable=True)
    wired_devices = Column(Integer, nullable=True)
    bandwidth_usage_mbps = Column(Float, nullable=True)
    bandwidth_capacity_mbps = Column(Float, nullable=True)
    bandwidth_utilization = Column(Float, nullable=True)
    avg_signal_dbm = Column(Float, nullable=True)

    __table_args__ = (
        Index("idx_metrics_network_time", "network_id", "timestamp"),
    )


class UptimeIncident(Base):
    __tablename__ = "uptime_incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    network_id = Column(String, ForeignKey("networks.id"), nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    affected_devices = Column(Text, nullable=True)  # JSON array of device IDs

    __table_args__ = (
        Index("idx_incidents_network_time", "network_id", "start_time"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    network_id = Column(String, ForeignKey("networks.id"), nullable=False)
    alert_type = Column(String, nullable=False)   # "offline", "degraded", "bandwidth"
    severity = Column(String, nullable=False)      # "critical", "warning"
    message = Column(String, nullable=False)
    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())
    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(String, nullable=True)

    __table_args__ = (
        Index("idx_alerts_network_time", "network_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Database Initialization
# ---------------------------------------------------------------------------

def init_db(db_path=None):
    """Create all tables. Safe to call multiple times (uses CREATE IF NOT EXISTS)."""
    global _engine, _SessionFactory
    _engine = _build_engine(db_path)
    _SessionFactory = sessionmaker(bind=_engine)
    Base.metadata.create_all(_engine)
    return _engine


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------

@contextmanager
def get_db_session(db_path=None):
    """Yield a SQLAlchemy session that auto-commits on success and rolls back on error."""
    factory = _get_session_factory(db_path)
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def insert_metric(
    network_id: str,
    timestamp: str,
    total_devices: Optional[int] = None,
    wireless_devices: Optional[int] = None,
    wired_devices: Optional[int] = None,
    bandwidth_usage_mbps: Optional[float] = None,
    bandwidth_capacity_mbps: Optional[float] = None,
    bandwidth_utilization: Optional[float] = None,
    avg_signal_dbm: Optional[float] = None,
    db_path: Optional[str] = None,
):
    """Insert a single metrics row."""
    with get_db_session(db_path) as session:
        metric = Metric(
            network_id=network_id,
            timestamp=timestamp,
            total_devices=total_devices,
            wireless_devices=wireless_devices,
            wired_devices=wired_devices,
            bandwidth_usage_mbps=bandwidth_usage_mbps,
            bandwidth_capacity_mbps=bandwidth_capacity_mbps,
            bandwidth_utilization=bandwidth_utilization,
            avg_signal_dbm=avg_signal_dbm,
        )
        session.add(metric)
    return metric


def get_metrics(network_id: str, since: Optional[str] = None, db_path: Optional[str] = None):
    """Return metrics for a network, optionally filtered by timestamp >= *since*."""
    with get_db_session(db_path) as session:
        query = session.query(Metric).filter(Metric.network_id == network_id)
        if since:
            query = query.filter(Metric.timestamp >= since)
        query = query.order_by(Metric.timestamp.asc())
        results = query.all()
        # Detach from session so callers can use them after session closes
        session.expunge_all()
        return results


def insert_uptime_incident(
    network_id: str,
    start_time: str,
    end_time: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    affected_devices: Optional[str] = None,
    db_path: Optional[str] = None,
):
    """Insert an uptime incident record."""
    with get_db_session(db_path) as session:
        incident = UptimeIncident(
            network_id=network_id,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration_seconds,
            affected_devices=affected_devices,
        )
        session.add(incident)
    return incident


def insert_alert(
    network_id: str,
    alert_type: str,
    severity: str,
    message: str,
    db_path: Optional[str] = None,
):
    """Insert a new alert."""
    with get_db_session(db_path) as session:
        alert = Alert(
            network_id=network_id,
            alert_type=alert_type,
            severity=severity,
            message=message,
        )
        session.add(alert)
    return alert


def get_alerts(
    network_id: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    db_path: Optional[str] = None,
):
    """Return alerts, optionally filtered by network and/or acknowledged status."""
    with get_db_session(db_path) as session:
        query = session.query(Alert)
        if network_id is not None:
            query = query.filter(Alert.network_id == network_id)
        if acknowledged is not None:
            query = query.filter(Alert.acknowledged == acknowledged)
        query = query.order_by(Alert.created_at.desc())
        results = query.all()
        session.expunge_all()
        return results


def acknowledge_alert(alert_id: int, db_path: Optional[str] = None):
    """Mark an alert as acknowledged. Returns True if the alert was found."""
    with get_db_session(db_path) as session:
        alert = session.query(Alert).filter(Alert.id == alert_id).first()
        if alert is None:
            return False
        alert.acknowledged = True
        alert.acknowledged_at = datetime.now(timezone.utc).isoformat()
        return True
