from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# We will import the notify function lazily or register it to avoid circular imports.
_notify_callback = None

def register_notify_callback(cb):
    global _notify_callback
    _notify_callback = cb
