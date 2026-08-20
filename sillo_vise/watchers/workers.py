"""
sillo_vise.watchers.workers — the supervisor, and the processes it is running.

``WorkerStats`` already holds processed, failed, active, workers, circuit and
uptime_seconds — the six fields the panel shows. What the framework does not
have is a time series behind them, which is the recorder's job, and it does not
have any of it *in this process*: workers run somewhere else.

That is the honest constraint, and it decides what this panel can be. When the
worker pool is in the same process — a development server running
``vise serve`` with an in-process pool — the stats are read directly and
everything on the panel is true. When workers are separate processes, only what
the shared backend knows is visible, and the panel says so rather than showing
a worker count of zero as though every worker had died.

Memory and CPU per worker are read from ``psutil`` when it is installed. It is
not a dependency: a per-process memory figure is worth having and is not worth
making every project install a compiled package for.
"""

from __future__ import annotations

import os
import time
from typing import Any

from ..recorder import Recorder
from .base import Availability, Watcher, available, unavailable

__all__ = ["WorkerWatcher"]


class WorkerWatcher(Watcher):
    """Reports the worker pool, wherever it can see one.

    Attributes:
        pool: The in-process pool, when there is one.
        backend: The queue backend, used when there is not.
        local: Whether workers run in this process.
    """

    name = "workers"
    requires = "a worker pool, or a queue backend that reports worker state"

    __slots__ = ("pool", "backend", "local", "_started")

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.pool: Any = None
        self.backend: Any = None
        self.local = False
        self._started = time.time()

    def probe(self, app: Any) -> Availability:
        """Report whether any worker state can be read.

        Args:
            app: The application.

        Returns:
            Whether there is something to show.
        """
        state = getattr(app, "state", None) or {}
        work = state.get("work") or {}

        if _pool(state, work) is not None:
            return available("in-process pool")

        backend = state.get("queue_connection") or work.get("connection")
        if backend is not None and hasattr(backend, "queue_stats"):
            return available(f"{_describe(backend)}, workers out of process")

        return unavailable("no worker pool and no queue backend to ask")

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Remember what can be read.

        Nothing is wrapped: worker state is polled, because the numbers change
        because of things happening in other processes.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)

        state = getattr(app, "state", None) or {}
        work = state.get("work") or {}

        self.pool = _pool(state, work)
        self.backend = state.get("queue_connection") or work.get("connection")
        self.local = self.pool is not None
        self._started = time.time()

    def stats(self) -> dict[str, Any]:
        """The current worker statistics.

        Returns:
            The ``WorkerStats`` fields, plus whether they describe this
            process. A reader has to be able to tell "three workers, and I can
            see them" from "three workers somewhere, and I am guessing".
        """
        stats = getattr(self.pool, "stats", None)
        if callable(stats):
            stats = stats()

        if stats is None:
            return {
                "processed": 0,
                "failed": 0,
                "active": 0,
                "workers": 0,
                "circuit": "closed",
                "uptime": int(time.time() - self._started),
                "local": self.local,
            }

        data = stats.to_dict() if hasattr(stats, "to_dict") else dict(stats)
        data["local"] = self.local
        return data

    def processes(self) -> list[dict[str, Any]]:
        """One row per worker process this watcher can actually see.

        Returns:
            The rows. Empty when workers run elsewhere, which the panel renders
            as a note rather than as an empty table.
        """
        if not self.local:
            return []

        workers = getattr(self.pool, "workers", None) or ()
        usage = _resource_usage()

        return [
            {
                "worker": getattr(worker, "name", f"worker-{index + 1:02d}"),
                "queues": ", ".join(getattr(worker, "queues", ()) or ["default"]),
                "circuit": _circuit(worker),
                "processed": int(getattr(worker, "processed", 0) or 0),
                "uptime": int(time.time() - self._started),
                "memory": usage,
            }
            for index, worker in enumerate(workers)
        ]


def _pool(state: Any, work: Any) -> Any:
    """The in-process worker pool, if this process runs one.

    Args:
        state: The application's state.
        work: The ``work`` entry within it.

    Returns:
        The pool, or None.
    """
    return state.get("worker_pool") or work.get("pool") or work.get("workers")


def _circuit(worker: Any) -> str:
    """A worker's circuit-breaker state, as the framework names it.

    Args:
        worker: The worker.

    Returns:
        ``closed``, ``open`` or ``half_open``.
    """
    circuit = getattr(worker, "circuit", None)
    return str(getattr(circuit, "value", None) or circuit or "closed")


def _describe(backend: Any) -> str:
    """Name a backend for the interface.

    Args:
        backend: The queue backend.

    Returns:
        A short lowercase name.
    """
    return type(backend).__name__.removesuffix("Backend").lower() or "queue"


def _resource_usage() -> str:
    """This process's resident memory, when it can be read.

    Args:
        None.

    Returns:
        A human figure, or an empty string when psutil is not installed. An
        empty string renders as a dash: a memory column that says nothing is
        better than one that says zero.
    """
    try:
        import psutil
    except ImportError:
        return ""

    try:
        resident = psutil.Process(os.getpid()).memory_info().rss
    except Exception:  # noqa: BLE001 - a permissions error is not a crash
        return ""

    return f"{resident / 1_000_000:.0f} MB"
