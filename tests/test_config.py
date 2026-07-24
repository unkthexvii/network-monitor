"""Tests for core/config.py — configuration defaults and types."""
import importlib
import os
import pytest
from core.config import (
    PING_COUNT,
    PING_INTERVAL,
    PING_TIMEOUT,
    OFFLINE_THRESHOLD,
    ONLINE_THRESHOLD,
    MINUTE_STAT_RETENTION_DAYS,
    EVENT_HISTORY_RETENTION_DAYS,
    DATABASE_URL,
    CACHE_TTL_SECONDS,
    READONLY,
    DEFAULT_ADMIN_PASSWORD,
    SESSION_TTL,
)


def test_ping_count_is_int():
    assert isinstance(PING_COUNT, int)
    assert PING_COUNT >= 1


def test_ping_interval_is_float():
    assert isinstance(PING_INTERVAL, float)
    assert PING_INTERVAL > 0


def test_ping_timeout_is_float():
    assert isinstance(PING_TIMEOUT, float)
    assert PING_TIMEOUT > 0


def test_offline_threshold_is_int():
    assert isinstance(OFFLINE_THRESHOLD, int)
    assert OFFLINE_THRESHOLD >= 1


def test_online_threshold_is_int():
    assert isinstance(ONLINE_THRESHOLD, int)
    assert ONLINE_THRESHOLD >= 1


def test_retention_days_are_ints():
    assert isinstance(MINUTE_STAT_RETENTION_DAYS, int)
    assert isinstance(EVENT_HISTORY_RETENTION_DAYS, int)
    assert MINUTE_STAT_RETENTION_DAYS > 0
    assert EVENT_HISTORY_RETENTION_DAYS > 0


def test_database_url_is_string():
    assert isinstance(DATABASE_URL, str)
    assert "sqlite" in DATABASE_URL


def test_cache_ttl_is_int():
    assert isinstance(CACHE_TTL_SECONDS, int)
    assert CACHE_TTL_SECONDS > 0


def test_readonly_is_bool():
    assert isinstance(READONLY, bool)


def test_default_admin_password_is_string():
    assert isinstance(DEFAULT_ADMIN_PASSWORD, str)
    assert len(DEFAULT_ADMIN_PASSWORD) > 0
    # Default password must not be the insecure "admin"
    assert DEFAULT_ADMIN_PASSWORD != "admin"


def test_session_ttl_is_int():
    assert isinstance(SESSION_TTL, int)
    assert SESSION_TTL > 0
    # Default is 24 hours in seconds
    assert SESSION_TTL == 24 * 3600


# ── Environment variable override tests ──


def _reload_config(monkeypatch, env_overrides):
    """Set env vars, reload core.config, and return the fresh module."""
    for key, value in env_overrides.items():
        monkeypatch.setenv(key, value)
    import core.config
    importlib.reload(core.config)
    return core.config


def test_ping_count_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_PING_COUNT": "7"})
    assert cfg.PING_COUNT == 7


def test_ping_interval_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_PING_INTERVAL": "0.5"})
    assert cfg.PING_INTERVAL == 0.5


def test_ping_timeout_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_PING_TIMEOUT": "3.0"})
    assert cfg.PING_TIMEOUT == 3.0


def test_offline_threshold_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_OFFLINE_THRESHOLD": "5"})
    assert cfg.OFFLINE_THRESHOLD == 5


def test_online_threshold_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_ONLINE_THRESHOLD": "10"})
    assert cfg.ONLINE_THRESHOLD == 10


def test_stat_retention_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_STAT_RETENTION_DAYS": "30"})
    assert cfg.MINUTE_STAT_RETENTION_DAYS == 30


def test_event_retention_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_EVENT_RETENTION_DAYS": "365"})
    assert cfg.EVENT_HISTORY_RETENTION_DAYS == 365


def test_database_url_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_DATABASE_URL": "sqlite+aiosqlite:///custom.db"})
    assert cfg.DATABASE_URL == "sqlite+aiosqlite:///custom.db"


def test_cache_ttl_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_CACHE_TTL": "120"})
    assert cfg.CACHE_TTL_SECONDS == 120


def test_readonly_override_true(monkeypatch):
    """MONITOR_READONLY=1 enables read-only mode."""
    cfg = _reload_config(monkeypatch, {"MONITOR_READONLY": "1"})
    assert cfg.READONLY is True


def test_readonly_override_true_string(monkeypatch):
    """MONITOR_READONLY=true enables read-only mode."""
    cfg = _reload_config(monkeypatch, {"MONITOR_READONLY": "true"})
    assert cfg.READONLY is True


def test_readonly_override_yes(monkeypatch):
    """MONITOR_READONLY=yes enables read-only mode."""
    cfg = _reload_config(monkeypatch, {"MONITOR_READONLY": "yes"})
    assert cfg.READONLY is True


def test_readonly_override_case_insensitive(monkeypatch):
    """MONITOR_READONLY=TRUE (uppercase) still enables read-only mode."""
    cfg = _reload_config(monkeypatch, {"MONITOR_READONLY": "TRUE"})
    assert cfg.READONLY is True


def test_readonly_override_disabled(monkeypatch):
    """MONITOR_READONLY unset or empty means read-only is off."""
    cfg = _reload_config(monkeypatch, {"MONITOR_READONLY": ""})
    assert cfg.READONLY is False


def test_readonly_override_invalid_value(monkeypatch):
    """MONITOR_READONLY=banana is not a recognised truthy value."""
    cfg = _reload_config(monkeypatch, {"MONITOR_READONLY": "banana"})
    assert cfg.READONLY is False


def test_session_hours_override(monkeypatch):
    """MONITOR_SESSION_HOURS=48 gives 48*3600 seconds."""
    cfg = _reload_config(monkeypatch, {"MONITOR_SESSION_HOURS": "48"})
    assert cfg.SESSION_TTL == 48 * 3600


def test_default_password_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MONITOR_DEFAULT_PASSWORD": "s3cret"})
    assert cfg.DEFAULT_ADMIN_PASSWORD == "s3cret"


def test_default_password_random_when_unset(monkeypatch):
    """When MONITOR_DEFAULT_PASSWORD is not set, a random token is generated."""
    monkeypatch.delenv("MONITOR_DEFAULT_PASSWORD", raising=False)
    import core.config
    importlib.reload(core.config)
    assert len(core.config.DEFAULT_ADMIN_PASSWORD) > 0
    assert core.config.DEFAULT_ADMIN_PASSWORD != "admin"
