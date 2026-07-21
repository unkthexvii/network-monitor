"""Tests for core/archive_query.py — date parsing."""
from datetime import datetime
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
