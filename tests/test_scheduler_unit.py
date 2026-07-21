"""Unit tests for core/scheduler.py — pure logic, no DB/network."""
import pytest
from core.scheduler import _handle_task_exception


class FakeTask:
    """Minimal asyncio.Task mock for testing _handle_task_exception."""
    def __init__(self, exc=None):
        self._exc = exc

    def exception(self):
        if self._exc is None:
            raise asyncio.CancelledError()
        return self._exc


import asyncio


def test_handle_task_exception_no_error(caplog):
    task = FakeTask(exc=None)
    # When exception() raises CancelledError, the handler should pass silently
    _handle_task_exception(task)
    # No crash = pass


def test_handle_task_exception_with_error(caplog):
    task = FakeTask(exc=ValueError("boom"))
    _handle_task_exception(task)
    assert any("boom" in record.message for record in caplog.records)


def test_handle_task_exception_cancelled(caplog):
    import asyncio
    task = FakeTask(exc=None)
    # exception() raises CancelledError — handler should catch and pass
    _handle_task_exception(task)
