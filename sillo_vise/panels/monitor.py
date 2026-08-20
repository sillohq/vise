"""
sillo_vise.panels.monitor — throughput, requests, queries, cache and outgoing calls.

The five panels a person watches while they work. Each one reads the store and
renders; none of them collects, and none of them can be reached at all unless
its watcher is live.

Overview is the exception to that rule and the reason for it: it reads whichever
watchers happen to be live and shows only their rows. On a project with no
Redis, the health rail has no queue line, rather than a queue line reading
"unknown".
"""

from __future__ import annotations

from typing import Any

from ..logs.format import duration, number, size, truncate
from ..recorder import EventKind, fingerprint_sql, short_sql
from .base import Panel, PanelContext, Rendered, columns, table
from .tiles import (
    TONE_BAD,
    TONE_GOOD,
    TONE_INFO,
    TONE_MUTED,
    TONE_WARN,
    bytes_tile,
    duration_tile,
    rate_tile,
    spark_of,
    threshold_tone,
    tile,
    trend_tile,
)

__all__ = [
    "CachePanel",
    "OutgoingPanel",
    "OverviewPanel",
    "QueriesPanel",
    "RequestsPanel",
]

#: Bars a chart draws. Matches the mockups, and forty minutes of a
#: per-minute series is a readable window.
CHART_BARS = 40


class OverviewPanel(Panel):
    """Throughput, latency, failures and health, in one screen."""

    id = "overview"
    name = "Overview"
    group = "Monitor"
    icon = "grid"
    summary = "Throughput, latency, queue size, failure rate and health in one screen."
    watcher = None

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the overview.

        Args:
            context: What the panel may read.

        Returns:
            Tiles, a request chart, the slowest routes and a health rail.
        """
        series = context.store.series
        requests = series["requests"]
        failures = requests.errors(5)

        return Rendered(
            id=self.id,
            tiles=[
                rate_tile("Requests", requests),
                duration_tile("p95 duration", requests),
                self._exception_tile(context),
                tile(
                    "5xx / min",
                    number(failures / 5),
                    delta=f"{number(requests.total(5))} served",
                    tone=threshold_tone(failures, 1, 10),
                    spark=spark_of(series["exceptions"]),
                ),
            ],
            chart={
                "label": "Requests",
                "note": f"{CHART_BARS}m",
                "bars": requests.counts(CHART_BARS),
            },
            table=self._slowest(context),
            aside=self._health(context),
        )

    def _exception_tile(self, context: PanelContext) -> dict[str, Any]:
        """The exception count, as a tile.

        Args:
            context: What the panel may read.

        Returns:
            The tile.
        """
        series = context.store.series["exceptions"]
        groups = {
            event.fingerprint for event in context.store.all(EventKind.EXCEPTION)
        }
        return tile(
            "Exceptions",
            number(len(groups)),
            delta=f"{number(series.total())} raised",
            tone=TONE_BAD if groups else TONE_GOOD,
            spark=spark_of(series),
        )

    def _slowest(self, context: PanelContext) -> dict[str, Any]:
        """The five slowest routes.

        Args:
            context: What the panel may read.

        Returns:
            The table.
        """
        by_route: dict[tuple[str, str], list[float]] = {}
        for event in context.store.all(EventKind.REQUEST):
            key = (event.route or event.path, event.method)
            by_route.setdefault(key, []).append(event.duration_ms)

        ranked = sorted(
            by_route.items(),
            key=lambda item: max(item[1]),
            reverse=True,
        )[:5]

        return table(
            columns("Route", ("Method", "hidden sm:table-cell"), "Slowest", ("Calls", "hidden md:table-cell")),
            [
                [truncate(route, 44), method, duration(max(timings)), number(len(timings))]
                for (route, method), timings in ranked
            ],
        )

    def _health(self, context: PanelContext) -> dict[str, Any]:
        """One row per subsystem that is actually being watched.

        A project with no Redis gets no queue row, rather than a queue row
        reading "unknown". The rail is a list of what is here, not a checklist
        of what a different project might have had.

        Args:
            context: What the panel may read.

        Returns:
            The side rail.
        """
        rows = []
        for state in context.registry:
            if not state.live:
                continue
            rows.append([state.name, state.availability.detail, "healthy"])

        return {"label": "Watching", "note": f"{len(rows)}", "rows": rows}


class RequestsPanel(Panel):
    """Every request, and everything each one caused."""

    id = "requests"
    name = "Requests"
    group = "Monitor"
    icon = "route"
    summary = "Every request, and everything each one caused."
    watcher = None

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the request log.

        Args:
            context: What the panel may read.

        Returns:
            Tiles and the request table.
        """
        series = context.store.series
        requests = series["requests"]
        served = requests.total(5)
        failures = requests.errors(5)

        return Rendered(
            id=self.id,
            tiles=[
                rate_tile("Requests", requests),
                duration_tile("p95 duration", requests),
                tile(
                    "5xx rate",
                    f"{(failures / served * 100) if served else 0:.2f}%",
                    delta=f"{number(failures)} of {number(served)}",
                    tone=threshold_tone(failures, 1, 10),
                    spark=spark_of(requests),
                ),
                bytes_tile("Bytes out", series["requests.bytes"]),
            ],
            chart={
                "label": "Requests",
                "note": f"{CHART_BARS}m",
                "bars": requests.counts(CHART_BARS),
            },
            table=table(
                columns(
                    "Method",
                    "Path",
                    "Status",
                    "Duration",
                    ("Bytes", "hidden md:table-cell"),
                    ("Client", "hidden lg:table-cell"),
                ),
                [
                    [
                        event.method,
                        truncate(event.full_path, 48),
                        event.status,
                        duration(event.duration_ms),
                        size(event.response_bytes),
                        event.client or "—",
                    ]
                    for event in context.store.recent(EventKind.REQUEST, limit=40)
                ],
            ),
        )


