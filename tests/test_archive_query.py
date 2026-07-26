"""Tests for core/archive_query.py — date parsing."""
from datetime import datetime
import pytest
from core.archive_query import parse_db_date


def test_parse_db_date_iso_with_t():
    dt = parse_db_date("2026-07-21T12:00:00")
    assert dt == datetime(2026, 7, 21, 12, 0, 0)


def test_parse_db_date_with_millis():
    dt = parse_db_date("2026-07-21 12:00:00.123456")
    assert dt == datetime(2026, 7, 21, 12, 0, 0, 123456)


def test_parse_db_date_without_millis():
    dt = parse_db_date("2026-07-21 12:00:00")
    assert dt == datetime(2026, 7, 21, 12, 0, 0)


def test_parse_db_date_strips_timezone():
    dt = parse_db_date("2026-07-21T12:00:00+00:00")
    assert dt.tzinfo is None
    assert dt.hour == 12


# -- Edge-case date format tests --


def test_parse_db_date_iso_with_positive_offset():
    """ISO format with a positive UTC offset (e.g. +05:30)."""
    dt = parse_db_date("2026-07-21T17:30:00+05:30")
    # fromisoformat strips tzinfo via replace(tzinfo=None), returning the wall-clock time
    assert dt.tzinfo is None
    assert dt == datetime(2026, 7, 21, 17, 30, 0)


def test_parse_db_date_iso_with_negative_offset():
    """ISO format with a negative UTC offset."""
    dt = parse_db_date("2026-07-21T07:00:00-05:00")
    assert dt.tzinfo is None
    assert dt == datetime(2026, 7, 21, 7, 0, 0)


def test_parse_db_date_z_suffix():
    """Z suffix (UTC indicator) is normalized to +00:00 for Python 3.8 compat."""
    dt = parse_db_date("2026-07-21T12:00:00Z")
    assert dt.tzinfo is None
    assert dt == datetime(2026, 7, 21, 12, 0, 0)


def test_parse_db_date_fractional_seconds_millis():
    """Fractional seconds with exactly 3 digits (milliseconds)."""
    dt = parse_db_date("2026-07-21 12:00:00.456")
    assert dt == datetime(2026, 7, 21, 12, 0, 0, 456000)


def test_parse_db_date_fractional_seconds_micros():
    """Fractional seconds with exactly 6 digits (microseconds)."""
    dt = parse_db_date("2026-07-21 12:00:00.654321")
    assert dt == datetime(2026, 7, 21, 12, 0, 0, 654321)


def test_parse_db_date_midnight():
    """Date string at midnight."""
    dt = parse_db_date("2026-01-01 00:00:00")
    assert dt == datetime(2026, 1, 1, 0, 0, 0)


def test_parse_db_date_end_of_day():
    """Date string just before midnight."""
    dt = parse_db_date("2026-12-31 23:59:59")
    assert dt == datetime(2026, 12, 31, 23, 59, 59)


def test_parse_db_date_iso_t_with_microseconds():
    """ISO format with T separator and microseconds."""
    dt = parse_db_date("2026-07-21T12:00:00.123456")
    assert dt.tzinfo is None
    assert dt == datetime(2026, 7, 21, 12, 0, 0, 123456)


def test_parse_db_date_iso_t_with_positive_offset_and_microseconds():
    """ISO format with T, offset, and microseconds combined."""
    dt = parse_db_date("2026-07-21T12:00:00.123456+02:00")
    assert dt.tzinfo is None
    assert dt == datetime(2026, 7, 21, 12, 0, 0, 123456)


def test_parse_db_date_invalid_format():
    """Non-date string raises ValueError."""
    with pytest.raises((ValueError, TypeError)):
        parse_db_date("not-a-date")


def test_parse_db_date_incomplete_datetime():
    """Incomplete datetime raises ValueError."""
    with pytest.raises((ValueError, TypeError)):
        parse_db_date("2026-07-21")
