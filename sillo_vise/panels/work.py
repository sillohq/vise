"""
sillo_vise.panels.work — queues, workers and schedules.

The three panels about things happening outside a request. They differ from the
Monitor panels in one important way: much of what they show is *not* in this
process. A worker draining a queue on another machine changes the queue's size
without anything here being called, so these panels combine recorded events with
a live read of the backend.

Where the two disagree, the live read wins and the panel says which it is
showing. A dashboard that reported a queue as empty because this process had
not seen anything enqueued would be worse than no dashboard.
"""

from __future__ import annotations

import time
from typing import Any

from ..logs.format import duration, elapsed, number, truncate
from ..recorder import EventKind
from .base import Panel, PanelContext, Rendered, columns, state_column, table
from .tiles import (
    TONE_BAD,
    TONE_GOOD,
    TONE_INFO,
    TONE_MUTED,
    rate_tile,
    spark_of,
    threshold_tone,
    tile,
)

__all__ = ["QueuesPanel", "SchedulesPanel", "WorkersPanel"]

#: Bars a chart draws.
CHART_BARS = 40

#: Statuses the queue table can show, in the order a job passes through them.
STATUSES = (
    "pending",
    "scheduled",
    "running",
    "retrying",
    "completed",
    "failed",
    "cancelled",
)


class QueuesPanel(Panel):
    """Size, throughput and oldest pending age, per queue."""

    id = "queues"
    name = "Queues"
    group = "Work"
    icon = "queue"
    summary = "Size, throughput and oldest pending age, per queue."
    watcher = "queues"

    def badge(self, context: PanelContext) -> str:
        """How many jobs have failed.

        Args:
            context: What the panel may read.

        Returns:
            The count, or an empty string when none have.
        """
        failed = int(context.store.series["jobs.failed"].total())
        return str(failed) if failed else ""

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the queue view.

        Args:
            context: What the panel may read.

        Returns:
            Tiles, a throughput chart, the job table and the per-queue rail.
        """
        series = context.store.series
        jobs = series["jobs"]
        completed = int(series["jobs.completed"].total())
        failed = int(series["jobs.failed"].total())
        running = self._in_flight(context)
        recent = context.store.recent(EventKind.JOB, limit=40)

        return Rendered(
            id=self.id,
            kind=EventKind.JOB.value,
            tiles=[
                tile(
                    "In flight",
                    number(len(running)),
                    delta=f"{number(int(series['jobs.pending'].total()))} enqueued",
                    tone=TONE_INFO if running else TONE_MUTED,
                    spark=spark_of(series["jobs.running"]),
                ),
                tile(
                    "Completed",
                    number(completed),
                    delta="since start",
                    tone=TONE_GOOD,
                    spark=spark_of(series["jobs.completed"]),
                ),
                tile(
                    "Failed",
                    number(failed),
                    delta="since start",
                    tone=threshold_tone(failed, 1, 20),
                    spark=spark_of(series["jobs.failed"]),
                ),
                rate_tile("Jobs", jobs),
            ],
            chart={
                "label": "Jobs processed",
                "note": f"{CHART_BARS}m",
                "bars": jobs.counts(CHART_BARS),
            },
            table=table(
                columns(
                    "Task",
                    ("Queue", "hidden sm:table-cell"),
                    state_column("Status"),
                    ("Duration", "hidden md:table-cell"),
                    ("Attempts", "hidden lg:table-cell"),
                ),
                [
                    [
                        truncate(event.task, 36),
                        event.queue,
                        event.status,
                        duration(event.duration_ms) if event.duration_ms else "—",
                        number(event.attempt),
                    ]
                    for event in recent
                ],
                ids=[event.id for event in recent],
            ),
            aside=self._queues(context),
            note=""
            if context.store.count(EventKind.JOB)
            else "No jobs have run since vise started. Queue size is read from the backend.",
        )

    @staticmethod
    def _in_flight(context: PanelContext) -> list[Any]:
        """Jobs that started and have not been seen finishing.

        Args:
            context: What the panel may read.

        Returns:
            The running jobs.
        """
        finished: set[str] = set()
        running: dict[str, Any] = {}

        for event in context.store.all(EventKind.JOB):
            if event.status == "running":
                running[event.task_id] = event
            elif event.status in {"completed", "failed", "cancelled"}:
                finished.add(event.task_id)

        return [event for task_id, event in running.items() if task_id not in finished]

    @staticmethod
    def _queues(context: PanelContext) -> dict[str, Any]:
        """One row per queue, from what the watcher recorded.

        Reading the backend's ``queue_stats`` would be better, and is
        deliberately not done here: this method is synchronous and
        ``queue_stats`` is a coroutine. The dashboard fills the live numbers in
        separately, on its own async path, rather than this panel blocking a
        render on a network round trip.

        Args:
            context: What the panel may read.

        Returns:
            The side rail.
        """
        seen: dict[str, dict[str, int]] = {}
        for event in context.store.all(EventKind.JOB):
            counts = seen.setdefault(event.queue, {})
            counts[event.status] = counts.get(event.status, 0) + 1

        rows = []
        for name, counts in sorted(seen.items()):
            pending = counts.get("pending", 0)
            failed = counts.get("failed", 0)
            rows.append(
                [
                    name,
                    f"{pending} enqueued",
                    "stalled"
                    if failed and not counts.get("completed")
                    else "degraded"
                    if failed
                    else "healthy",
                ]
            )

        return {"label": "Queues", "note": f"{len(rows)}", "rows": rows}


class WorkersPanel(Panel):
    """The supervisor, and the processes it is running."""

    id = "workers"
    name = "Workers"
    group = "Work"
    icon = "worker"
    summary = "The supervisor, and the processes it is running."
    watcher = "workers"

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the worker view.

        Args:
            context: What the panel may read.

        Returns:
            Tiles and, when the workers are in this process, a table of them.
        """
        watcher = context.watcher("workers")
        stats = watcher.stats() if watcher else {}
        processes = watcher.processes() if watcher else []
        local = bool(stats.get("local"))

        return Rendered(
            id=self.id,
            tiles=[
                tile(
                    "Workers",
                    number(stats.get("workers", 0)),
                    delta="in this process" if local else "out of process",
                    tone=TONE_GOOD if stats.get("workers") else TONE_MUTED,
                ),
                tile(
                    "Active",
                    number(stats.get("active", 0)),
                    delta="running now",
                    tone=TONE_INFO,
                ),
                tile(
                    "Processed",
                    number(stats.get("processed", 0)),
                    delta=f"{number(stats.get('failed', 0))} failed",
                    tone=TONE_GOOD,
                ),
                tile(
                    "Circuit",
                    str(stats.get("circuit", "closed")),
                    delta=elapsed(float(stats.get("uptime", 0))),
                    tone=TONE_GOOD if stats.get("circuit") == "closed" else TONE_BAD,
                ),
            ],
            table=table(
                columns(
                    "Worker",
                    ("Queues", "hidden sm:table-cell"),
                    state_column("Circuit"),
                    ("Processed", "hidden md:table-cell"),
                    ("Uptime", "hidden lg:table-cell"),
                    ("Memory", "hidden xl:table-cell"),
                ),
                [
                    [
                        row["worker"],
                        row["queues"],
                        row["circuit"],
                        number(row["processed"]),
                        elapsed(float(row["uptime"])),
                        row["memory"] or "—",
                    ]
                    for row in processes
                ],
            ),
            note=""
            if local
            else "Workers run in other processes. Only what the queue backend "
            "reports is visible here.",
        )