class QueriesPanel(Panel):
    """SQL with its bindings, its duration, and what ran it."""

    id = "queries"
    name = "Queries"
    group = "Monitor"
    icon = "orm"
    summary = "SQL with its bindings, its duration, and the code that ran it."
    watcher = "queries"

    def badge(self, context: PanelContext) -> str:
        """How many N+1 groups are outstanding.

        Args:
            context: What the panel may read.

        Returns:
            The count, or an empty string when there are none.
        """
        found = len(self._n_plus_one(context))
        return str(found) if found else ""

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the query log.

        Args:
            context: What the panel may read.

        Returns:
            Tiles, a query chart, the query table and the N+1 rail.
        """
        series = context.store.series
        queries = series["queries"]
        requests = series["requests"].total(5)
        repeated = self._n_plus_one(context)

        return Rendered(
            id=self.id,
            tiles=[
                tile(
                    "Queries / request",
                    f"{queries.total(5) / requests:.1f}" if requests else "—",
                    delta=f"{number(queries.total(5))} in 5m",
                    tone=TONE_MUTED,
                    spark=spark_of(queries),
                ),
                duration_tile("p95 duration", queries),
                tile(
                    "N+1 groups",
                    number(len(repeated)),
                    delta="repeated in one request",
                    tone=TONE_BAD if repeated else TONE_GOOD,
                    spark=spark_of(queries),
                ),
                tile(
                    "Slow queries",
                    number(queries.errors()),
                    delta=f"over {context.config.recorder.slow_query_ms}ms",
                    tone=threshold_tone(queries.errors(5), 1, 20),
                    spark=spark_of(queries),
                ),
            ],
            chart={
                "label": "Queries",
                "note": f"{CHART_BARS}m",
                "bars": queries.counts(CHART_BARS),
            },
            table=table(
                columns(
                    "Statement",
                    "Duration",
                    ("Rows", "hidden md:table-cell"),
                    ("Connection", "hidden lg:table-cell"),
                ),
                [
                    [
                        short_sql(event.sql, 70),
                        duration(event.duration_ms),
                        number(event.rows),
                        event.connection,
                    ]
                    for event in context.store.recent(EventKind.QUERY, limit=40)
                ],
            ),
            aside={
                "label": "Repeated",
                "note": f"{len(repeated)}",
                "rows": [
                    [short_sql(sql, 34), f"{count} times in one request", "degraded"]
                    for sql, count in repeated[:6]
                ],
            }
            if repeated
            else None,
        )

    @staticmethod
    def _n_plus_one(context: PanelContext) -> list[tuple[str, int]]:
        """Statements repeated enough times inside one request to be an N+1.

        Grouping is per request rather than globally. The same statement run
        once by each of a thousand requests is a hot query; run a thousand
        times by *one* request it is a missing join, and only the second is
        something to fix.

        Args:
            context: What the panel may read.

        Returns:
            The statement and its repeat count, worst first.
        """
        per_request: dict[tuple[str | None, str], int] = {}
        examples: dict[str, str] = {}

        for event in context.store.all(EventKind.QUERY):
            shape = event.fingerprint or fingerprint_sql(event.sql)
            per_request[(event.request_id, shape)] = (
                per_request.get((event.request_id, shape), 0) + 1
            )
            examples.setdefault(shape, event.sql)

        worst: dict[str, int] = {}
        for (request_id, shape), count in per_request.items():
            if request_id and count > 5 and count > worst.get(shape, 0):
                worst[shape] = count

        return sorted(
            ((examples[shape], count) for shape, count in worst.items()),
            key=lambda item: item[1],
            reverse=True,
        )


class CachePanel(Panel):
    """Hit ratio, hot keys, and what was read on every request."""

    id = "cache"
    name = "Cache"
    group = "Monitor"
    icon = "cache"
    summary = "Hit ratio, hot keys, and what was read on every request."
    watcher = "cache"

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the cache log.

        Args:
            context: What the panel may read.

        Returns:
            Tiles, the operation table and the hot-key rail.
        """
        series = context.store.series
        hits = series["cache.hit"].total()
        misses = series["cache.miss"].total()
        looked = hits + misses
        ratio = (hits / looked * 100) if looked else 0.0

        return Rendered(
            id=self.id,
            tiles=[
                tile(
                    "Hit ratio",
                    f"{ratio:.1f}%" if looked else "—",
                    delta=f"{number(hits)} of {number(looked)}",
                    tone=threshold_tone(100 - ratio, 20, 50) if looked else TONE_MUTED,
                    spark=spark_of(series["cache.hit"]),
                ),
                rate_tile("Reads", series["cache"]),
                tile(
                    "Misses",
                    number(misses),
                    delta="in the window",
                    tone=TONE_MUTED,
                    spark=spark_of(series["cache.miss"]),
                ),
                duration_tile("p95 duration", series["cache"]),
            ],
            chart={
                "label": "Cache reads",
                "note": f"{CHART_BARS}m",
                "bars": series["cache"].counts(CHART_BARS),
            },
            table=table(
                columns(
                    "Key",
                    ("Operation", "hidden sm:table-cell"),
                    "Result",
                    ("TTL", "hidden md:table-cell"),
                    ("Duration", "hidden lg:table-cell"),
                ),
                [
                    [
                        truncate(event.key, 40),
                        event.operation,
                        event.result,
                        f"{event.ttl}s" if event.ttl else "—",
                        duration(event.duration_ms),
                    ]
                    for event in context.store.recent(EventKind.CACHE, limit=40)
                ],
            ),
            aside=self._hot_keys(context),
        )

    @staticmethod
    def _hot_keys(context: PanelContext) -> dict[str, Any]:
        """The most-missed key prefixes.

        Most-missed rather than most-read: a prefix being read often is the
        cache working, and a prefix missing often is the cache not working,
        and only one of those is worth six rows of a side rail.

        Args:
            context: What the panel may read.

        Returns:
            The side rail.
        """
        missed: dict[str, int] = {}
        for event in context.store.all(EventKind.CACHE):
            if event.result == "miss":
                missed[event.prefix] = missed.get(event.prefix, 0) + 1

        ranked = sorted(missed.items(), key=lambda item: item[1], reverse=True)[:6]

        return {
            "label": "Most missed",
            "note": f"{len(missed)}",
            "rows": [[prefix, f"{count} misses", "degraded"] for prefix, count in ranked],
        }


