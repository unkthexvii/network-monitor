from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from fastapi import APIRouter, Request, HTTPException
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Global dict of connected clients: {id(q): {"queue": asyncio.Queue, "last_activity": float}}
_clients: dict[int, dict] = {}

# How long without activity before a client is considered dead.
_CLIENT_TIMEOUT_SECONDS = 300  # 5 minutes

# Configurable limits — total global clients and per-IP.
_MAX_TOTAL_CLIENTS = int(os.getenv("MONITOR_SSE_MAX_TOTAL", "50"))
_MAX_PER_IP = int(os.getenv("MONITOR_SSE_MAX_PER_IP", "10"))

async def sse_publisher(event_name: str, data: dict):
    """
    Publish an SSE event to all connected clients.
    Iterates over a snapshot to avoid RuntimeError from concurrent modification.
    """
    payload = json.dumps(data)
    for client_id, client_info in list(_clients.items()):
        try:
            client_info["queue"].put_nowait({"event": event_name, "data": payload})
        except asyncio.QueueFull:
            logger.warning("SSE client queue full, dropping event")

async def _cleanup_stale_clients():
    """Remove clients that haven't had activity within the timeout window."""
    now = time.monotonic()
    stale = [cid for cid, info in list(_clients.items())
             if now - info["last_activity"] > _CLIENT_TIMEOUT_SECONDS]
    for cid in stale:
        client = _clients.pop(cid, None)
        if client:
            logger.info(f"SSE stale client {cid} cleaned up (inactive > {_CLIENT_TIMEOUT_SECONDS}s)")


@router.get("/api/stream")
async def stream(request: Request):
    """
    SSE stream endpoint for live dashboard updates.

    Limits:
      - Per-IP (MONITOR_SSE_MAX_PER_IP, default 10): fairness guard.
      - Total global (MONITOR_SSE_MAX_TOTAL, default 50): prevents fd exhaustion.
    """
    total = len(_clients)
    if total >= _MAX_TOTAL_CLIENTS:
        async def deny_generator():
            yield {"event": "connection_denied",
                   "data": json.dumps({"reason": "server_full", "total_clients": total})}
        return EventSourceResponse(deny_generator())

    ip = request.client.host if request.client else "unknown"
    ip_count = sum(1 for info in _clients.values() if info.get("ip") == ip)
    if ip_count >= _MAX_PER_IP:
        async def deny_generator():
            yield {"event": "connection_denied",
                   "data": json.dumps({"reason": "too_many_connections", "limit": _MAX_PER_IP, "current": ip_count})}
        return EventSourceResponse(deny_generator(), ping=30)

    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
    client_id = id(q)
    _clients[client_id] = {"queue": q, "last_activity": time.monotonic(), "ip": ip}
    logger.info(f"SSE client connected (total: {len(_clients)})")

    async def event_generator():
        try:
            while True:
                try:
                    message = await asyncio.wait_for(q.get(), timeout=120)
                    # Update activity timestamp on received messages
                    if client_id in _clients:
                        _clients[client_id]["last_activity"] = time.monotonic()
                    yield message
                except asyncio.TimeoutError:
                    # No message for 120s — send heartbeat to probe connection
                    # Clean up stale clients periodically
                    await _cleanup_stale_clients()
                    yield {"comment": "heartbeat"}
                    continue
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            _clients.pop(client_id, None)
            logger.info(f"SSE client disconnected (total: {len(_clients)})")

    return EventSourceResponse(event_generator(), ping=30)
