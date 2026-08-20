"""
sillo_vise.recorder.events — the eleven things vise records.

One dataclass per kind, all carrying ``id``, ``at`` and ``request_id``. That
last field is what makes the Requests panel more than a list: an event emitted
while a request is in flight is tagged with it, so "the queries, cache reads,
outgoing calls, jobs, events and mail one request produced" is a filter over
the store rather than six separate correlations.

``slots=True`` throughout. The recorder allocates one of these per query, per
cache read and per outgoing call, and the framework's own overhead went from
702.8µs to 27.1µs by deleting per-request allocation — handing a chunk of that
back in ``__dict__`` overhead would be an odd way to repay it.

Every dataclass carries ``to_dict``. The dashboard reads events over JSON, and
``dataclasses.asdict`` recurses and copies where these need neither.
"""

from __future__ import annotations

import dataclasses
import enum
import time
import uuid
from typing import Any, ClassVar

__all__ = [
    "CacheEvent",
    "Event",
    "EventKind",
    "ExceptionEvent",
    "JobEvent",
    "LogEvent",
    "MailEvent",
    "OutgoingEvent",
    "QueryEvent",
    "RequestEvent",
    "ScheduleEvent",
    "SignalEvent",
    "WebsocketEvent",
    "new_id",
]


def new_id() -> str:
    """Mint a short identifier for an event.

    Twelve hex characters, not a full UUID: these appear in log lines and in
    table cells where a 36-character identifier costs a column, and the
    namespace only has to be unique within one bounded in-process ring.

    Returns:
        A twelve-character identifier.
    """
    return uuid.uuid4().hex[:12]


class EventKind(str, enum.Enum):
    """The kinds of event the store keeps, one ring each.

    A ``str`` enum so a kind serialises to its own name without a conversion
    step at the JSON boundary, and so a route parameter can be compared
    against it directly.
    """

    REQUEST = "request"
    QUERY = "query"
    CACHE = "cache"
    OUTGOING = "outgoing"
    JOB = "job"
    SCHEDULE = "schedule"
    EXCEPTION = "exception"
    LOG = "log"
    MAIL = "mail"
    WEBSOCKET = "websocket"
    SIGNAL = "signal"


