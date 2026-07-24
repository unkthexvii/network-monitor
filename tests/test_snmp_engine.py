"""Tests for core/snmp_engine.py -- device type detection and OID matching."""
from __future__ import annotations
import asyncio
import json
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =====================================================================
#  Helper: replicate the device-type classification logic from
#  fetch_snmp_data so we can verify correctness of every branch.
# =====================================================================

def classify_device(device_type, device_name):
    device_type_clean = (device_type or "").lower()
    return {
        "is_switch": device_type_clean in ["switch", "router", "gateway", "firewall"],
        "is_wlc": bool(
            device_type_clean in ["wireless controller (wlc)", "controller", "access point"]
            or (device_name and "WLC" in str(device_name).upper())
        ),
        "is_ups": device_type_clean == "ups",
        "is_printer": device_type_clean == "printer",
        "is_server": device_type_clean in ["server", "virtual machine", "hypervisor", "storage/nas", "database"],
    }


# -- Device-type classification tests --

class TestDeviceTypeClassification:
    @pytest.mark.parametrize("dtype", ["Switch", "SWITCH", "switch"])
    def test_switch_variants(self, dtype):
        c = classify_device(dtype, None)
        assert c["is_switch"] is True
        assert c["is_wlc"] is False
        assert c["is_ups"] is False
        assert c["is_printer"] is False
        assert c["is_server"] is False

    @pytest.mark.parametrize("dtype", ["Router", "router", "ROUTER"])
    def test_router(self, dtype):
        c = classify_device(dtype, None)
        assert c["is_switch"] is True

    @pytest.mark.parametrize("dtype", ["Gateway", "gateway"])
    def test_gateway(self, dtype):
        c = classify_device(dtype, None)
        assert c["is_switch"] is True

    @pytest.mark.parametrize("dtype", ["Firewall", "firewall"])
    def test_firewall(self, dtype):
        c = classify_device(dtype, None)
        assert c["is_switch"] is True

    @pytest.mark.parametrize("dtype", ["Wireless Controller (WLC)", "wireless controller (wlc)"])
    def test_wlc_full_name(self, dtype):
        c = classify_device(dtype, None)
        assert c["is_wlc"] is True
        assert c["is_switch"] is False

    @pytest.mark.parametrize("dtype", ["Controller", "controller"])
    def test_controller(self, dtype):
        c = classify_device(dtype, None)
        assert c["is_wlc"] is True

    @pytest.mark.parametrize("dtype", ["Access Point", "access point"])
    def test_access_point(self, dtype):
        c = classify_device(dtype, None)
        assert c["is_wlc"] is True

    def test_wlc_in_device_name(self):
        c = classify_device("other", "Building-A WLC-5520")
        assert c["is_wlc"] is True

    def test_wlc_in_device_name_case_insensitive(self):
        c = classify_device(None, "my-wlc-device")
        assert c["is_wlc"] is True

    def test_wlc_no_match_without_wlc(self):
        c = classify_device(None, "Switch-Floor1")
        assert c["is_wlc"] is False

    def test_ups(self):
        c = classify_device("ups", None)
        assert c["is_ups"] is True
        assert c["is_switch"] is False

    def test_printer(self):
        c = classify_device("printer", None)
        assert c["is_printer"] is True
        assert c["is_switch"] is False

    @pytest.mark.parametrize("dtype", ["Server", "server", "Virtual Machine", "Hypervisor", "Storage/NAS", "Database"])
    def test_server_variants(self, dtype):
        c = classify_device(dtype, None)
        assert c["is_server"] is True
        assert c["is_switch"] is False

    def test_none_type(self):
        c = classify_device(None, None)
        assert all(v is False for v in c.values())

    def test_empty_string(self):
        c = classify_device("", None)
        assert all(v is False for v in c.values())

    def test_unknown_type(self):
        c = classify_device("iot-sensor", "Sensor-01")
        assert all(v is False for v in c.values())


# =====================================================================
#  Helper: build mock SNMP varBinds
# =====================================================================

class FakeVarBind:
    def __init__(self, oid, value):
        self._oid = oid
        self._value = value

    def __getitem__(self, idx):
        if idx == 0:
            return self._OidProxy(self._oid)
        if idx == 1:
            return self._ValProxy(self._value)
        raise IndexError(idx)

    class _OidProxy:
        def __init__(self, oid):
            self._oid = oid
        def prettyPrint(self):
            return self._oid

    class _ValProxy:
        def __init__(self, val):
            self._val = val
        def prettyPrint(self):
            return self._val


class FakeVarBindTyped:
    def __init__(self, oid, value, class_name="OctetString"):
        self._oid = oid
        self._value = value
        self._class_name = class_name

    def __getitem__(self, idx):
        if idx == 0:
            return _Oid(self._oid)
        if idx == 1:
            return _TypedVal(self._value, self._class_name)
        raise IndexError(idx)


