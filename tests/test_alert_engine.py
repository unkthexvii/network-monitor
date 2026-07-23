"""Tests for core/alert_engine.py — callback registry, message builder, and notification."""
import pytest
from core.alert_engine import (
    register_notify_callback,
    build_alert_message,
    notify_state_change,
)


# ── Callback Registry ──

def test_register_callback():
    called = []

    async def cb(event_type, data):
        called.append((event_type, data))

    register_notify_callback(cb)
    # After registration, the global should be set
    from core import alert_engine
    assert alert_engine._notify_callback is cb


def test_register_overwrites_previous():
    async def cb1(a, b):
        pass

    async def cb2(a, b):
        pass

    register_notify_callback(cb1)
    register_notify_callback(cb2)
    from core import alert_engine
    assert alert_engine._notify_callback is cb2


# ── build_alert_message ──

class FakeDevice:
    def __init__(self, name="TestDev", ip_address="10.0.0.1"):
        self.name = name
        self.ip_address = ip_address
        self.id = 1


def test_message_initialized():
    dev = FakeDevice()
    msg, alert_type = build_alert_message("UNKNOWN", "ONLINE", dev)
    assert "Device Initialized" in msg
    assert "TestDev" in msg
    assert "10.0.0.1" in msg
    assert alert_type == "INITIALIZED"


def test_message_offline():
    dev = FakeDevice()
    msg, alert_type = build_alert_message("ONLINE", "OFFLINE", dev)
    assert "went OFFLINE" in msg
    assert alert_type == "OFFLINE"


def test_message_online():
    dev = FakeDevice()
    msg, alert_type = build_alert_message("OFFLINE", "ONLINE", dev)
    assert "went ONLINE" in msg
    assert alert_type == "ONLINE"


def test_message_paused():
    dev = FakeDevice()
    msg, alert_type = build_alert_message("ONLINE", "PAUSED", dev)
    assert "was paused" in msg
    assert alert_type == "PAUSED"


def test_message_resumed():
    dev = FakeDevice()
    msg, alert_type = build_alert_message("PAUSED", "RESUMED", dev)
    assert "was resumed" in msg
    assert alert_type == "RESUMED"


# ── notify_state_change ──

@pytest.mark.asyncio
async def test_notify_state_change_no_callback():
    """Should not raise when no callback is registered."""
    # Ensure no callback is registered
    from core import alert_engine
    alert_engine._notify_callback = None
    dev = FakeDevice()
    await notify_state_change("ONLINE", "OFFLINE", dev)  # should not raise


@pytest.mark.asyncio
async def test_notify_state_change_with_callback():
    """Should dispatch event data to the registered callback."""
    received = []

    async def cb(event_type, data):
        received.append((event_type, data))

    register_notify_callback(cb)
    dev = FakeDevice()
    await notify_state_change("ONLINE", "OFFLINE", dev)

    assert len(received) == 1
    event_type, data = received[0]
    assert event_type == "status_change"
    assert data["device_id"] == 1
    assert data["device_name"] == "TestDev"
    assert data["ip_address"] == "10.0.0.1"
    assert data["status"] == "OFFLINE"
    assert "went OFFLINE" in data["message"]
    assert "timestamp" in data
