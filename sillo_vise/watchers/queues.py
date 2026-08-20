"""
sillo_vise.watchers.queues — queue size, throughput and what is in flight.

Unlike the other watchers, this one mostly *polls*. A queue's size, its oldest
pending age and its health are properties of a shared backend, not events
happening inside this process — a worker on another machine draining the queue
changes all three without anything here being called. sillo's backends already
answer this: ``queue_stats(name)`` returns a ``QueueStats`` with exactly the
fields the panel shows.

So there are two halves. The poll keeps the size and health tiles current, and
the middleware hook — sillo's queue middleware contract, the same shape
``LoggingMiddleware`` uses — records individual jobs as they run, which is what
gives the table its statuses, durations and tracebacks.

The probe is a real ``ping``. "A queue is configured" and "the queue answers"
are different states, and only the second one should produce a panel: a Redis
URL in the configuration with nothing listening on it is precisely the case
where a dashboard showing green would be lying.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any

import anyio

from ..recorder import Recorder, job_scope
from .base import Availability, Watcher, available, unavailable

__all__ = ["QueueMiddleware", "QueueWatcher"]


class QueueMiddleware:
    """Queue middleware that records each job's life.

    Implements the hook shape sillo's ``LoggingMiddleware`` uses —
    ``before_enqueue``, ``before_execute``, ``after_execute``, ``on_error`` —
    so it registers wherever that one does.

    Attributes:
        recorder: Where events go.
        started: Start times, keyed by task id, so a duration can be measured
            across the two callbacks that bracket execution.
    """

    __slots__ = ("recorder", "started", "_scopes")

    def __init__(self, recorder: Recorder) -> None:
        """Build the middleware.

        Args:
            recorder: Where events go.
        """
        self.recorder = recorder
        self.started: dict[str, float] = {}
        self._scopes: dict[str, Any] = {}

    async def before_enqueue(self, task: Any) -> None:
        """Record a job being put on a queue.

        Args:
            task: The task being enqueued.
        """
        self.recorder.job(
            _name(task),
            queue=_queue(task),
            task_id=_id(task),
            status="pending",
            payload=_payload(task),
            tags=_tags(task),
        )

    async def before_execute(self, task: Any) -> None:
        """Note that a job has started, and open its context.

        The context is what attributes a query issued inside a job to the job
        rather than to whichever request happened to enqueue it.

        Args:
            task: The task about to run.
        """
        task_id = _id(task)
        self.started[task_id] = time.perf_counter()

        scope = job_scope(task_id)
        scope.__enter__()
        self._scopes[task_id] = scope

        self.recorder.job(
            _name(task),
            queue=_queue(task),
            task_id=task_id,
            status="running",
            attempt=int(getattr(task, "attempt", 0) or 0),
            tags=_tags(task),
        )

    async def after_execute(self, result: Any) -> None:
        """Record a job that finished.

        Args:
            result: The ``TaskResult``.
        """
        task_id = str(getattr(result, "task_id", "") or "")
        status = getattr(result, "status", None)

        self.recorder.job(
            str(getattr(result, "name", "") or ""),
            queue=str(getattr(result, "queue_name", "") or "default"),
            task_id=task_id,
            status=getattr(status, "value", None) or str(status or "completed"),
            attempt=int(getattr(result, "attempt", 0) or 0),
            duration_ms=self._elapsed(task_id),
        )
        self._close(task_id)

    async def on_error(self, task: Any, error: Exception) -> None:
        """Record a job that raised.

        Args:
            task: The task that failed.
            error: What it raised.
        """
        task_id = _id(task)
        self.recorder.job(
            _name(task),
            queue=_queue(task),
            task_id=task_id,
            status="failed",
            attempt=int(getattr(task, "attempt", 0) or 0),
            duration_ms=self._elapsed(task_id),
            error=f"{type(error).__name__}: {error}",
            traceback="".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        )
        self.recorder.exception(error, job=_name(task))
        self._close(task_id)

    def _elapsed(self, task_id: str) -> float:
        """How long a job ran, in milliseconds.

        Args:
            task_id: The job's id.

        Returns:
            The duration, or zero when the start was never seen — which
            happens for a job that was already running when vise attached.
        """
        started = self.started.pop(task_id, None)
        return (time.perf_counter() - started) * 1000 if started else 0.0

    def _close(self, task_id: str) -> None:
        """Leave a job's context.

        Args:
            task_id: The job's id.
        """
        scope = self._scopes.pop(task_id, None)
        if scope is not None:
            scope.__exit__(None, None, None)


class QueueWatcher(Watcher):
    """Watches the queue backend, and every job that runs through it.

    Attributes:
        backend: The queue backend found on the application.
        middleware: The per-job hook, when one could be registered.
    """

    name = "queues"
    requires = "a queue backend that answers ping()"

    __slots__ = ("backend", "middleware")

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.backend: Any = None
        self.middleware: QueueMiddleware | None = None

    def probe(self, app: Any) -> Availability:
        """Report whether a queue backend answers.

        Args:
            app: The application.

        Returns:
            Whether there is a live queue.
        """
        backend = _backend(app)
        if backend is None:
            return unavailable("no queue backend — sillo.work is not set up")

        # A configured URL with nothing listening on it is the exact case
        # where a green panel would be a lie, so the probe asks.
        if not _reachable(backend):
            return unavailable(f"{_describe(backend)} is not answering")

        return available(_describe(backend))

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Register the per-job hook and remember the backend for polling.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)

        self.backend = _backend(app)
        self.middleware = QueueMiddleware(recorder)

        registrar = getattr(self.backend, "add_middleware", None) or getattr(
            self.backend, "use", None
        )
        if callable(registrar):
            registrar(self.middleware)

    def queues(self) -> list[str]:
        """The queue names the backend knows about.

        Returns:
            The names, or ``["default"]`` when the backend does not enumerate
            them — which is the honest fallback, since every sillo project has
            a default queue whether or not the backend lists it.
        """
        names = getattr(self.backend, "queue_names", None)
        if callable(names):
            try:
                found = list(names())
            except Exception:  # noqa: BLE001 - an unreachable backend is not a crash
                found = []
            if found:
                return found

        known = getattr(self.backend, "_queues", None)
        return sorted(known) if known else ["default"]

    async def stats(self, name: str) -> Any:
        """A queue's current statistics.

        Args:
            name: The queue's name.

        Returns:
            The backend's ``QueueStats``, or None when it could not be read.
        """
        reader = getattr(self.backend, "queue_stats", None)
        if not callable(reader):
            return None

        try:
            return await reader(name)
        except Exception:  # noqa: BLE001 - a queue that stops answering is a
            # panel that goes quiet, not a dashboard that falls over.
            return None

    def detach(self) -> None:
        """Forget the backend. The middleware stays registered.

        sillo's queue backends have no interface for removing a middleware,
        and a watcher must not reach into a private list to invent one. In
        practice this only matters at shutdown, where the process is going
        away regardless.
        """
        self.backend = None
        self.middleware = None
        super().detach()


def _backend(app: Any) -> Any:
    """The queue backend an application is using.

    Args:
        app: The application.

    Returns:
        The backend, or None.
    """
    state = getattr(app, "state", None) or {}
    work = state.get("work") or {}
    return (
        state.get("queue")
        or work.get("backend")
        or work.get("connection")
        or state.get("queue_connection")
    )


def _reachable(backend: Any) -> bool:
    """Whether a backend answers.

    An in-process backend is reachable by definition; a Redis one has to be
    asked, and asking is synchronous here because the probe runs outside the
    event loop.

    Args:
        backend: The queue backend.

    Returns:
        Whether the panel should exist.
    """
    ping = getattr(backend, "ping", None)
    if ping is None:
        # A memory backend has nothing to ping and is always up.
        return True

    try:
        return bool(anyio.from_thread.run(ping))
    except Exception:  # noqa: BLE001 - outside a loop, or the queue is down
        return _sync_ping(ping)


def _sync_ping(ping: Any) -> bool:
    """Run an async ``ping`` from outside an event loop.

    Args:
        ping: The backend's ping coroutine function.

    Returns:
        Whether it answered.
    """
    try:
        return bool(asyncio.run(ping()))
    except Exception:  # noqa: BLE001 - the queue is down, which is the answer
        return False


def _describe(backend: Any) -> str:
    """Name a backend for the interface.

    Args:
        backend: The queue backend.

    Returns:
        A short lowercase name.
    """
    return type(backend).__name__.removesuffix("Backend").lower() or "queue"


def _name(task: Any) -> str:
    """A task's registered name.

    Args:
        task: The task.

    Returns:
        The name, or the class's.
    """
    return str(getattr(task, "name", "") or type(task).__name__)


def _queue(task: Any) -> str:
    """The queue a task is on.

    Args:
        task: The task.

    Returns:
        The queue's name.
    """
    return str(
        getattr(task, "queue_name", "") or getattr(task, "queue", "") or "default"
    )


def _id(task: Any) -> str:
    """A task's identifier.

    Args:
        task: The task.

    Returns:
        The id, or an empty string.
    """
    return str(getattr(task, "task_id", "") or getattr(task, "id", "") or "")


def _payload(task: Any) -> Any:
    """A task's arguments, as something the recorder can redact.

    Args:
        task: The task.

    Returns:
        A mapping of arguments, or None.
    """
    kwargs = getattr(task, "kwargs", None)
    args = getattr(task, "args", None)

    if isinstance(kwargs, dict) and kwargs:
        return dict(kwargs)
    if args:
        return {"args": list(args)}
    return None


def _tags(task: Any) -> list[str]:
    """Tags declared on a task.

    Args:
        task: The task.

    Returns:
        The tags, as text.
    """
    tags = getattr(task, "tags", None) or ()
    return [str(tag) for tag in tags]
