"""
sillo_vise.cli.bench — ``vise bench``.

Rule three of the Foreman specification: *the cost is a published number*. Vise
on and vise off are separate rows, measured on the same machine against the same
application, and the README carries both.

The measurement is deliberately narrow. It drives the ASGI application directly
rather than over a socket, because a loopback TCP round trip is tens of
microseconds of noise around an overhead measured in single microseconds — the
framework's own benchmark got from 702.8µs to 27.1µs by deleting per-request
allocation, and a harness that could not see a 27µs difference would be
measuring the harness.

Three rows, and the middle one is the important one:

* **sillo alone** — the application with nothing around it.
* **recorder off** — vise installed with ``[recorder] enabled = false``. This
  should be indistinguishable from the first row, because nothing is wrapped.
  If it is not, "disabled means compiled out" is not true and this is how we
  find out.
* **recorder on** — everything running.
"""

from __future__ import annotations

import statistics
import time
from typing import Any, ClassVar

import anyio
from sillo.console import Command, Option

from ..config import (
    DashboardConfig,
    LogConfig,
    RecorderConfig,
    ServerConfig,
    ViseConfig,
)
from ..server import import_application
from ..server.install import install
from .discover import discover_target

__all__ = ["Bench"]

#: Requests run before measuring, to let import-time and first-call costs settle.
WARMUP = 200


class Bench(Command):
    """Measure what vise costs."""

    name = "bench"
    help = "Measure the per-request overhead vise adds, with the recorder on and off"
    description = (
        "Drives the ASGI application directly, three ways: bare, with vise "
        "installed and the recorder off, and with everything running. The "
        "middle row is the one that keeps 'disabled means compiled out' honest."
    )

    arguments: ClassVar[list] = [
        Option("app", help="Import string. Default: discovered"),
        Option("requests", short="n", type=int, default=2000, help="Requests per row"),
        Option("path", default="/", help="Path to request"),
    ]

    def handle(self) -> int | None:
        """Run the benchmark.

        Returns:
            The exit code.
        """
        target = self.option("app") or discover_target(_bare_config())
        if target is None:
            self.fail("No application found. Name one with --app.")

        count = max(100, int(self.option("requests")))
        path = self.option("path")

        rows = []
        for label, config in _scenarios():
            application = import_application(target)
            installation = install(application, config) if config else None

            try:
                timings = anyio.run(_measure, application, path, count)
            finally:
                if installation is not None:
                    installation.shutdown()

            rows.append((label, timings))

        self._report(rows, count, path)
        return 0

    def _report(
        self, rows: list[tuple[str, list[float]]], count: int, path: str
    ) -> None:
        """Print the table.

        Args:
            rows: Label and timings pairs.
            count: Requests per row.
            path: The path requested.
        """
        baseline = statistics.median(rows[0][1]) if rows else 0.0

        self.line(f"{count:,} requests to {path}, driven in-process")
        self.blank()

        self.table(
            ["scenario", "p50", "p95", "p99", "overhead"],
            [
                [
                    label,
                    _us(statistics.median(timings)),
                    _us(_percentile(timings, 0.95)),
                    _us(_percentile(timings, 0.99)),
                    _delta(statistics.median(timings) - baseline),
                ]
                for label, timings in rows
            ],
        )

        self.blank()
        self.muted(
            "  The middle row should be indistinguishable from the first. "
            "If it is not, vise is costing something while switched off."
        )


def _scenarios() -> list[tuple[str, ViseConfig | None]]:
    """The three configurations to measure.

    Returns:
        A label and the configuration for each, with None for the bare run.
    """
    quiet = LogConfig(banner=False, access=False)
    server = ServerConfig(reload=False)
    off_dashboard = DashboardConfig(enabled=False)

    return [
        ("sillo alone", None),
        (
            "vise, recorder off",
            ViseConfig(
                recorder=RecorderConfig(enabled=False),
                dashboard=off_dashboard,
                logs=quiet,
                server=server,
            ),
        ),
        (
            "vise, recorder on",
            ViseConfig(
                recorder=RecorderConfig(enabled=True),
                dashboard=off_dashboard,
                logs=quiet,
                server=server,
            ),
        ),
    ]


