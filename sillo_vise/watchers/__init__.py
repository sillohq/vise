"""
sillo_vise.watchers — one watcher per concern, over hooks the framework already has.

A watcher builds events and hands them to the recorder. It does not own storage,
does not decide what is a secret and does not render anything. Before it is
attached it has to prove it can observe the running application, and a watcher
that cannot leaves its panel out with a reason rather than showing an empty one.
"""

from __future__ import annotations

from .base import Availability, Watcher, available, unavailable
from .cache import CacheWatcher
from .exceptions import ExceptionWatcher
from .logs import LogWatcher, RecordingHandler
from .mail import MailWatcher
from .outgoing import OutgoingWatcher
from .queries import QueryWatcher
from .queues import QueueMiddleware, QueueWatcher
from .realtime import RealtimeWatcher, WebsocketRecorder
from .registry import WatcherRegistry, WatcherState, default_watchers
from .requests import RequestRecorder, RequestWatcher
from .schedules import ScheduleWatcher
from .workers import WorkerWatcher

__all__ = [
    "Availability",
    "CacheWatcher",
    "ExceptionWatcher",
    "LogWatcher",
    "MailWatcher",
    "OutgoingWatcher",
    "QueryWatcher",
    "QueueMiddleware",
    "QueueWatcher",
    "RealtimeWatcher",
    "RecordingHandler",
    "RequestRecorder",
    "RequestWatcher",
    "ScheduleWatcher",
    "WatcherRegistry",
    "WatcherState",
    "WebsocketRecorder",
    "Watcher",
    "WorkerWatcher",
    "available",
    "default_watchers",
    "unavailable",
]