class OutgoingPanel(Panel):
    """Every call the application made to somebody else."""

    id = "outgoing"
    name = "Outgoing"
    group = "Monitor"
    icon = "outbound"
    summary = "Every call the application made to somebody else."
    watcher = "outgoing"

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the outgoing log.

        Args:
            context: What the panel may read.

        Returns:
            Tiles, the call table and the per-host rail.
        """
        series = context.store.series["outgoing"]
        calls = series.total()
        failed = series.errors()

        return Rendered(
            id=self.id,
            tiles=[
                rate_tile("Calls", series),
                duration_tile("p95 duration", series),
                tile(
                    "Error rate",
                    f"{(failed / calls * 100) if calls else 0:.1f}%",
                    delta=f"{number(failed)} of {number(calls)}",
                    tone=threshold_tone(failed, 1, 10),
                    spark=spark_of(series),
                ),
                tile(
                    "Retries",
                    number(
                        sum(event.retries for event in context.store.all(EventKind.OUTGOING))
                    ),
                    delta="in the window",
                    tone=TONE_INFO,
                    spark=spark_of(series),
                ),
            ],
            chart={
                "label": "Outgoing calls",
                "note": f"{CHART_BARS}m",
                "bars": series.counts(CHART_BARS),
            },
            table=table(
                columns(
                    "Method",
                    "URL",
                    "Status",
                    ("Duration", "hidden md:table-cell"),
                    ("Retries", "hidden lg:table-cell"),
                ),
                [
                    [
                        event.method,
                        truncate(event.url, 46),
                        event.status or event.error or "—",
                        duration(event.duration_ms),
                        number(event.retries),
                    ]
                    for event in context.store.recent(EventKind.OUTGOING, limit=40)
                ],
            ),
            aside=self._hosts(context),
        )

    @staticmethod
    def _hosts(context: PanelContext) -> dict[str, Any]:
        """Volume and error rate per host.

        Args:
            context: What the panel may read.

        Returns:
            The side rail.
        """
        counts: dict[str, list[int]] = {}
        for event in context.store.all(EventKind.OUTGOING):
            row = counts.setdefault(event.host or "—", [0, 0])
            row[0] += 1
            if event.error or event.status >= 400:
                row[1] += 1

        ranked = sorted(counts.items(), key=lambda item: item[1][0], reverse=True)[:6]

        return {
            "label": "Hosts",
            "note": f"{len(counts)}",
            "rows": [
                [
                    host,
                    f"{total} calls",
                    "healthy" if not failed else "degraded" if failed < total else "stalled",
                ]
                for host, (total, failed) in ranked
            ],
        }
