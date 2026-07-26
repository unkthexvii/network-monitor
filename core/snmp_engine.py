from __future__ import annotations
import asyncio
import logging
import json
import time
from datetime import datetime, timezone
from pysnmp.hlapi.asyncio import (
    SnmpEngine, getCmd, CommunityData,
    UdpTransportTarget, ContextData, ObjectType, ObjectIdentity,
    UsmUserData, usmHMACMD5AuthProtocol, usmDESPrivProtocol,
    usmHMACSHAAuthProtocol, usmAesCfb128Protocol
)

from database.session import async_session
from database.models import Device, DeviceStatus

logger = logging.getLogger("SNMPEngine")

# Pre-resolved numeric OIDs — using numeric strings bypasses MIB loading,
# preventing unbounded MibBuilder cache growth over time.
# SNMPv2-MIB numeric equivalents:
SNMP_OID_SYSNAME = "1.3.6.1.2.1.1.5"
SNMP_OID_SYSDESCR = "1.3.6.1.2.1.1.1"
SNMP_OID_SYSUPTIME = "1.3.6.1.2.1.1.3"
SNMP_OID_SYSCONTACT = "1.3.6.1.2.1.1.4"
SNMP_OID_SYSLOCATION = "1.3.6.1.2.1.1.6"

# Base OIDs for all SNMP devices
SNMP_OID_IFNUMBER = "1.3.6.1.2.1.2.1.0"
SNMP_OID_ENTPHYSICAL_SERIALNUM = "1.3.6.1.2.1.47.1.1.1.1.11.1"
SNMP_OID_ENTPHYSICAL_MODELNAME = "1.3.6.1.2.1.47.1.1.1.1.13.1"
SNMP_OID_ENTPHYSICAL_NAME = "1.3.6.1.2.1.47.1.1.1.1.7.1"

# Device-type specific OIDs
SNMP_OID_CISCO_CPU_5MIN = "1.3.6.1.4.1.9.9.109.1.1.1.1.7.1"
SNMP_OID_CISCO_MEM_USED = "1.3.6.1.4.1.9.9.48.1.1.1.5.1"
SNMP_OID_CISCO_MEM_FREE = "1.3.6.1.4.1.9.9.48.1.1.1.6.1"
SNMP_OID_CISCO_TEMP = "1.3.6.1.4.1.9.9.13.1.3.1.3.1"
SNMP_OID_WLC_CLIENT_COUNT = "1.3.6.1.4.1.14179.2.1.1.1.0"
SNMP_OID_WLC_CLIENT_COUNT_ALT = "1.3.6.1.4.1.14179.2.1.1.1.38"
SNMP_OID_WLC_CLIENT_COUNT_ALT2 = "1.3.6.1.4.1.9.9.618.1.8.4.0"
SNMP_OID_WLC_AP_COUNT = "1.3.6.1.4.1.14179.2.1.1.1.19"
SNMP_OID_UPS_BATTERY_STATUS = "1.3.6.1.2.1.33.1.2.1.0"
SNMP_OID_UPS_BATTERY_CHARGE = "1.3.6.1.2.1.33.1.2.4.0"
SNMP_OID_PRINTER_TONER = "1.3.6.1.2.1.43.11.1.1.9.1.1"
SNMP_OID_PRINTER_PAGES = "1.3.6.1.2.1.43.10.2.1.4.1.1"
SNMP_OID_PRINTER_NAME = "1.3.6.1.2.1.43.5.1.1.17.1"
SNMP_OID_SERVER_CPU_LOAD = "1.3.6.1.2.1.25.3.3.1.2.1"
SNMP_OID_SERVER_STORAGE_TOTAL = "1.3.6.1.2.1.25.2.3.1.5.1"
SNMP_OID_SERVER_STORAGE_USED = "1.3.6.1.2.1.25.2.3.1.6.1"


