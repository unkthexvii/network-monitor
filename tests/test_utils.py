"""Tests for core/utils.py — alert messages, timeframe bounds, check intervals."""
from datetime import datetime, timedelta, timezone
from core.utils import (
    build_alert_messages,
    format_time_duration,
    get_timeframe_bounds,
    get_default_check_interval,
    parse_utc_iso,
)


class FakeAlert:
    def __init__(self, alert_type, device_id, created_at, message=""):
        self.alert_type = alert_type
        self.device_id = device_id
        self.created_at = created_at
        self.message = message


# ── format_time_duration ──

def test_format_time_duration_seconds():
    assert format_time_duration(45) == "0m 45s"


def test_format_time_duration_minutes():
    assert format_time_duration(125) == "2m 5s"


def test_format_time_duration_hours():
    assert format_time_duration(3661) == "1h 1m 1s"


# ── parse_utc_iso ──

def test_parse_utc_iso_with_z():
    dt = parse_utc_iso("2026-07-21T12:00:00Z")
    assert dt.year == 2026
    assert dt.month == 7
    assert dt.hour == 12
    assert dt.tzinfo is None  # timezone-naive UTC


def test_parse_utc_iso_without_t():
    # Without T, parse_utc_iso treats the string as local time and converts to UTC
    dt = parse_utc_iso("2026-07-21 12:00:00")
    assert dt.tzinfo is None  # always returns naive UTC
    assert dt.year == 2026
    assert dt.month == 7


def test_parse_utc_iso_with_millis():
    dt = parse_utc_iso("2026-07-21 12:00:00.123456")
    assert dt.microsecond == 123456


# ── build_alert_messages ──

def test_build_alert_messages_offline_online_downtime():
    now = datetime(2026, 7, 21, 12, 0, 0)
    alerts = [
        FakeAlert("ONLINE", 1, now, ""),
        FakeAlert("OFFLINE", 1, now - timedelta(minutes=5), ""),
    ]
    msgs = build_alert_messages(alerts)
    assert "Connection Restored" in msgs[0]
    assert "Downtime: 5m 0s" in msgs[0]
    assert msgs[1] == "Connection Lost"


def test_build_alert_messages_paused_resumed():
    now = datetime(2026, 7, 21, 12, 0, 0)
    alerts = [
        FakeAlert("RESUMED", 1, now, ""),
        FakeAlert("PAUSED", 1, now - timedelta(hours=2), ""),
    ]
    msgs = build_alert_messages(alerts)
    assert "Monitoring Resumed" in msgs[0]
    assert "2h 0m 0s" in msgs[0]
    assert msgs[1] == "Monitoring Paused"


def test_build_alert_messages_empty():
    assert build_alert_messages([]) == []


def test_build_alert_messages_custom_message():
    now = datetime(2026, 7, 21, 12, 0, 0)
    alerts = [
        FakeAlert("CUSTOM", 1, now, "Something happened"),
    ]
    msgs = build_alert_messages(alerts)
    assert msgs[0] == "Something happened"


# ── get_timeframe_bounds ──

def test_timeframe_24h():
    start, end, blocks, delta, label = get_timeframe_bounds("24h")
    assert (end - start) == timedelta(hours=24)
    assert blocks == 24
    assert label == "Hour"


def test_timeframe_7d():
    start, end, blocks, delta, label = get_timeframe_bounds("7d")
    assert (end - start) == timedelta(days=7)
    assert blocks == 28
    assert label == "6-Hour Block"


def test_timeframe_30d():
    start, end, blocks, delta, label = get_timeframe_bounds("30d")
    assert (end - start) == timedelta(days=30)
    assert blocks == 30
    assert label == "Day"


def test_timeframe_unknown_defaults_to_24h():
    start, end, blocks, delta, label = get_timeframe_bounds("unknown")
    assert (end - start) == timedelta(hours=24)


# ── get_default_check_interval ──

def test_default_interval_router():
    assert get_default_check_interval("router") == 60


def test_default_interval_switch():
    assert get_default_check_interval("switch") == 60


def test_default_interval_server():
    assert get_default_check_interval("server") == 60


def test_default_interval_ap():
    assert get_default_check_interval("access point") == 120


def test_default_interval_ups():
    assert get_default_check_interval("ups") == 120


def test_default_interval_workstation():
    assert get_default_check_interval("workstation") == 300


def test_default_interval_printer():
    assert get_default_check_interval("printer") == 300


def test_default_interval_unknown():
    assert get_default_check_interval("unknown_type") == 60


def test_default_interval_empty():
    assert get_default_check_interval("") == 60


def test_default_interval_none():
    assert get_default_check_interval(None) == 60
