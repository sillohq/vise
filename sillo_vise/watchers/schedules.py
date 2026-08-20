"""
sillo_vise.watchers.schedules — cron entries, their next fire, and the last result.

``SchedulerManager`` is on ``app.state["scheduler"]`` whenever ``setup_work``
ran, and it holds the jobs, their triggers and a ``SchedulerStats``. Reading it
gives the panel its table for free.

What it does not give is history: the manager knows a job's next fire, not how
long the last run took or whether it succeeded. That comes from wrapping the
manager's execute step, which is the same trade the query watcher makes and for
the same reason — the measurement point exists exactly once, and it is inside
the thing being measured.

Leader election is reported when the scheduler exposes it. sillo's manager runs
per process today, so on a multi-process deployment several of them fire the
same cron entry; that is a property of the framework, not something a dashboard
should paper over, so the panel says which process it is looking at.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..recorder import Recorder
from .base import Availability, Watcher, available, unavailable

__all__ = ["ScheduleWatcher"]

#: The manager method that runs one scheduled job.
_EXECUTE = "_execute"


class ScheduleWatcher(Watcher):
    """Records scheduled runs, and reads the schedule itself.

    Attributes:
        manager: The ``SchedulerManager``.
        original: The execute method taken off its class.
    """

    name = "schedules"
    requires = "a scheduler, which sillo.work.setup_work installs"

    __slots__ = ("manager", "original")

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.manager: Any = None
        self.original: Callable | None = None

    def probe(self, app: Any) -> Availability:
        """Report whether a scheduler is set up.

        Args:
            app: The application.

        Returns:
            Whether there is a schedule to read.
        """
        manager = _manager(app)
        if manager is None:
            return unavailable("no scheduler — sillo.work.setup_work has not run")

        return available(f"{len(_jobs(manager))} scheduled")

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Wrap the manager's execute step.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)

        self.manager = _manager(app)
        if self.manager is None:  # pragma: no cover - probe said yes
            return

        manager_class = type(self.manager)
        original = manager_class.__dict__.get(_EXECUTE)
        if original is None or getattr(original, "__vise_wrapped__", False):
            return

        self.original = original
        setattr(manager_class, _EXECUTE, self._wrap(original))

    def _wrap(self, original: Callable) -> Callable:
        """Build the replacement for the execute step.

        Args:
            original: What it was.

        Returns:
            A coroutine function that times the run and records its outcome.
        """

        async def wrapped(manager: Any, job: Any, *args: Any, **kwargs: Any) -> Any:
            """Run the job, then record how it went."""
            started = time.perf_counter()
            try:
                result = await original(manager, job, *args, **kwargs)
            except Exception as error:
                self._record(job, started, "failed", f"{type(error).__name__}: {error}")
                raise

            self._record(job, started, "completed", "")
            return result

        wrapped.__name__ = _EXECUTE
        wrapped.__qualname__ = f"vise:SchedulerManager.{_EXECUTE}"
        wrapped.__vise_wrapped__ = True  # type: ignore[attr-defined]
        return wrapped

    def _record(self, job: Any, started: float, outcome: str, error: str) -> None:
        """Store one scheduled run.

        Args:
            job: The scheduled job.
            started: ``perf_counter`` reading from before it ran.
            outcome: ``completed`` or ``failed``.
            error: The message, when it failed.
        """
        if self.recorder is None:  # pragma: no cover - detached
            return

        self.recorder.schedule(
            _job_name(job),
            expression=_expression(job),
            outcome=outcome,
            duration_ms=(time.perf_counter() - started) * 1000,
            error=error,
        )

    def jobs(self) -> list[dict[str, Any]]:
        """The schedule, as rows.

        Returns:
            One row per scheduled job.
        """
        return [
            {
                "job": _job_name(job),
                "expression": _expression(job),
                "next_fire": _next_fire(job),
                "status": _status(job),
                "enabled": _status(job) != "paused",
            }
            for job in _jobs(self.manager)
        ]

    def stats(self) -> dict[str, Any]:
        """The scheduler's own counters.

        Returns:
            The ``SchedulerStats`` fields, or zeroes when there is no manager.
        """
        stats = getattr(self.manager, "stats", None)
        if callable(stats):
            stats = stats()

        if stats is None:
            return {
                "jobs_total": 0,
                "jobs_active": 0,
                "jobs_paused": 0,
                "runs": 0,
                "errors": 0,
            }

        return stats.to_dict() if hasattr(stats, "to_dict") else dict(stats)

    def detach(self) -> None:
        """Put the execute step back."""
        if self.manager is not None and self.original is not None:
            setattr(type(self.manager), _EXECUTE, self.original)
        self.manager = None
        self.original = None
        super().detach()


def _manager(app: Any) -> Any:
    """The scheduler an application is using.

    Args:
        app: The application.

    Returns:
        The manager, or None.
    """
    state = getattr(app, "state", None) or {}
    work = state.get("work") or {}
    return state.get("scheduler") or work.get("scheduler")


def _jobs(manager: Any) -> list[Any]:
    """The jobs a manager holds.

    Args:
        manager: The scheduler manager.

    Returns:
        The jobs, or an empty list.
    """
    if manager is None:
        return []

    # `SchedulerManager.list()` is the accessor. An earlier version of this
    # read `manager.jobs`, which does not exist — so the panel appeared, said
    # "0 scheduled", and was wrong in a way nothing complained about.
    lister = getattr(manager, "list", None)
    if callable(lister):
        try:
            return list(lister())
        except Exception:  # noqa: BLE001 - a scheduler mid-start is not a crash
            return []

    held = getattr(manager, "_jobs", None)
    if isinstance(held, dict):
        return list(held.values())

    return []


def _job_name(job: Any) -> str:
    """A scheduled job's name.

    Args:
        job: The job.

    Returns:
        The name.
    """
    return str(getattr(job, "name", "") or getattr(job, "id", "") or "job")


def _expression(job: Any) -> str:
    """A job's trigger, as declared.

    Args:
        job: The job.

    Returns:
        The cron expression or interval, as text.
    """
    trigger = getattr(job, "trigger", None)
    if trigger is None:
        return ""

    # A cron trigger carries its expression; an interval one carries seconds
    # and reads far better as "every 30s" than as the repr of an object.
    expression = getattr(trigger, "expression", None)
    if expression:
        return str(expression)

    seconds = getattr(trigger, "seconds", None) or getattr(trigger, "interval", None)
    if seconds:
        return f"every {int(seconds)}s"

    return type(trigger).__name__.removesuffix("Trigger").lower()


def _next_fire(job: Any) -> float | None:
    """When a job fires next, as a timestamp.

    Args:
        job: The job.

    Returns:
        The timestamp, or None when the scheduler has not computed one.
    """
    when = (
        getattr(job, "next_run_time", None)
        or getattr(job, "next_run", None)
        or getattr(job, "next_fire", None)
    )
    if when is None:
        return None

    timestamp = getattr(when, "timestamp", None)
    return timestamp() if callable(timestamp) else float(when)


def _status(job: Any) -> str:
    """A job's status, as the framework's ``JobStatus`` names it.

    Args:
        job: The job.

    Returns:
        ``active``, ``paused``, ``completed`` or ``cancelled``.
    """
    status = getattr(job, "status", None)
    return str(getattr(status, "value", None) or status or "active")