# ── Global Singleton SnmpEngine ──
# PySNMP is designed for engine reuse, not create-destroy. Creating a new
# SnmpEngine every poll cycle loads 5 core MIB modules each time and
# accumulates unbounded state (nextid.Integer bank, LCD caches, MibBuilder).
# A single shared engine avoids this entirely. Periodic reset via
# reset_snmp_engine() properly releases accumulated state using the
# library's documented cleanup APIs:
#   1. transportDispatcher.closeDispatcher() — closes UDP sockets, cancels tasks
#   2. unregisterTransportDispatcher() — unbinds engine callbacks
#   3. getMibBuilder().unloadModules() — frees MibBuilder cache
#   4. cache.clear() — clears user context (MibViewController, LCD)
#
# The asyncio.Lock prevents concurrent reset + poll_cycle races.
# Concurrent getCmd calls on the shared engine are safe — PySNMP creates
# ad-hoc transport sessions internally.

_snmp_engine: SnmpEngine | None = None
_snmp_engine_lock = asyncio.Lock()


async def get_snmp_engine() -> SnmpEngine:
    """Return the global singleton SnmpEngine, creating it lazily if needed."""
    global _snmp_engine
    if _snmp_engine is None:
        _snmp_engine = SnmpEngine()
        logger.info("SNMP engine created (global singleton)")
    return _snmp_engine


async def reset_snmp_engine():
    """Reset the global SnmpEngine using PySNMP's documented cleanup APIs.

    Frees accumulated state (MibBuilder cache, transport sockets, LCD caches).
    Called by the scheduler every 23h to prevent long-term memory growth.
    """
    global _snmp_engine
    old = _snmp_engine
    if old is None:
        return

    async with _snmp_engine_lock:
        # Verify old hasn't been replaced by a concurrent reset
        if _snmp_engine is not old:
            return

        # Step 1: Close transport dispatcher (UDP sockets, asyncio tasks)
        try:
            if old.transportDispatcher is not None:
                old.transportDispatcher.closeDispatcher()
                logger.debug("SNMP transport dispatcher closed")
        except Exception as ex:
            logger.warning(f"SNMP closeDispatcher error (non-fatal): {ex}")

        # Step 2: Unbind transport dispatcher from engine
        try:
            old.unregisterTransportDispatcher()
            logger.debug("SNMP transport dispatcher unregistered")
        except Exception as ex:
            logger.warning(f"SNMP unregisterTransportDispatcher error (non-fatal): {ex}")

        # Step 3: Free MibBuilder cache (OidOrderedDict, MibScalarInstance, PLY tables)
        try:
            old.getMibBuilder().unloadModules()
            logger.debug("SNMP MIB modules unloaded (MibBuilder cache freed)")
        except Exception as ex:
            logger.warning(f"SNMP unloadModules error (non-fatal): {ex}")

        # Step 4: Clear user context (MibViewController, LCD configurator caches)
        try:
            old.cache.clear()
            logger.debug("SNMP engine user context cleared")
        except Exception as ex:
            logger.warning(f"SNMP engine cache.clear error (non-fatal): {ex}")

        del old
        _snmp_engine = SnmpEngine()
        logger.info("SNMP engine reset complete — new engine created")


