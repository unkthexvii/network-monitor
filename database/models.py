from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean,
    ForeignKey, Index, func
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _utcnow():
    """Timezone-naive UTC now, replacing deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Device(Base):
    __tablename__ = 'devices'
    __table_args__ = (
        Index('ix_devices_enabled', 'enabled'),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    ip_address = Column(String, unique=True, nullable=False)
    site = Column(String)
    location = Column(String)
    rack = Column(String)
    device_type = Column(String)
    vendor = Column(String)
    model = Column(String)
    check_interval = Column(Integer, default=60)
    enabled = Column(Boolean, default=True)
    remark = Column(String)
    
    # SNMP Settings
    snmp_version = Column(String, default="None") # None, v2c, v3
    snmp_community = Column(String)
    snmp_v3_user = Column(String)
    snmp_v3_auth = Column(String)
    snmp_v3_priv = Column(String)

    # Float type — SQLite uses dynamic typing so existing Integer values
    # in old databases are automatically readable as floats. No migration needed.
    latency_threshold_ms = Column(Float, default=200.0)
    packet_loss_threshold = Column(Float, default=0.20)
    created_at = Column(DateTime, default=_utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, server_default=func.now())

    # Relationships
    status = relationship("DeviceStatus", back_populates="device", uselist=False, cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="device", cascade="all, delete-orphan")


class TopologyTab(Base):
    __tablename__ = 'topology_tabs'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow, server_default=func.now())


class TopologyNode(Base):
    __tablename__ = 'topology_nodes'

    tab_id = Column(Integer, ForeignKey('topology_tabs.id', ondelete='CASCADE'), primary_key=True)
    device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'), primary_key=True)
    pos_x = Column(Float)
    pos_y = Column(Float)
    icon_type = Column(String)


class TopologyLink(Base):
    __tablename__ = 'topology_links'

    id = Column(Integer, primary_key=True)
    tab_id = Column(Integer, ForeignKey('topology_tabs.id', ondelete='CASCADE'))
    parent_device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'))
    child_device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'))
    link_type = Column(String)
    created_at = Column(DateTime, default=_utcnow, server_default=func.now())


class DeviceStatus(Base):
    __tablename__ = 'device_status'
    __table_args__ = (
        Index('ix_device_status_status', 'status'),
    )

    device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'), primary_key=True)
    status = Column(String, default="UNKNOWN")
    latency_ms = Column(Float, default=0.0)
    packet_loss = Column(Float, default=0.0)
    last_seen = Column(DateTime)
    offline_since = Column(DateTime)
    fail_count = Column(Integer, default=0)
    recovery_count = Column(Integer, default=0)
    
    # SNMP Fetched Data
    sys_name = Column(String)
    sys_contact = Column(String)
    sys_location = Column(String)
    sys_descr = Column(String)
    sys_uptime = Column(String)
    client_count = Column(Integer)
    ap_count = Column(Integer)
    serial_number = Column(String)
    snmp_custom_data = Column(String) # JSON payload

    device = relationship("Device", back_populates="status")


class Alert(Base):
    __tablename__ = 'alerts'
    __table_args__ = (
        Index('ix_alerts_device_created', 'device_id', 'created_at'),
        Index('ix_alerts_created', 'created_at'),
    )

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'))
    alert_type = Column(String)
    message = Column(String)
    created_at = Column(DateTime, default=_utcnow, server_default=func.now())

    device = relationship("Device", back_populates="alerts")


class MinuteStat(Base):
    __tablename__ = 'minute_stats'
    __table_args__ = (
        Index('ix_minute_stats_device_minute', 'device_id', 'minute'),
    )

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey('devices.id', ondelete='CASCADE'))
    minute = Column(DateTime)
    avg_latency = Column(Float)
    min_latency = Column(Float)
    max_latency = Column(Float)
    packet_loss = Column(Float)
    uptime_percent = Column(Float)


class Setting(Base):
    __tablename__ = 'settings'

    key = Column(String, primary_key=True)
    value = Column(String)
    description = Column(String)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, server_default=func.now())