@dataclasses.dataclass(slots=True)
class Event:
    """What every recorded event carries.

    ``kind`` is a class attribute rather than a field. Every instance of a
    subclass has the same one, so storing it per instance would be a slot per
    event spent restating what the class already says — and making it a field
    with a default forces every subclass to re-declare defaults for the fields
    that follow it.

    Attributes:
        kind: Which ring this belongs in. Set by the subclass.
        id: Identifier, unique within the process.
        at: Unix timestamp of capture.
        request_id: The request in flight when this was emitted, when there
            was one. Jobs and scheduled runs have none.
    """

    kind: ClassVar[EventKind] = EventKind.LOG

    id: str = dataclasses.field(default_factory=new_id)
    at: float = dataclasses.field(default_factory=time.time)
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render for the dashboard.

        Returns:
            The event as plain data, with the kind as its string value.
        """
        data = {
            field.name: getattr(self, field.name) for field in dataclasses.fields(self)
        }
        data["kind"] = self.kind.value
        return data

    def belongs_to(self, request_id: str) -> bool:
        """Whether this was emitted while *request_id* was in flight.

        Args:
            request_id: The request being assembled.

        Returns:
            True when the event is part of that request's story.
        """
        return self.request_id == request_id


@dataclasses.dataclass(slots=True)
class RequestEvent(Event):
    """One HTTP request, and what it cost.

    The field names follow the framework's own ``RequestRecord`` —
    ``duration_ms``, ``response_bytes``, ``client`` — so a reader who knows one
    knows the other, and so a hosted collector ingesting these does not need a
    translation table.

    Attributes:
        method: HTTP method.
        path: Path matched, without the query string.
        query: Query string, redacted.
        route: Name of the route that handled it, when it was named.
        status: Response status code. Zero while still in flight.
        duration_ms: Wall-clock milliseconds.
        response_bytes: Bytes written to the body.
        client: ``host:port`` of the peer.
        headers: Request headers, redacted, in the order sent.
        response_headers: Response headers, redacted, in the order sent.
        user: Identity of the authenticated user, when there was one.
        slow: Whether *duration_ms* reached the configured threshold.
        body: Captured request body, when body capture is on.
        response_body: Captured response body, when body capture is on.
    """

    kind: ClassVar[EventKind] = EventKind.REQUEST

    method: str = "GET"
    path: str = "/"
    query: str = ""
    route: str = ""
    status: int = 0
    duration_ms: float = 0.0
    response_bytes: int = 0
    client: str = ""
    headers: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    response_headers: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    user: str = ""
    slow: bool = False
    body: str = ""
    response_body: str = ""

    def __post_init__(self) -> None:
        """Let the event be its own correlation key.

        A request is the thing other events point at, so its ``request_id`` is
        its own id. That removes a special case from every reader: "events
        belonging to this request" is one predicate, and the request itself
        satisfies it.
        """
        if self.request_id is None:
            self.request_id = self.id

    @property
    def full_path(self) -> str:
        """Path and query string together.

        Returns:
            What the client asked for, as one string.
        """
        return f"{self.path}?{self.query}" if self.query else self.path


@dataclasses.dataclass(slots=True)
class QueryEvent(Event):
    """One SQL statement.

    Attributes:
        sql: The statement, as the driver received it.
        params: Bind parameters, or their types when binding redaction is on.
        duration_ms: How long it took.
        rows: Rows returned or affected, when the driver reported it.
        connection: Which connection ran it.
        source: The call site, when it could be attributed.
        slow: Whether *duration_ms* reached the configured threshold.
        fingerprint: The statement with its literals removed, which is what
            N+1 detection groups on.
    """

    kind: ClassVar[EventKind] = EventKind.QUERY

    sql: str = ""
    params: Any = None
    duration_ms: float = 0.0
    rows: int = 0
    connection: str = "default"
    source: str = ""
    slow: bool = False
    fingerprint: str = ""


@dataclasses.dataclass(slots=True)
class CacheEvent(Event):
    """One cache operation.

    Attributes:
        key: The key, namespaced as the backend saw it.
        operation: ``get``, ``set``, ``delete``, ``touch`` or ``clear``.
        result: ``hit``, ``miss``, ``stored``, ``evicted`` or ``error``.
        ttl: Seconds remaining or set, when the operation had one.
        duration_ms: How long it took.
        backend: Which backend served it.
    """

    kind: ClassVar[EventKind] = EventKind.CACHE

    key: str = ""
    operation: str = "get"
    result: str = ""
    ttl: int | None = None
    duration_ms: float = 0.0
    backend: str = ""

    @property
    def prefix(self) -> str:
        """The key's first segment, which is how hit ratio is grouped.

        Returns:
            Everything before the first colon, or the whole key.
        """
        return self.key.partition(":")[0]


@dataclasses.dataclass(slots=True)
class OutgoingEvent(Event):
    """One call the application made to somebody else.

    Attributes:
        method: HTTP method.
        url: Target URL, with userinfo and configured query values redacted.
        host: Host alone, which is what per-host volume groups on.
        status: Response status. Zero when the call never completed.
        duration_ms: Wall-clock milliseconds.
        retries: Attempts after the first.
        request_bytes: Bytes sent.
        response_bytes: Bytes received.
        error: Message, when the call failed without a status.
        breaker: Circuit-breaker state for the host at the time.
    """

    kind: ClassVar[EventKind] = EventKind.OUTGOING

    method: str = "GET"
    url: str = ""
    host: str = ""
    status: int = 0
    duration_ms: float = 0.0
    retries: int = 0
    request_bytes: int = 0
    response_bytes: int = 0
    error: str = ""
    breaker: str = ""


@dataclasses.dataclass(slots=True)
class JobEvent(Event):
    """One queued job, at one point in its life.

    The status values are the framework's ``TaskStatus`` — pending, scheduled,
    running, completed, failed, cancelled, retrying — so the Queues panel's
    first-class views are the enum, not a vocabulary vise invented.

    Attributes:
        task: The task's registered name.
        queue: Which queue it is on.
        task_id: The framework's identifier for the job.
        status: A ``TaskStatus`` value.
        attempt: Which attempt this is.
        duration_ms: How long the attempt took.
        payload: Arguments, redacted.
        error: Message, when the attempt failed.
        traceback: Formatted traceback, when the attempt failed.
        tags: Tags declared on the task.
    """

    kind: ClassVar[EventKind] = EventKind.JOB

    task: str = ""
    queue: str = "default"
    task_id: str = ""
    status: str = "pending"
    attempt: int = 0
    duration_ms: float = 0.0
    payload: Any = None
    error: str = ""
    traceback: str = ""
    tags: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(slots=True)
class ScheduleEvent(Event):
    """One firing of a scheduled job.

    Attributes:
        job: The scheduled job's name.
        expression: Its trigger, as declared.
        outcome: ``completed``, ``failed`` or ``skipped``.
        duration_ms: How long the run took.
        manual: Whether a person pressed run, rather than the trigger firing.
        error: Message, when the run failed.
    """

    kind: ClassVar[EventKind] = EventKind.SCHEDULE

    job: str = ""
    expression: str = ""
    outcome: str = "completed"
    duration_ms: float = 0.0
    manual: bool = False
    error: str = ""


@dataclasses.dataclass(slots=True)
class ExceptionEvent(Event):
    """One raised exception.

    Attributes:
        type: The exception class's name.
        message: ``str()`` of the exception, with loose credentials redacted.
        where: ``file:line`` the exception came from.
        traceback: The formatted traceback.
        route: Route being handled, when one was.
        job: Job being executed, when one was.
        handled: Whether an exception handler dealt with it.
        fingerprint: Type and location, which is what the panel groups on.
    """

    kind: ClassVar[EventKind] = EventKind.EXCEPTION

    type: str = ""
    message: str = ""
    where: str = ""
    traceback: str = ""
    route: str = ""
    job: str = ""
    handled: bool = False
    fingerprint: str = ""

    def __post_init__(self) -> None:
        """Derive the grouping key when it was not given."""
        if not self.fingerprint:
            self.fingerprint = f"{self.type}@{self.where}"


@dataclasses.dataclass(slots=True)
class LogEvent(Event):
    """One log line.

    Attributes:
        level: Lowercased level name.
        message: The formatted message, with loose credentials redacted.
        logger: The logger's name.
        route: Route being handled, when one was.
        fields: Structured extras attached to the record.
    """

    kind: ClassVar[EventKind] = EventKind.LOG

    level: str = "info"
    message: str = ""
    logger: str = ""
    route: str = ""
    fields: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True)
class MailEvent(Event):
    """One message sent, queued or suppressed.

    Attributes:
        to: Recipients, joined.
        subject: The subject line.
        template: Template that rendered it, when one did.
        status: ``completed``, ``queued``, ``retrying``, ``failed`` or
            ``suppressed``.
        mailer: Which transport handled it.
        error: Message, when delivery failed.
    """

    kind: ClassVar[EventKind] = EventKind.MAIL

    to: str = ""
    subject: str = ""
    template: str = ""
    status: str = "queued"
    mailer: str = ""
    error: str = ""


@dataclasses.dataclass(slots=True)
class WebsocketEvent(Event):
    """One websocket connection or message.

    Attributes:
        channel: Channel or path.
        action: ``connect``, ``disconnect``, ``send`` or ``receive``.
        members: Members on the channel afterwards.
        bytes: Payload size, or the connection's total on a close.
        close_code: Close code, on a disconnect.
        duration_ms: How long the connection lived, on a close. Zero on the
            frames in between — a connection has a lifetime and a frame does
            not, and the panel shows the two in different columns.
        slow: Whether the consumer was behind when this happened.
    """

    kind: ClassVar[EventKind] = EventKind.WEBSOCKET

    channel: str = ""
    action: str = "connect"
    members: int = 0
    bytes: int = 0
    close_code: int | None = None
    duration_ms: float = 0.0
    slow: bool = False


@dataclasses.dataclass(slots=True)
class SignalEvent(Event):
    """One emitted application event, and how its listeners fared.

    Named ``Signal`` rather than ``Event`` because :class:`Event` is already
    the base class here, and a ``EventEvent`` helps nobody.

    Attributes:
        name: The event's name.
        listeners: How many listeners ran.
        duration_ms: Total time across listeners.
        failures: How many listeners raised.
        transport: Which transport carried it.
    """

    kind: ClassVar[EventKind] = EventKind.SIGNAL

    name: str = ""
    listeners: int = 0
    duration_ms: float = 0.0
    failures: int = 0
    transport: str = ""

    def __post_init__(self) -> None:
        """Force the kind."""
        self.kind = EventKind.SIGNAL