async def fetch_snmp_data(device_ip, snmp_version, community=None, v3_user=None, v3_auth=None, v3_priv=None, device_type=None, device_name=None, engine=None):
    """
    Fetches sysName, sysDescr, and sysUpTime from the target device.
    Uses the global singleton SnmpEngine (passed by caller or resolved lazily).
    """
    auth_data = None
    if snmp_version == "v2c" and community:
        auth_data = CommunityData(community, mpModel=1)
    elif snmp_version == "v3" and v3_user:
        auth_proto = usmHMACSHAAuthProtocol if v3_auth else None
        priv_proto = usmAesCfb128Protocol if v3_priv else None

        auth_data = UsmUserData(
            userName=v3_user,
            authKey=v3_auth if v3_auth else None,
            privKey=v3_priv if v3_priv else None,
            authProtocol=auth_proto,
            privProtocol=priv_proto
        )
    else:
        logger.warning(f"SNMP SKIP {device_ip}: snmp_version={snmp_version} community={'SET' if community else 'EMPTY'}")
        return None

    try:
        comm_masked = (community[:2] + "***") if community and len(community) > 2 else ("*" * len(community)) if community else "??"
        logger.info(f"SNMP TRY {device_ip} v={snmp_version} comm={comm_masked} name={device_name or '-'}")

        t_start = time.monotonic()
        if engine is None:
            engine = await get_snmp_engine()

        _result = await getCmd(
            engine,
            auth_data,
            UdpTransportTarget((device_ip, 161), timeout=2, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(SNMP_OID_SYSNAME)),
            ObjectType(ObjectIdentity(SNMP_OID_SYSDESCR)),
            ObjectType(ObjectIdentity(SNMP_OID_SYSUPTIME)),
            ObjectType(ObjectIdentity(SNMP_OID_SYSCONTACT)),
            ObjectType(ObjectIdentity(SNMP_OID_SYSLOCATION)),
            lookupMib=False,  # Skip MIB resolution on response
        )
        errorIndication, errorStatus, errorIndex, varBinds = await _result
        t_elapsed = time.monotonic() - t_start

        if errorIndication:
            logger.error(f"SNMP FAIL {device_ip} ({t_elapsed:.1f}s) {errorIndication}")
            return None
        elif errorStatus:
            logger.error(f"SNMP FAIL {device_ip} ({t_elapsed:.1f}s) status={errorStatus.prettyPrint()}")
            return None
        else:
            logger.info(f"SNMP OK {device_ip} ({t_elapsed:.1f}s)")
            result = {}
            for varBind in varBinds:
                oid = varBind[0].prettyPrint()
                val = varBind[1].prettyPrint()
                if oid.startswith(SNMP_OID_SYSNAME + "."):
                    result['sys_name'] = val
                elif oid.startswith(SNMP_OID_SYSCONTACT + "."):
                    result['sys_contact'] = val
                elif oid.startswith(SNMP_OID_SYSLOCATION + "."):
                    result['sys_location'] = val
                elif oid.startswith(SNMP_OID_SYSDESCR + "."):
                    result['sys_descr'] = val
                elif oid.startswith(SNMP_OID_SYSUPTIME + "."):
                    try:
                        ticks = int(varBind[1])
                        seconds = ticks / 100.0
                        m, s = divmod(seconds, 60)
                        h, m = divmod(m, 60)
                        d, h = divmod(h, 24)
                        result['sys_uptime'] = f"{int(d)}d {int(h):02d}:{int(m):02d}:{int(s):02d}"
                    except Exception:
                        result['sys_uptime'] = val

            # ── Base OIDs for ALL SNMP devices ──
            extra_oids = []
            extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_IFNUMBER)))
            extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_ENTPHYSICAL_SERIALNUM)))
            extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_ENTPHYSICAL_MODELNAME)))
            extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_ENTPHYSICAL_NAME)))

            # ── Type-specific OIDs ──
            device_type_clean = (device_type or "").lower()
            is_switch = device_type_clean in ["switch", "router", "gateway", "firewall"]
            is_wlc = device_type_clean in ["wireless controller (wlc)", "controller", "access point"] or (device_name and "WLC" in str(device_name).upper())
            is_ups = device_type_clean == "ups"
            is_printer = device_type_clean == "printer"
            is_server = device_type_clean in ["server", "virtual machine", "hypervisor", "storage/nas", "database"]

            if is_switch:
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_CISCO_CPU_5MIN)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_CISCO_MEM_USED)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_CISCO_MEM_FREE)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_CISCO_TEMP)))

            if is_wlc:
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_WLC_CLIENT_COUNT)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_WLC_CLIENT_COUNT_ALT)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_WLC_CLIENT_COUNT_ALT2)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_WLC_AP_COUNT)))

            if is_ups:
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_UPS_BATTERY_STATUS)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_UPS_BATTERY_CHARGE)))

            if is_printer:
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_PRINTER_TONER)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_PRINTER_PAGES)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_PRINTER_NAME)))

            if is_server:
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_SERVER_CPU_LOAD)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_SERVER_STORAGE_TOTAL)))
                extra_oids.append(ObjectType(ObjectIdentity(SNMP_OID_SERVER_STORAGE_USED)))

            if extra_oids:
                _res2 = await getCmd(
                    engine,
                    auth_data,
                    UdpTransportTarget((device_ip, 161), timeout=2, retries=1),
                    ContextData(),
                    *extra_oids,
                    lookupMib=False,
                )
                e_Ind, e_Stat, e_Idx, e_Binds = await _res2
                if not e_Ind and not e_Stat:
                    custom_data = {}
                    for varBind in e_Binds:
                        oid = varBind[0].prettyPrint()
                        val = varBind[1]
                        cls = val.__class__.__name__
                        if cls not in ('NoSuchObject', 'NoSuchInstance', 'EndOfMibView'):
                            val_str = val.prettyPrint()
                            # ── Base OIDs ──
                            if oid.endswith(SNMP_OID_IFNUMBER):
                                custom_data['Interfaces'] = val_str
                            elif oid.endswith(SNMP_OID_ENTPHYSICAL_SERIALNUM):
                                result['serial_number'] = val_str
                            elif oid.endswith(SNMP_OID_ENTPHYSICAL_MODELNAME):
                                custom_data['Model Name'] = val_str
                            elif oid.endswith(SNMP_OID_ENTPHYSICAL_NAME):
                                custom_data['Chassis Name'] = val_str
                            # ── WLC client count (first match wins) ──
                            elif oid.endswith(SNMP_OID_WLC_CLIENT_COUNT):
                                try: result['client_count'] = int(val_str)
                                except (ValueError, TypeError) as e: logger.debug(f"SNMP parse client_count for {device_ip}: {e}")
                            elif oid.endswith(SNMP_OID_WLC_CLIENT_COUNT_ALT) and 'client_count' not in result:
                                try: result['client_count'] = int(val_str)
                                except (ValueError, TypeError) as e: logger.debug(f"SNMP parse client_count for {device_ip}: {e}")
                            elif oid.endswith(SNMP_OID_WLC_CLIENT_COUNT_ALT2) and 'client_count' not in result:
                                try: result['client_count'] = int(val_str)
                                except (ValueError, TypeError) as e: logger.debug(f"SNMP parse client_count for {device_ip}: {e}")
                            # ── WLC AP count ──
                            elif oid.endswith(SNMP_OID_WLC_AP_COUNT):
                                try: result['ap_count'] = int(val_str)
                                except (ValueError, TypeError) as e: logger.debug(f"SNMP parse ap_count for {device_ip}: {e}")
                            # ── Switch/router (Cisco) ──
                            elif oid.endswith(SNMP_OID_CISCO_CPU_5MIN):
                                custom_data['CPU 5min %'] = val_str
                            elif oid.endswith(SNMP_OID_CISCO_MEM_USED):
                                custom_data['Mem Used'] = val_str
                            elif oid.endswith(SNMP_OID_CISCO_MEM_FREE):
                                custom_data['Mem Free'] = val_str
                            elif oid.endswith(SNMP_OID_CISCO_TEMP):
                                custom_data['Temperature'] = val_str
                            # ── UPS ──
                            elif oid.endswith(SNMP_OID_UPS_BATTERY_STATUS):
                                status_map = {'1': 'Unknown', '2': 'Normal', '3': 'Low', '4': 'Depleted'}
                                custom_data['Battery Status'] = status_map.get(val_str, f"Code {val_str}")
                            elif oid.endswith(SNMP_OID_UPS_BATTERY_CHARGE):
                                custom_data['Battery Level'] = f"{val_str}%"
                            # ── Printer ──
                            elif oid.endswith(SNMP_OID_PRINTER_TONER):
                                custom_data['Toner Level'] = f"{val_str}%"
                            elif oid.endswith(SNMP_OID_PRINTER_PAGES):
                                custom_data['Pages Printed'] = val_str
                            elif oid.endswith(SNMP_OID_PRINTER_NAME):
                                custom_data['Printer Name'] = val_str
                            # ── Server ──
                            elif oid.endswith(SNMP_OID_SERVER_CPU_LOAD):
                                custom_data['CPU Load %'] = f"{val_str}%"
                            elif oid.endswith(SNMP_OID_SERVER_STORAGE_TOTAL):
                                custom_data['Storage Total'] = val_str
                            elif oid.endswith(SNMP_OID_SERVER_STORAGE_USED):
                                custom_data['Storage Used'] = val_str
                        else:
                            logger.debug(f"SNMP OID skipped for {device_ip}: {oid} = {cls}")
                    if custom_data:
                        result['custom_data'] = json.dumps(custom_data)

            return result
    except Exception as e:
        logger.error(f"SNMP Exception for {device_ip}: {e}", exc_info=True)
        return None


