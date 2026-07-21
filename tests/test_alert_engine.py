"""Tests for core/alert_engine.py — callback registry."""
from core.alert_engine import register_notify_callback, _notify_callback


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
