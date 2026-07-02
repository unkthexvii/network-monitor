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

_snmp_engine = None

def _get_engine():
    global _snmp_engine
    if _snmp_engine is None:
        _snmp_engine = SnmpEngine()
    return _snmp_engine


async def fetch_snmp_data(device_ip, snmp_version, community=None, v3_user=None, v3_auth=None, v3_priv=None, device_type=None, device_name=None):
    """
    Fetches sysName, sysDescr, and sysUpTime from the target device.
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
        comm_masked = (community[:2] + "***") if community and len(community) > 2 else "??"
        logger.info(f"SNMP TRY {device_ip} v={snmp_version} comm={comm_masked} name={device_name or '-'}")

        t_start = time.monotonic()
        _result = await getCmd(
            _get_engine(),
            auth_data,
            UdpTransportTarget((device_ip, 161), timeout=2, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysName', 0)),
            ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysDescr', 0)),
            ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysUpTime', 0)),
            ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysContact', 0)),
            ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysLocation', 0))
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
                if 'sysName' in oid:
                    result['sys_name'] = val
                elif 'sysContact' in oid:
                    result['sys_contact'] = val
                elif 'sysLocation' in oid:
                    result['sys_location'] = val
                elif 'sysDescr' in oid:
                    result['sys_descr'] = val
                elif 'sysUpTime' in oid:
                    # Convert timeticks to string format roughly (days, hh:mm:ss)
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
            extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.2.1.0')))    # ifNumber
            extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.47.1.1.1.1.11.1'))) # entPhysicalSerialNum
            extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.47.1.1.1.1.13.1'))) # entPhysicalModelName
            extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.47.1.1.1.1.7.1')))  # entPhysicalName

            # ── Type-specific OIDs ──
            device_type_clean = (device_type or "").lower()
            is_switch = device_type_clean in ["switch", "router", "gateway", "firewall"]
            is_wlc = device_type_clean in ["wireless controller (wlc)", "controller", "access point"] or (device_name and "WLC" in str(device_name).upper())
            is_ups = device_type_clean == "ups"
            is_printer = device_type_clean == "printer"
            is_server = device_type_clean in ["server", "virtual machine", "hypervisor", "storage/nas", "database"]

            if is_switch:
                # Cisco CPU/memory/temperature
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.4.1.9.9.109.1.1.1.1.7.1'))) # cpmCPUTotal5minRev
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.4.1.9.9.48.1.1.1.5.1')))    # ciscoMemoryPoolUsed
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.4.1.9.9.48.1.1.1.6.1')))    # ciscoMemoryPoolFree
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.4.1.9.9.13.1.3.1.3.1')))    # ciscoEnvMonTemperatureStatusValue

            if is_wlc:
                # Client count (try 3 OIDs in order)
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.4.1.14179.2.1.1.1.0')))    # bsnMobileStationCount
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.4.1.14179.2.1.1.1.38')))   # bsnAPIfLoadNumberOfUsers
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.4.1.9.9.618.1.8.4.0')))     # cLWlanTotMobileStation
                # AP count
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.4.1.14179.2.1.1.1.19')))   # bsnAPCount

            if is_ups:
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.33.1.2.1.0')))          # upsBatteryStatus
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.33.1.2.4.0')))          # upsEstimatedChargeRemaining

            if is_printer:
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.43.11.1.1.9.1.1')))     # prtMarkerSuppliesLevel
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.43.10.2.1.4.1.1')))     # prtMarkerLifeCount (pages printed)
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.43.5.1.1.17.1')))       # prtGeneralPrinterName

            if is_server:
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.25.3.3.1.2.1')))         # hrProcessorLoad
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.25.2.3.1.5.1')))         # hrStorageSize (total)
                extra_oids.append(ObjectType(ObjectIdentity('1.3.6.1.2.1.25.2.3.1.6.1')))         # hrStorageUsed (used)
                
            if extra_oids:
                _res2 = await getCmd(
                    _get_engine(),
                    auth_data,
                    UdpTransportTarget((device_ip, 161), timeout=2, retries=1),
                    ContextData(),
                    *extra_oids
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
                            if '1.1.4.0' in oid:          # sysContact
                                custom_data['Contact'] = val_str
                            elif '1.1.6.0' in oid:        # sysLocation
                                custom_data['Location'] = val_str
                            elif '1.2.1.0' in oid and '2.1' in oid and '1.3.6.1.2.1.2.1' in oid:  # ifNumber
                                custom_data['Interfaces'] = val_str
                            elif '47.1.1.1.1.11.1' in oid:  # entPhysicalSerialNum
                                result['serial_number'] = val_str
                            elif '47.1.1.1.1.13.1' in oid:  # entPhysicalModelName
                                custom_data['Model Name'] = val_str
                            elif '47.1.1.1.1.7.1' in oid:   # entPhysicalName
                                custom_data['Chassis Name'] = val_str
                            # ── WLC client count (first match wins, skip others) ──
                            elif '14179.2.1.1.1.0' in oid:
                                try: result['client_count'] = int(val_str)
                                except: pass
                            elif '14179.2.1.1.1.38' in oid and 'client_count' not in result:
                                try: result['client_count'] = int(val_str)
                                except: pass
                            elif '9.9.618.1.8.4.0' in oid and 'client_count' not in result:
                                try: result['client_count'] = int(val_str)
                                except: pass
                            # ── WLC AP count ──
                            elif '14179.2.1.1.1.19' in oid:
                                try: result['ap_count'] = int(val_str)
                                except: pass
                            # ── Switch/router (Cisco) ──
                            elif '9.9.109.1.1.1.1.7.1' in oid:
                                custom_data['CPU 5min %'] = val_str
                            elif '9.9.48.1.1.1.5.1' in oid:
                                custom_data['Mem Used'] = val_str
                            elif '9.9.48.1.1.1.6.1' in oid:
                                custom_data['Mem Free'] = val_str
                            elif '9.9.13.1.3.1.3.1' in oid:
                                custom_data['Temperature'] = val_str
                            # ── UPS ──
                            elif '33.1.2.1.0' in oid:
                                status_map = {'1': 'Unknown', '2': 'Normal', '3': 'Low', '4': 'Depleted'}
                                custom_data['Battery Status'] = status_map.get(val_str, f"Code {val_str}")
                            elif '33.1.2.4.0' in oid:
                                custom_data['Battery Level'] = f"{val_str}%"
                            # ── Printer ──
                            elif '43.11.1.1.9.1.1' in oid:
                                custom_data['Toner Level'] = f"{val_str}%"
                            elif '43.10.2.1.4.1.1' in oid:
                                custom_data['Pages Printed'] = val_str
                            elif '43.5.1.1.17.1' in oid:
                                custom_data['Printer Name'] = val_str
                            # ── Server ──
                            elif '25.3.3.1.2.1' in oid:
                                custom_data['CPU Load %'] = f"{val_str}%"
                            elif '25.2.3.1.5.1' in oid:
                                custom_data['Storage Total'] = val_str
                            elif '25.2.3.1.6.1' in oid:
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

            sem = asyncio.Semaphore(50)

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
                        device_name=device.name
                    )
                )
                
            results = await asyncio.gather(*tasks, return_exceptions=True)

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
    except Exception as e:
        logger.error(f"Error in SNMP poll cycle: {e}")
