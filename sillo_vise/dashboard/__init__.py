"""
sillo_vise.dashboard — Foreman, served alongside the application.

One raw ASGI middleware answering under its own prefix, gated by
:class:`~sillo_vise.dashboard.security.AccessGate`, serving a built interface out
of the package and a JSON API out of the recorder's store.
"""

from __future__ import annotations

from .api import DashboardAPI
from .app import Dashboard
from .assets import Assets
from .security import ACCESS_MODES, AccessGate
from .stream import EventStream, sse

__all__ = [
    "ACCESS_MODES",
    "AccessGate",
    "Assets",
    "Dashboard",
    "DashboardAPI",
    "EventStream",
    "sse",
]
