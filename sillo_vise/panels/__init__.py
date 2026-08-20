"""
sillo_vise.panels — fourteen screens, drawn from one store.

A panel reads and renders. It never collects, and it never exists unless the
watcher that feeds it is collecting — so an application with no queue has no
Queues panel, rather than a Queues panel full of zeroes.
"""

from __future__ import annotations

from .base import Panel, PanelContext, Rendered, columns, state_column, table
from .diagnose import ExceptionsPanel, LogsPanel, MailPanel, RealtimePanel
from .monitor import (
    CachePanel,
    OutgoingPanel,
    OverviewPanel,
    QueriesPanel,
    RequestsPanel,
)
from .registry import GROUPS, PanelRegistry, all_panels
from .tiles import (
    TONE_BAD,
    TONE_GOOD,
    TONE_INFO,
    TONE_MUTED,
    TONE_WARN,
    tile,
)
from .tools import ConfigPanel, RoutesPanel
from .work import QueuesPanel, SchedulesPanel, WorkersPanel

__all__ = [
    "GROUPS",
    "TONE_BAD",
    "TONE_GOOD",
    "TONE_INFO",
    "TONE_MUTED",
    "TONE_WARN",
    "CachePanel",
    "ConfigPanel",
    "ExceptionsPanel",
    "LogsPanel",
    "MailPanel",
    "OutgoingPanel",
    "OverviewPanel",
    "Panel",
    "PanelContext",
    "PanelRegistry",
    "QueriesPanel",
    "QueuesPanel",
    "RealtimePanel",
    "Rendered",
    "RequestsPanel",
    "RoutesPanel",
    "SchedulesPanel",
    "WorkersPanel",
    "all_panels",
    "columns",
    "state_column",
    "table",
    "tile",
]