from sqlalchemy import select

async def poll_all_devices():
    """
    Runs an SNMP poll against all enabled devices that have SNMP configured.
    Uses the global singleton SnmpEngine (reused across all poll cycles).
    """
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Device).where(
                    Device.enabled == True,
                    Device.snmp_version.in_(["v2c", "v3"])
                )
            )
            devices = result.scalars().all()
            logger.info(f"SNMP POLL START: {len(devices)} device(s), first: {devices[0].ip_address if devices else 'none'}")

            if not devices:
                return

            sem = asyncio.Semaphore(50)
            snmp_engine = await get_snmp_engine()

            async def sem_fetch(*args, **kwargs):
                async with sem:
                    return await fetch_snmp_data(*args, **kwargs)

            tasks = []
            for device in devices:
                tasks.append(
                    sem_fetch(
                        device_ip=device.ip_address,
                        snmp_version=device.snmp_version,
                        community=device.snmp_community,
                        v3_user=device.snmp_v3_user,
                        v3_auth=device.snmp_v3_auth,
                        v3_priv=device.snmp_v3_priv,
                        device_type=device.device_type,
                        device_name=device.name,
                        engine=snmp_engine
                    )
                )

            SNMP_POLL_TIMEOUT_1 = 30.0
            SNMP_POLL_TIMEOUT_2 = 60.0

            done, pending = await asyncio.wait(tasks, timeout=SNMP_POLL_TIMEOUT_1)

            if pending:
                timed_out_ips = [
                    devices[i].ip_address
                    for i, t in enumerate(tasks) if t in pending
                ]
                logger.warning(
                    f"SNMP poll: {len(pending)}/{len(devices)} device(s) timed out "
                    f"after {SNMP_POLL_TIMEOUT_1}s — retrying with "
                    f"{SNMP_POLL_TIMEOUT_2}s timeout. Affected: {timed_out_ips[:10]}"
                )
                retry_done, retry_pending = await asyncio.wait(
                    pending, timeout=SNMP_POLL_TIMEOUT_2
                )
                done = done | retry_done

                if retry_pending:
                    still_pending_ips = [
                        devices[i].ip_address
                        for i, t in enumerate(tasks) if t in retry_pending
                    ]
                    logger.error(
                        f"SNMP poll: {len(retry_pending)} device(s) still unreachable "
                        f"after retry. IPs: {still_pending_ips[:10]}"
                    )
                    for task in retry_pending:
                        task.cancel()
                    await asyncio.gather(*retry_pending, return_exceptions=True)

            results = []
            for task in tasks:
                if task in done:
                    try:
                        results.append(task.result())
                    except asyncio.CancelledError:
                        results.append(None)
                    except Exception:
                        results.append(None)
                else:
                    results.append(None)

            # Fetch all DeviceStatus records in chunked queries to avoid SQLite's 999-variable limit
            device_ids = [d.id for d, r in zip(devices, results) if isinstance(r, dict)]
            if device_ids:
                status_map = {}
                CHUNK_SIZE = 500
                for i in range(0, len(device_ids), CHUNK_SIZE):
                    chunk = device_ids[i:i + CHUNK_SIZE]
                    status_records_result = await db.execute(
                        select(DeviceStatus).where(DeviceStatus.device_id.in_(chunk))
                    )
                    for s in status_records_result.scalars().all():
                        status_map[s.device_id] = s

                for device, data in zip(devices, results):
                    if isinstance(data, dict):
                        status = status_map.get(device.id)
                        if status:
                            if 'sys_name' in data: status.sys_name = data.get('sys_name')
                            if 'sys_contact' in data: status.sys_contact = data.get('sys_contact')
                            if 'sys_location' in data: status.sys_location = data.get('sys_location')
                            if 'sys_descr' in data: status.sys_descr = data.get('sys_descr')
                            if 'sys_uptime' in data: status.sys_uptime = data.get('sys_uptime')
                            if 'client_count' in data: status.client_count = data.get('client_count')
                            if 'ap_count' in data: status.ap_count = data.get('ap_count')
                            if 'serial_number' in data: status.serial_number = data.get('serial_number')
                            if 'custom_data' in data: status.snmp_custom_data = data.get('custom_data')

            await db.commit()
    except asyncio.CancelledError:
        logger.info("SNMP poll cycle cancelled")
        raise
    except Exception as e:
        logger.error(f"Error in SNMP poll cycle: {type(e).__name__}: {e}")