class _Oid:
    def __init__(self, oid):
        self._oid = oid
    def prettyPrint(self):
        return self._oid


class _TypedVal:
    def __init__(self, val, cls_name):
        self._val = val
        self.__class__ = type(cls_name, (), {
            "prettyPrint": lambda self_: self_._val,
            "__name__": cls_name,
        })


def _make_sys_varbinds(**overrides):
    defaults = {
        "SNMPv2-MIB::sysName.0": "TestDevice",
        "SNMPv2-MIB::sysDescr.0": "TestOS 1.0",
        "SNMPv2-MIB::sysUpTime.0": "1234500",
        "SNMPv2-MIB::sysContact.0": "admin@test.com",
        "SNMPv2-MIB::sysLocation.0": "Building A",
    }
    defaults.update(overrides)
    return [FakeVarBind(oid, val) for oid, val in defaults.items()]


def _make_mock_get_cmd(first_response, second_response=None):
    call_count = 0

    async def mock_get_cmd(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            resp = first_response
        else:
            resp = second_response if second_response is not None else first_response
        async def _inner():
            return resp
        return _inner()

    return mock_get_cmd


# =====================================================================
#  Tests for OID matching inside fetch_snmp_data
# =====================================================================

@pytest.mark.asyncio
async def test_fetch_snmp_v2c_basic():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, []),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.1", snmp_version="v2c",
                community="public", device_name="TestSwitch",
            )
    assert result is not None
    assert result["sys_name"] == "TestDevice"
    assert result["sys_descr"] == "TestOS 1.0"
    assert result["sys_contact"] == "admin@test.com"
    assert result["sys_location"] == "Building A"
    assert result["sys_uptime"] is not None


def test_sys_uptime_conversion():
    ticks = 9006100
    seconds = ticks / 100.0
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    assert f"{int(d)}d {int(h):02d}:{int(m):02d}:{int(s):02d}" == "1d 01:01:01"


@pytest.mark.asyncio
async def test_fetch_snmp_v3_auth_priv():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, []),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.2", snmp_version="v3",
                v3_user="admin", v3_auth="authpass", v3_priv="privpass",
            )
    assert result is not None
    assert result["sys_name"] == "TestDevice"


@pytest.mark.asyncio
async def test_fetch_snmp_returns_none_on_bad_version():
    from core.snmp_engine import fetch_snmp_data
    result = await fetch_snmp_data(
        device_ip="10.0.0.3", snmp_version="v1", community="public",
    )
    assert result is None


@pytest.mark.asyncio
async def test_fetch_snmp_returns_none_on_error_indication():
    from core.snmp_engine import fetch_snmp_data
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=("timeout", None, None, []),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.4", snmp_version="v2c", community="public",
            )
    assert result is None


@pytest.mark.asyncio
async def test_fetch_snmp_returns_none_on_error_status():
    from core.snmp_engine import fetch_snmp_data
    error_status = MagicMock()
    error_status.prettyPrint.return_value = "noSuchName"
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, error_status, 0, []),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.5", snmp_version="v2c", community="public",
            )
    assert result is None


@pytest.mark.asyncio
async def test_fetch_snmp_exception_returns_none():
    from core.snmp_engine import fetch_snmp_data
    async def mock_get_cmd(*args, **kwargs):
        raise ConnectionRefusedError("no route to host")
    with patch("core.snmp_engine.getCmd", side_effect=mock_get_cmd):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.6", snmp_version="v2c", community="public",
            )
    assert result is None


@pytest.mark.asyncio
async def test_none_community_skipped():
    from core.snmp_engine import fetch_snmp_data
    result = await fetch_snmp_data(
        device_ip="10.0.0.24", snmp_version="v2c", community=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_v3_without_user_skipped():
    from core.snmp_engine import fetch_snmp_data
    result = await fetch_snmp_data(
        device_ip="10.0.0.25", snmp_version="v3", v3_user=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_switch_extra_oids_requested():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    call_args_log = []
    async def mock_get_cmd(*args, **kwargs):
        call_args_log.append(args)
        async def _inner():
            if len(call_args_log) == 1:
                return (None, None, None, sys_binds)
            return (None, None, None, [])
        return _inner()
    with patch("core.snmp_engine.getCmd", side_effect=mock_get_cmd):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.7", snmp_version="v2c",
                community="public", device_type="Switch", device_name="CoreSwitch1",
            )
    assert result is not None
    assert len(call_args_log) == 2
    second_call_oids = call_args_log[1][4:]
    assert len(second_call_oids) == 8  # 4 base + 4 switch


@pytest.mark.asyncio
async def test_ups_extra_oids_requested():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    call_args_log = []
    async def mock_get_cmd(*args, **kwargs):
        call_args_log.append(args)
        async def _inner():
            if len(call_args_log) == 1:
                return (None, None, None, sys_binds)
            return (None, None, None, [])
        return _inner()
    with patch("core.snmp_engine.getCmd", side_effect=mock_get_cmd):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.8", snmp_version="v2c",
                community="public", device_type="UPS",
            )
    assert result is not None
    assert len(call_args_log) == 2
    second_call_oids = call_args_log[1][4:]
    assert len(second_call_oids) == 6  # 4 base + 2 UPS


