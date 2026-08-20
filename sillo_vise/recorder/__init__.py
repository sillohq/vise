"""
sillo_vise.recorder — one recorder, one store, one place credentials are removed.

Watchers build events and call :meth:`Recorder.emit`. Panels read the store.
Nothing else in vise touches either, which is what keeps the retention policy,
the memory ceiling and the redaction rules each decided once.
"""

from __future__ import annotations

from .context import (
    current_job,
    current_request,
    current_route,
    job_scope,
    request_scope,
    set_route,
)
from .events import (
    CacheEvent,
    Event,
    EventKind,
    ExceptionEvent,
    JobEvent,
    LogEvent,
    MailEvent,
    OutgoingEvent,
    QueryEvent,
    RequestEvent,
    ScheduleEvent,
    SignalEvent,
    WebsocketEvent,
    new_id,
)
from .fingerprint import fingerprint_sql, is_n_plus_one, short_sql, where_raised
from .recorder import Recorder
from .redact import PLACEHOLDER, Redactor
from .series import Bucket, Series, SeriesSet
from .store import Store, StoreSubscription

__all__ = [
    "PLACEHOLDER",
    "Bucket",
    "CacheEvent",
    "Event",
    "EventKind",
    "ExceptionEvent",
    "JobEvent",
    "LogEvent",
    "MailEvent",
    "OutgoingEvent",
    "QueryEvent",
    "Recorder",
    "Redactor",
    "RequestEvent",
    "ScheduleEvent",
    "Series",
    "SeriesSet",
    "SignalEvent",
    "Store",
    "StoreSubscription",
    "WebsocketEvent",
    "current_job",
    "current_request",
    "current_route",
    "fingerprint_sql",
    "is_n_plus_one",
    "job_scope",
    "new_id",
    "request_scope",
    "set_route",
    "short_sql",
    "where_raised",
]