async def _measure(app: Any, path: str, count: int) -> list[float]:
    """Time *count* requests through *app*.

    Args:
        app: The ASGI application.
        path: The path to request.
        count: How many requests to time.

    Returns:
        Per-request durations, in seconds.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"bench"), (b"accept", b"*/*")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }

    async def receive() -> dict[str, Any]:
        """An empty body."""
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_: dict[str, Any]) -> None:
        """Discard the response."""

    async with Lifespan(app):
        for _ in range(WARMUP):
            await app(dict(scope), receive, send)

        timings = []
        for _ in range(count):
            started = time.perf_counter()
            await app(dict(scope), receive, send)
            timings.append(time.perf_counter() - started)

    return timings


class Lifespan:
    """Holds an application's lifespan open for the length of a block.

    ASGI's lifespan is *one* call that lives as long as the server does: the
    application awaits ``receive``, gets ``lifespan.startup``, replies, and then
    awaits again for ``lifespan.shutdown``. Calling it once per phase — which is
    how this was first written — leaves the application waiting for a shutdown
    that never arrives, and the benchmark hangs.

    Running it as a background task and messaging it is the only shape that
    works, and it matters here because the database, the scheduler and vise's
    own startup probe all hang off lifespan. A benchmark that skipped it would
    be measuring an application that had not finished starting.

    Attributes:
        app: The ASGI application.
    """

    def __init__(self, app: Any) -> None:
        """Prepare to run *app*'s lifespan.

        Args:
            app: The ASGI application.
        """
        self.app = app
        self._to_app: Any = None
        self._from_app: Any = None
        self._tasks: Any = None

    async def __aenter__(self) -> Lifespan:
        """Start the application.

        Returns:
            This, so the block can hold it.
        """
        self._to_app = anyio.create_memory_object_stream(4)
        self._from_app = anyio.create_memory_object_stream(4)

        self._tasks = anyio.create_task_group()
        await self._tasks.__aenter__()
        self._tasks.start_soon(self._run)

        await self._to_app[0].send({"type": "lifespan.startup"})
        with anyio.move_on_after(10):
            await self._from_app[1].receive()

        return self

    async def __aexit__(self, *exception: object) -> None:
        """Stop the application."""
        with anyio.move_on_after(10):
            await self._to_app[0].send({"type": "lifespan.shutdown"})
            await self._from_app[1].receive()

        self._tasks.cancel_scope.cancel()
        await self._tasks.__aexit__(None, None, None)

    async def _run(self) -> None:
        """Drive the application's lifespan call."""

        async def receive() -> Any:
            """The next message for the application."""
            return await self._to_app[1].receive()

        async def send(message: Any) -> None:
            """A reply from the application."""
            await self._from_app[0].send(message)

        try:
            await self.app(
                {"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send
            )
        except Exception:  # noqa: BLE001 - an application without lifespan is fine
            # Unblock anyone waiting on a reply that is never coming.
            with anyio.move_on_after(1):
                await self._from_app[0].send({"type": "lifespan.startup.complete"})


def _percentile(values: list[float], fraction: float) -> float:
    """An exact percentile over the measured values.

    Exact rather than sampled, unlike the dashboard's: a benchmark keeps every
    observation because it has a bounded number of them and one job.

    Args:
        values: The measurements.
        fraction: Between 0 and 1.

    Returns:
        The value at that percentile.
    """
    if not values:
        return 0.0

    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _us(seconds: float) -> str:
    """Render a duration in microseconds.

    Args:
        seconds: The duration.

    Returns:
        The duration as text.
    """
    return f"{seconds * 1_000_000:.1f}µs"


def _delta(seconds: float) -> str:
    """Render an overhead against the baseline.

    Args:
        seconds: The difference.

    Returns:
        The overhead as text, or a dash for the baseline row.
    """
    micros = seconds * 1_000_000
    if abs(micros) < 0.05:
        return "—"
    return f"{'+' if micros > 0 else ''}{micros:.1f}µs"


def _bare_config() -> ViseConfig:
    """A configuration used only for discovering the application.

    Returns:
        The defaults.
    """
    return ViseConfig()
