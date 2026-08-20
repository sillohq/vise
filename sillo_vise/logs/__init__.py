"""
sillo_vise.logs — the server's voice.

uvicorn's logging is switched off entirely rather than reconfigured, and
replaced by three things: a banner at startup, an aligned formatter for
application logs, and one access line per request written from the recorder's
own measurement.
"""

from __future__ import annotations

from .access import AccessLog
from .banner import Banner
from .format import duration, elapsed, number, size, truncate
from .formatter import JSONFormatter, ViseFormatter
from .install import (
    LOG_LEVELS,
    attach_access_log,
    install_logging,
    silence_uvicorn,
)
from .theme import level_style, status_style

__all__ = [
    "LOG_LEVELS",
    "AccessLog",
    "Banner",
    "JSONFormatter",
    "ViseFormatter",
    "attach_access_log",
    "duration",
    "elapsed",
    "install_logging",
    "level_style",
    "number",
    "silence_uvicorn",
    "size",
    "status_style",
    "truncate",
]