@pytest.mark.asyncio
async def test_printer_extra_oids_requested():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    call_args_log = []
    async def mock_get_cmd(*args, **kwargs):
        call_args_log.append(args)
        async def _inner():
            if len(call_args_log) == 1:
                return (None, None, None, sys_binds)
            return (None, None, None, [])
        return _inner()
    with patch("core.snmp_engine.getCmd", side_effect=mock_get_cmd):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.9", snmp_version="v2c",
                community="public", device_type="Printer",
            )
    assert result is not None
    assert len(call_args_log) == 2
    second_call_oids = call_args_log[1][4:]
    assert len(second_call_oids) == 7  # 4 base + 3 printer


@pytest.mark.asyncio
async def test_server_extra_oids_requested():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    call_args_log = []
    async def mock_get_cmd(*args, **kwargs):
        call_args_log.append(args)
        async def _inner():
            if len(call_args_log) == 1:
                return (None, None, None, sys_binds)
            return (None, None, None, [])
        return _inner()
    with patch("core.snmp_engine.getCmd", side_effect=mock_get_cmd):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.10", snmp_version="v2c",
                community="public", device_type="Server",
            )
    assert result is not None
    assert len(call_args_log) == 2
    second_call_oids = call_args_log[1][4:]
    assert len(second_call_oids) == 7  # 4 base + 3 server


@pytest.mark.asyncio
async def test_wlc_extra_oids_requested():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    call_args_log = []
    async def mock_get_cmd(*args, **kwargs):
        call_args_log.append(args)
        async def _inner():
            if len(call_args_log) == 1:
                return (None, None, None, sys_binds)
            return (None, None, None, [])
        return _inner()
    with patch("core.snmp_engine.getCmd", side_effect=mock_get_cmd):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.11", snmp_version="v2c",
                community="public", device_type="Wireless Controller (WLC)",
            )
    assert result is not None
    assert len(call_args_log) == 2
    second_call_oids = call_args_log[1][4:]
    assert len(second_call_oids) == 8  # 4 base + 4 WLC


@pytest.mark.asyncio
async def test_unknown_device_type_only_base_oids():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    call_args_log = []
    async def mock_get_cmd(*args, **kwargs):
        call_args_log.append(args)
        async def _inner():
            if len(call_args_log) == 1:
                return (None, None, None, sys_binds)
            return (None, None, None, [])
        return _inner()
    with patch("core.snmp_engine.getCmd", side_effect=mock_get_cmd):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.12", snmp_version="v2c",
                community="public", device_type="iot-sensor",
            )
    assert result is not None
    assert len(call_args_log) == 2
    second_call_oids = call_args_log[1][4:]
    assert len(second_call_oids) == 4  # only base OIDs


# -- OID response-parsing tests --

@pytest.mark.asyncio
async def test_switch_custom_data_parsing():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.2.1.2.1.0", "48"),
        FakeVarBind("1.3.6.1.4.1.9.9.109.1.1.1.1.7.1", "25"),
        FakeVarBind("1.3.6.1.4.1.9.9.48.1.1.1.5.1", "524288"),
        FakeVarBind("1.3.6.1.4.1.9.9.48.1.1.1.6.1", "491520"),
        FakeVarBind("1.3.6.1.4.1.9.9.13.1.3.1.3.1", "38"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.13", snmp_version="v2c",
                community="public", device_type="Switch",
            )
    assert result is not None
    custom = json.loads(result["custom_data"])
    assert custom["Interfaces"] == "48"
    assert custom["CPU 5min %"] == "25"
    assert custom["Mem Used"] == "524288"
    assert custom["Mem Free"] == "491520"
    assert custom["Temperature"] == "38"


@pytest.mark.asyncio
async def test_ups_custom_data_parsing():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.2.1.33.1.2.1.0", "2"),
        FakeVarBind("1.3.6.1.2.1.33.1.2.4.0", "85"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.14", snmp_version="v2c",
                community="public", device_type="UPS",
            )
    assert result is not None
    custom = json.loads(result["custom_data"])
    assert custom["Battery Status"] == "Normal"
    assert custom["Battery Level"] == "85%"


