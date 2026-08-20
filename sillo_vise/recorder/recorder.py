"""
sillo_vise.recorder.recorder — the one recorder every watcher writes to.

Fourteen panels, one recorder. Watchers do not touch the store, do not decide
retention and do not decide what is a secret; they build an event and call
:meth:`Recorder.emit`, and the recorder redacts it, stamps it with the request
in flight, files it and updates the series.

That is also what keeps "disabled means compiled out" honest. When the recorder
is off, nothing constructs it, no middleware is wrapped around the application
and no watcher is attached — so there is no branch on the hot path to skip.
:attr:`Recorder.enabled` exists for the case where a running recorder is paused
from the interface, which is a different thing from one that was never started.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ..config import RecorderConfig
from .context import current_job, current_request, current_route
from .events import (
    CacheEvent,
    Event,
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
)
from .fingerprint import fingerprint_sql, where_raised
from .redact import Redactor
from .store import Store

__all__ = ["Recorder"]


class Recorder:
    """Collects what the application does, redacting on the way in.

    Attributes:
        config: The recorder section of the configuration.
        store: Where events land.
        redactor: What removes credentials before they land.
        enabled: Whether emissions are accepted. Distinct from the recorder
            not existing at all, which is what ``recorder.enabled = false``
            produces.
        started_at: When collection began, which the uptime tile reports.
    """

    __slots__ = ("config", "store", "redactor", "enabled", "started_at", "_hooks")

    def __init__(self, config: RecorderConfig | None = None, store: Store | None = None) -> None:
        """Build a recorder.

        Args:
            config: The recorder configuration. Defaults are used when absent.
            store: An existing store, for tests that want to inspect one they
                built. A new one is opened otherwise.
        """
        self.config = config or RecorderConfig()
        self.store = store or Store(self.config.buffer, self.config.window_minutes)
        self.redactor = Redactor.from_config(self.config)
        self.enabled = True
        self.started_at = time.time()
        self._hooks: list[Callable[[Event], None]] = []

    # -- emission -------------------------------------------------------

    def emit(self, event: Event) -> Event:
        """Record an event.

        Every watcher comes through here. The order is deliberate: redact,
        then correlate, then store. Redaction first because the store must
        never hold a credential even for the instant before something else
        removes it.

        Args:
            event: The event to record.

        Returns:
            The event as stored — redacted, and stamped with the request in
            flight when there was one.
        """
        if not self.enabled:
            return event

        if event.request_id is None:
            event.request_id = current_request()

        self._redact(event)
        self.store.add(event)
        self._measure(event)

        for hook in self._hooks:
            hook(event)

        return event

    def _redact(self, event: Event) -> None:
        """Remove credentials from *event*, in place.

        Dispatched on the kind rather than by asking each event to redact
        itself, so that every rule about what is a secret lives in one file
        that can be read start to finish.

        Args:
            event: The event about to be stored.
        """
        redactor = self.redactor

        if isinstance(event, RequestEvent):
            event.query = redactor.query(event.query)
            event.headers = redactor.header_pairs(event.headers)
            event.response_headers = redactor.header_pairs(event.response_headers)
            if event.body:
                event.body = redactor.body(event.body, self.config.max_body_bytes)
            if event.response_body:
                event.response_body = redactor.body(
                    event.response_body, self.config.max_body_bytes
                )

        elif isinstance(event, QueryEvent):
            event.params = redactor.sql_params(event.params)

        elif isinstance(event, OutgoingEvent):
            event.url = redactor.url(event.url)

        elif isinstance(event, (LogEvent, ExceptionEvent)):
            event.message = redactor.text(event.message)

        elif isinstance(event, JobEvent) and isinstance(event.payload, dict):
            event.payload = redactor.params_of(event.payload)

    def _measure(self, event: Event) -> None:
        """Update the series a kind feeds.

        Args:
            event: The event just stored.
        """
        series = self.store.series

        if isinstance(event, RequestEvent):
            series.add("requests", event.duration_ms, error=event.status >= 500)
            series.add("requests.bytes", float(event.response_bytes))
        elif isinstance(event, QueryEvent):
            series.add("queries", event.duration_ms, error=event.slow)
        elif isinstance(event, CacheEvent):
            series.add("cache", event.duration_ms, error=event.result == "miss")
            series.add(f"cache.{event.result}", 1.0)
        elif isinstance(event, OutgoingEvent):
            series.add(
                "outgoing", event.duration_ms, error=bool(event.error) or event.status >= 400
            )
        elif isinstance(event, JobEvent):
            series.add("jobs", event.duration_ms, error=event.status == "failed")
            series.add(f"jobs.{event.status}", 1.0)
        elif isinstance(event, ExceptionEvent):
            series.add("exceptions", 1.0, error=True)
        elif isinstance(event, LogEvent):
            series.add("logs", 1.0, error=event.level in {"error", "critical"})
            series.add(f"logs.{event.level}", 1.0)
        elif isinstance(event, MailEvent):
            series.add("mail", 1.0, error=event.status == "failed")
        elif isinstance(event, WebsocketEvent):
            series.add("websocket", float(event.bytes))
        elif isinstance(event, SignalEvent):
            series.add("signals", event.duration_ms, error=bool(event.failures))
        elif isinstance(event, ScheduleEvent):
            series.add("schedules", event.duration_ms, error=event.outcome == "failed")

    # -- convenience for watchers ---------------------------------------

    def request(self, **fields: Any) -> RequestEvent:
        """Record an HTTP request.

        Args:
            **fields: Fields of :class:`~sillo_vise.recorder.events.RequestEvent`.

        Returns:
            The stored event.
        """
        event = RequestEvent(**fields)
        event.slow = event.duration_ms >= self.config.slow_request_ms
        return self.emit(event)  # type: ignore[return-value]

    def query(self, sql: str, **fields: Any) -> QueryEvent:
        """Record a SQL statement.

        Args:
            sql: The statement.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.QueryEvent`.

        Returns:
            The stored event.
        """
        event = QueryEvent(sql=sql, **fields)
        event.slow = event.duration_ms >= self.config.slow_query_ms
        if not event.fingerprint:
            event.fingerprint = fingerprint_sql(sql)
        return self.emit(event)  # type: ignore[return-value]

    def cache(self, key: str, **fields: Any) -> CacheEvent:
        """Record a cache operation.

        Args:
            key: The key operated on.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.CacheEvent`.

        Returns:
            The stored event.
        """
        return self.emit(CacheEvent(key=key, **fields))  # type: ignore[return-value]

    def outgoing(self, url: str, **fields: Any) -> OutgoingEvent:
        """Record a call to somebody else.

        Args:
            url: The target.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.OutgoingEvent`.

        Returns:
            The stored event.
        """
        return self.emit(OutgoingEvent(url=url, **fields))  # type: ignore[return-value]

    def job(self, task: str, **fields: Any) -> JobEvent:
        """Record a queued job at a point in its life.

        Args:
            task: The task's registered name.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.JobEvent`.

        Returns:
            The stored event.
        """
        fields.setdefault("task_id", current_job() or "")
        return self.emit(JobEvent(task=task, **fields))  # type: ignore[return-value]

    def schedule(self, job: str, **fields: Any) -> ScheduleEvent:
        """Record a firing of a scheduled job.

        Args:
            job: The scheduled job's name.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.ScheduleEvent`.

        Returns:
            The stored event.
        """
        return self.emit(ScheduleEvent(job=job, **fields))  # type: ignore[return-value]

    def exception(self, error: BaseException, **fields: Any) -> ExceptionEvent:
        """Record a raised exception.

        Args:
            error: The exception.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.ExceptionEvent`.

        Returns:
            The stored event.
        """
        fields.setdefault("type", type(error).__name__)
        fields.setdefault("message", str(error))
        fields.setdefault("where", where_raised(error))
        fields.setdefault("route", current_route())
        fields.setdefault("job", current_job() or "")
        return self.emit(ExceptionEvent(**fields))  # type: ignore[return-value]

    def log(self, level: str, message: str, **fields: Any) -> LogEvent:
        """Record a log line.

        Args:
            level: The level's name.
            message: The formatted message.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.LogEvent`.

        Returns:
            The stored event.
        """
        fields.setdefault("route", current_route())
        return self.emit(  # type: ignore[return-value]
            LogEvent(level=level.lower(), message=message, **fields)
        )

    def mail(self, to: str, **fields: Any) -> MailEvent:
        """Record a message sent, queued or suppressed.

        Args:
            to: The recipients.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.MailEvent`.

        Returns:
            The stored event.
        """
        return self.emit(MailEvent(to=to, **fields))  # type: ignore[return-value]

    def websocket(self, channel: str, **fields: Any) -> WebsocketEvent:
        """Record a websocket connection or message.

        Args:
            channel: The channel or path.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.WebsocketEvent`.

        Returns:
            The stored event.
        """
        return self.emit(WebsocketEvent(channel=channel, **fields))  # type: ignore[return-value]

    def signal(self, name: str, **fields: Any) -> SignalEvent:
        """Record an emitted application event.

        Args:
            name: The event's name.
            **fields: Other fields of
                :class:`~sillo_vise.recorder.events.SignalEvent`.

        Returns:
            The stored event.
        """
        return self.emit(SignalEvent(name=name, **fields))  # type: ignore[return-value]

    # -- hooks ----------------------------------------------------------

    def on_event(self, hook: Callable[[Event], None]) -> Callable[[Event], None]:
        """Call *hook* with every event, after it is stored.

        This is how the access log is written: the recorder already knows the
        duration and the byte count, so the log is a reader of the recorder
        rather than a second measurement of the same request.

        Args:
            hook: Called with each stored event.

        Returns:
            The hook, so this can be used as a decorator.
        """
        self._hooks.append(hook)
        return hook

    def remove_hook(self, hook: Callable[[Event], None]) -> None:
        """Stop calling *hook*.

        Args:
            hook: The hook to remove.
        """
        if hook in self._hooks:
            self._hooks.remove(hook)

    # -- reporting ------------------------------------------------------

    @property
    def uptime_seconds(self) -> float:
        """How long collection has been running.

        Returns:
            Seconds since the recorder was built.
        """
        return time.time() - self.started_at

    def totals(self) -> dict[str, int]:
        """Lifetime counts per kind.

        Returns:
            Kind names to counts.
        """
        return self.store.totals()

    def pause(self) -> None:
        """Stop accepting emissions, keeping what is already stored."""
        self.enabled = False

    def resume(self) -> None:
        """Start accepting emissions again."""
        self.enabled = True

    def __repr__(self) -> str:
        """A short description for debugging.

        Returns:
            Whether it is running, and how much it holds.
        """
        state = "enabled" if self.enabled else "paused"
        return f"Recorder({state}, {sum(self.totals().values())} events, {self.store!r})"

