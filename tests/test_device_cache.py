"""Tests for core/device_cache.py — CachedDevice dataclass."""
from datetime import datetime
from core.device_cache import CachedDevice


def test_cached_device_defaults():
    dev = CachedDevice(id=1, name="Test", ip_address="10.0.0.1", check_interval=60, enabled=True)
    assert dev.id == 1
    assert dev.name == "Test"
    assert dev.ip_address == "10.0.0.1"
    assert dev.status == "UNKNOWN"
    assert dev.latency_ms == 0.0
    assert dev.packet_loss == 0.0
    assert dev.fail_count == 0
    assert dev.recovery_count == 0
    assert dev.dirty is False
    assert dev.last_ping_start == 0.0
    assert dev.first_fail_time is None


def test_cached_device_custom_thresholds():
    dev = CachedDevice(
        id=2, name="Router", ip_address="10.0.0.2",
        check_interval=30, enabled=True,
        latency_threshold_ms=500.0, packet_loss_threshold=0.50
    )
    assert dev.latency_threshold_ms == 500.0
    assert dev.packet_loss_threshold == 0.50


def test_cached_device_status_fields():
    now = datetime(2026, 7, 21, 12, 0, 0)
    dev = CachedDevice(
        id=3, name="AP", ip_address="10.0.0.3",
        check_interval=120, enabled=False
    )
    dev.status = "ONLINE"
    dev.latency_ms = 15.5
    dev.packet_loss = 0.05
    dev.last_seen = now
    dev.offline_since = None
    dev.fail_count = 0
    dev.recovery_count = 1

    assert dev.status == "ONLINE"
    assert dev.latency_ms == 15.5
    assert dev.packet_loss == 0.05
    assert dev.last_seen == now
    assert dev.enabled is False
