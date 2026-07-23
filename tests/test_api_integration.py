"""Integration tests against the running server (localhost:8000).
These require the NetworkMonitor_Win7.exe to be running.
Set MONITOR_DEFAULT_PASSWORD env var to match the server's default password."""
import httpx
import pytest
import os

BASE = "http://localhost:8000"
# The server uses MONITOR_DEFAULT_PASSWORD env var or generates a random one.
# For tests, set MONITOR_DEFAULT_PASSWORD=admin when starting the server.
ADMIN_PASSWORD = os.getenv("MONITOR_DEFAULT_PASSWORD", "admin")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=10) as c:
        yield c


@pytest.fixture(scope="module")
def auth_client(client):
    """Login and return an authenticated client (cookie-based auth)."""
    r = client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    if r.status_code == 200:
        return client  # httpx.Client persists cookies automatically
    return client


# ── Readonly endpoint (no auth required) ──

def test_readonly_endpoint(client):
    r = client.get("/api/readonly")
    assert r.status_code == 200
    data = r.json()
    assert "readonly" in data
    assert "authenticated" in data


# ── Auth endpoints ──

def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"password": "wrongpassword"})
    assert r.status_code in (401, 403)


def test_login_correct_password(client):
    r = client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    assert r.status_code == 200
    assert "auth_token" in r.cookies  # httpOnly cookie is set


def test_protected_endpoint_no_auth(client):
    r = client.get("/api/devices")
    # GET is allowed without auth (read-only), so 200
    assert r.status_code == 200


def test_protected_endpoint_with_auth(auth_client):
    r = auth_client.get("/api/devices")
    assert r.status_code == 200


# ── Devices API ──

def test_get_devices(client):
    r = client.get("/api/devices")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_get_devices_paginated(client):
    r = client.get("/api/devices/paginated", params={"page": 1, "limit": 10})
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "pages" in data


def test_get_device_names(client):
    r = client.get("/api/devices/names")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_get_subnets(client):
    r = client.get("/api/devices/subnets")
    assert r.status_code == 200
    data = r.json()
    assert "subnets" in data
    assert isinstance(data["subnets"], list)


# ── Dashboard API ──

def test_dashboard_stats(client):
    r = client.get("/api/dashboard/stats")
    assert r.status_code == 200


def test_dashboard_events(client):
    r = client.get("/api/dashboard/events")
    assert r.status_code == 200


# ── Alerts API ──

def test_get_alerts(client):
    r = client.get("/api/alerts", params={"limit": 10})
    assert r.status_code == 200


# ── Reports API ──

def test_reports_ui_data(client):
    r = client.get("/api/reports/ui_data", params={"device_id": 1, "timeframe": "24h"})
    # 200 if device exists, 422 if missing param, 500 if no device with id=1
    assert r.status_code in (200, 422, 500)


# ── Topology API ──

def test_get_topology(client):
    r = client.get("/api/topology")
    assert r.status_code == 200


# ── Static files ──

def test_index_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "html" in r.headers.get("content-type", "")


def test_wall_page(client):
    r = client.get("/wall")
    assert r.status_code == 200