class SchedulesPanel(Panel):
    """Cron entries, their next fire, and what happened last time."""

    id = "schedules"
    name = "Schedules"
    group = "Work"
    icon = "schedule"
    summary = "Cron entries, their next fire, and what happened last time."
    watcher = "schedules"

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the schedule view.

        Args:
            context: What the panel may read.

        Returns:
            Tiles and the schedule table.
        """
        watcher = context.watcher("schedules")
        jobs = watcher.jobs() if watcher else []
        stats = watcher.stats() if watcher else {}
        history = self._last_runs(context)
        failures = int(context.store.series["schedules"].errors())

        return Rendered(
            id=self.id,
            tiles=[
                tile(
                    "Schedules",
                    number(len(jobs)),
                    delta=f"{number(sum(1 for job in jobs if job['enabled']))} enabled",
                    tone=TONE_MUTED,
                ),
                tile(
                    "Due within the hour",
                    number(self._due_soon(jobs)),
                    delta="next fire",
                    tone=TONE_INFO,
                ),
                tile(
                    "Runs",
                    number(
                        stats.get("runs", int(context.store.count(EventKind.SCHEDULE)))
                    ),
                    delta="since start",
                    tone=TONE_GOOD,
                    spark=spark_of(context.store.series["schedules"]),
                ),
                tile(
                    "Failures",
                    number(failures),
                    delta="in the window",
                    tone=threshold_tone(failures, 1, 5),
                    spark=spark_of(context.store.series["schedules"]),
                ),
            ],
            table=table(
                columns(
                    "Task",
                    ("Expression", "hidden sm:table-cell"),
                    "Next fire",
                    ("Last run", "hidden md:table-cell"),
                    state_column("Outcome", "hidden lg:table-cell"),
                ),
                [
                    [
                        job["job"],
                        job["expression"] or "—",
                        _until(job["next_fire"]),
                        duration(history.get(job["job"], (0.0, ""))[0])
                        if job["job"] in history
                        else "—",
                        history.get(job["job"], (0.0, "—"))[1],
                    ]
                    for job in jobs
                ],
            ),
            note="" if jobs else "A scheduler is running and has no jobs registered.",
        )

    @staticmethod
    def _due_soon(jobs: list[dict[str, Any]]) -> int:
        """How many jobs fire within the next hour.

        Args:
            jobs: The schedule.

        Returns:
            The count.
        """
        horizon = time.time() + 3600
        return sum(
            1 for job in jobs if job["next_fire"] and job["next_fire"] <= horizon
        )

    @staticmethod
    def _last_runs(context: PanelContext) -> dict[str, tuple[float, str]]:
        """The most recent run of each scheduled job.

        Args:
            context: What the panel may read.

        Returns:
            The job's name, its duration and its outcome.
        """
        latest: dict[str, tuple[float, str]] = {}
        for event in context.store.all(EventKind.SCHEDULE):
            latest[event.job] = (event.duration_ms, event.outcome)
        return latest


def _until(timestamp: float | None) -> str:
    """How long until a moment.

    Args:
        timestamp: When it happens, or None.

    Returns:
        A relative time, or an em dash when the scheduler has not said.
    """
    if not timestamp:
        return "—"

    remaining = timestamp - time.time()
    return f"in {elapsed(remaining)}" if remaining > 0 else "due"