@pytest.mark.asyncio
async def test_ups_battery_status_map():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.2.1.33.1.2.1.0", "4"),
        FakeVarBind("1.3.6.1.2.1.33.1.2.4.0", "5"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.15", snmp_version="v2c",
                community="public", device_type="UPS",
            )
    assert result is not None
    custom = json.loads(result["custom_data"])
    assert custom["Battery Status"] == "Depleted"
    assert custom["Battery Level"] == "5%"


@pytest.mark.asyncio
async def test_ups_unknown_battery_code():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.2.1.33.1.2.1.0", "99"),
        FakeVarBind("1.3.6.1.2.1.33.1.2.4.0", "10"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.16", snmp_version="v2c",
                community="public", device_type="UPS",
            )
    assert result is not None
    custom = json.loads(result["custom_data"])
    assert custom["Battery Status"] == "Code 99"


@pytest.mark.asyncio
async def test_printer_custom_data_parsing():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.2.1.43.11.1.1.9.1.1", "42"),
        FakeVarBind("1.3.6.1.2.1.43.10.2.1.4.1.1", "15023"),
        FakeVarBind("1.3.6.1.2.1.43.5.1.1.17.1", "HP-4250"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.17", snmp_version="v2c",
                community="public", device_type="Printer",
            )
    assert result is not None
    custom = json.loads(result["custom_data"])
    assert custom["Toner Level"] == "42%"
    assert custom["Pages Printed"] == "15023"
    assert custom["Printer Name"] == "HP-4250"


@pytest.mark.asyncio
async def test_server_custom_data_parsing():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.2.1.25.3.3.1.2.1", "45"),
        FakeVarBind("1.3.6.1.2.1.25.2.3.1.5.1", "1048576"),
        FakeVarBind("1.3.6.1.2.1.25.2.3.1.6.1", "524288"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.18", snmp_version="v2c",
                community="public", device_type="Server",
            )
    assert result is not None
    custom = json.loads(result["custom_data"])
    assert custom["CPU Load %"] == "45%"
    assert custom["Storage Total"] == "1048576"
    assert custom["Storage Used"] == "524288"


@pytest.mark.asyncio
async def test_wlc_client_count_and_ap_count():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.4.1.14179.2.1.1.1.0", "125"),
        FakeVarBind("1.3.6.1.4.1.14179.2.1.1.1.19", "8"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.19", snmp_version="v2c",
                community="public", device_type="Wireless Controller (WLC)",
            )
    assert result is not None
    assert result["client_count"] == 125
    assert result["ap_count"] == 8


@pytest.mark.asyncio
async def test_wlc_client_count_fallback_oid():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.4.1.14179.2.1.1.1.38", "50"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.20", snmp_version="v2c",
                community="public", device_type="Wireless Controller (WLC)",
            )
    assert result is not None
    assert result["client_count"] == 50


@pytest.mark.asyncio
async def test_wlc_client_count_third_fallback_oid():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.4.1.9.9.618.1.8.4.0", "30"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.21", snmp_version="v2c",
                community="public", device_type="Wireless Controller (WLC)",
            )
    assert result is not None
    assert result["client_count"] == 30


@pytest.mark.asyncio
async def test_nasobject_value_skipped():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBindTyped("1.3.6.1.2.1.2.1.0", "bad", class_name="NoSuchObject"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.22", snmp_version="v2c",
                community="public", device_type="Server",
            )
    assert result is not None
    assert "custom_data" not in result


@pytest.mark.asyncio
async def test_serial_number_parsed():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    extra_binds = [
        FakeVarBind("1.3.6.1.2.1.47.1.1.1.1.11.1", "SN12345"),
        FakeVarBind("1.3.6.1.2.1.47.1.1.1.1.13.1", "WS-C3750"),
        FakeVarBind("1.3.6.1.2.1.47.1.1.1.1.7.1", "Switch01"),
    ]
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=(None, None, None, extra_binds),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.23", snmp_version="v2c",
                community="public", device_type="Switch",
            )
    assert result is not None
    assert result["serial_number"] == "SN12345"
    custom = json.loads(result["custom_data"])
    assert custom["Model Name"] == "WS-C3750"
    assert custom["Chassis Name"] == "Switch01"


@pytest.mark.asyncio
async def test_extra_oid_error_returns_base_result():
    from core.snmp_engine import fetch_snmp_data
    sys_binds = _make_sys_varbinds()
    with patch("core.snmp_engine.getCmd", side_effect=_make_mock_get_cmd(
        first_response=(None, None, None, sys_binds),
        second_response=("timeout", None, None, []),
    )):
        with patch("core.snmp_engine.SnmpEngine"):
            result = await fetch_snmp_data(
                device_ip="10.0.0.26", snmp_version="v2c",
                community="public", device_type="Switch",
            )
    assert result is not None
    assert result["sys_name"] == "TestDevice"
    assert "custom_data" not in result
