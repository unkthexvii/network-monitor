from __future__ import annotations
import asyncio
from typing import List, Dict, Any
from icmplib import async_multiping
from core.config import PING_COUNT, PING_INTERVAL, PING_TIMEOUT
import logging

logger = logging.getLogger(__name__)

async def ping_devices(ips: List[str], count: int = PING_COUNT, interval: float = PING_INTERVAL, timeout: float = PING_TIMEOUT) -> List[Dict[str, Any]]:
    """
    Asynchronously pings a list of IP addresses.
    Returns a list of dicts with ip, status, latency, packet_loss.
    Note: Requires Administrator privileges on Windows.
    """
    if not ips:
        return []

    # Windows select() struggles with > 100 raw sockets concurrently. 
    # Batching them in groups of 50 drastically reduces CPU usage and context switching.
    responses = await async_multiping(
        ips,
        count=count,
        interval=interval,
        timeout=timeout,
        concurrent_tasks=min(50, len(ips)),
        privileged=True # Raw sockets require privileged=True on Windows/Linux
    )
    
    results = []
    for host in responses:
        results.append({
            "ip_address": host.address,
            "status": "ONLINE" if host.is_alive else "OFFLINE",
            "latency_ms": host.avg_rtt,
            "packet_loss": host.packet_loss,
        })
    return results
