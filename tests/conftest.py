"""Pytest configuration for the Network Monitor test suite."""
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Enable integration tests that require a running server",
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless --run-integration flag is passed."""
    if not config.getoption("--run-integration", default=False):
        skip_integration = pytest.mark.skip(
            reason="Run with --run-integration to enable integration tests"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
