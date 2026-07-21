"""Tests for core/config.py — configuration defaults and types."""
from core.config import (
    PING_COUNT,
    PING_INTERVAL,
    PING_TIMEOUT,
    OFFLINE_THRESHOLD,
    ONLINE_THRESHOLD,
    RAW_PING_RETENTION_DAYS,
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
    assert isinstance(RAW_PING_RETENTION_DAYS, int)
    assert isinstance(MINUTE_STAT_RETENTION_DAYS, int)
    assert isinstance(EVENT_HISTORY_RETENTION_DAYS, int)
    assert RAW_PING_RETENTION_DAYS > 0
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


def test_session_ttl_is_int():
    assert isinstance(SESSION_TTL, int)
    assert SESSION_TTL > 0
    # Default is 24 hours in seconds
    assert SESSION_TTL == 24 * 3600
