"""
sillo_vise.panels.diagnose — exceptions, logs, real-time and mail.

The four panels a person opens when something is wrong. Two of them are always
available, because every application raises and logs; two appear only when the
project actually has the subsystem.

Exceptions is the only panel that groups rather than lists. Four hundred
occurrences of one ``OperationalError`` is one problem, and a table of four
hundred rows is a table nobody reads — so the rows are groups, with a count, a
first-seen and a last-seen, and the individual occurrences hang off the group.
"""

from __future__ import annotations

import time
from typing import Any

from ..logs.format import elapsed, number, size, truncate
from ..recorder import EventKind
from .base import Panel, PanelContext, Rendered, columns, table
from .tiles import (
    TONE_BAD,
    TONE_GOOD,
    TONE_INFO,
    TONE_MUTED,
    TONE_WARN,
    rate_tile,
    spark_of,
    threshold_tone,
    tile,
)

__all__ = ["ExceptionsPanel", "LogsPanel", "MailPanel", "RealtimePanel"]

#: Bars a chart draws.
CHART_BARS = 40


class ExceptionsPanel(Panel):
    """Grouped by type and location, with the request that raised them."""

    id = "exceptions"
    name = "Exceptions"
    group = "Diagnose"
    icon = "alert"
    summary = "Grouped by type and location, with the request that raised them."
    watcher = "exceptions"

    def badge(self, context: PanelContext) -> str:
        """How many distinct exception groups there are.

        Args:
            context: What the panel may read.

        Returns:
            The count, or an empty string when there are none.
        """
        groups = len(self._grouped(context))
        return str(groups) if groups else ""

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the exception view.

        Args:
            context: What the panel may read.

        Returns:
            Tiles, a chart and the grouped table.
        """
        groups = self._grouped(context)
        series = context.store.series["exceptions"]
        occurrences = int(series.total())
        unhandled = len(
            [
                event
                for event in context.store.all(EventKind.EXCEPTION)
                if not event.handled
            ]
        )

        return Rendered(
            id=self.id,
            tiles=[
                tile(
                    "Groups",
                    number(len(groups)),
                    delta="distinct",
                    tone=TONE_BAD if groups else TONE_GOOD,
                    spark=spark_of(series),
                ),
                tile(
                    "Occurrences",
                    number(occurrences),
                    delta="since start",
                    tone=threshold_tone(occurrences, 1, 100),
                    spark=spark_of(series),
                ),
                tile(
                    "Unhandled",
                    number(unhandled),
                    delta="reached the client",
                    tone=TONE_BAD if unhandled else TONE_GOOD,
                    spark=spark_of(series),
                ),
                rate_tile("Raised", series),
            ],
            chart={
                "label": "Exceptions",
                "note": f"{CHART_BARS}m",
                "bars": series.counts(CHART_BARS),
            },
            table=table(
                columns(
                    "Exception",
                    ("Raised in", "hidden md:table-cell"),
                    "Count",
                    ("Last seen", "hidden sm:table-cell"),
                    ("State", "hidden lg:table-cell"),
                ),
                [
                    [
                        group["type"],
                        truncate(group["where"], 40),
                        number(group["count"]),
                        f"{elapsed(time.time() - group['last_seen'])} ago",
                        "handled" if group["handled"] else "open",
                    ]
                    for group in groups
                ],
            ),
            note="" if groups else "Nothing has raised since vise started.",
        )

    @staticmethod
    def _grouped(context: PanelContext) -> list[dict[str, Any]]:
        """Exceptions collapsed into groups, worst first.

        Args:
            context: What the panel may read.

        Returns:
            One entry per fingerprint.
        """
        groups: dict[str, dict[str, Any]] = {}

        for event in context.store.all(EventKind.EXCEPTION):
            group = groups.get(event.fingerprint)
            if group is None:
                groups[event.fingerprint] = {
                    "fingerprint": event.fingerprint,
                    "type": event.type,
                    "where": event.where,
                    "message": event.message,
                    "count": 1,
                    "first_seen": event.at,
                    "last_seen": event.at,
                    "handled": event.handled,
                }
                continue

            group["count"] += 1
            group["last_seen"] = event.at
            # A group is only "handled" when every occurrence was. One that
            # reached a client is the fact worth surfacing.
            group["handled"] = group["handled"] and event.handled

        return sorted(groups.values(), key=lambda group: group["count"], reverse=True)


class LogsPanel(Panel):
    """A live tail that knows which request each line belongs to."""

    id = "logs"
    name = "Logs"
    group = "Diagnose"
    icon = "terminal"
    summary = "A live tail that knows which request each line belongs to."
    watcher = "logs"

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the log tail.

        Args:
            context: What the panel may read.

        Returns:
            Tiles, a chart and the tail.
        """
        series = context.store.series

        return Rendered(
            id=self.id,
            tiles=[
                rate_tile("Lines", series["logs"]),
                tile(
                    "Errors",
                    number(int(series["logs.error"].total())),
                    delta="in the window",
                    tone=threshold_tone(series["logs.error"].total(), 1, 20),
                    spark=spark_of(series["logs.error"]),
                ),
                tile(
                    "Warnings",
                    number(int(series["logs.warning"].total())),
                    delta="in the window",
                    tone=TONE_WARN if series["logs.warning"].total() else TONE_GOOD,
                    spark=spark_of(series["logs.warning"]),
                ),
                tile(
                    "Retained",
                    number(context.store.retained(EventKind.LOG)),
                    delta=f"of {number(context.config.recorder.buffer)}",
                    tone=TONE_MUTED,
                    spark=spark_of(series["logs"]),
                ),
            ],
            chart={
                "label": "Log lines",
                "note": f"{CHART_BARS}m",
                "bars": series["logs"].counts(CHART_BARS),
            },
            table=table(
                columns(
                    "Level",
                    "Message",
                    ("Logger", "hidden md:table-cell"),
                    ("Route", "hidden lg:table-cell"),
                ),
                [
                    [
                        event.level,
                        truncate(event.message, 72),
                        event.logger,
                        event.route or "—",
                    ]
                    for event in context.store.recent(EventKind.LOG, limit=60)
                ],
            ),
        )


class RealtimePanel(Panel):
    """Websocket connections, channels, and event flow."""

    id = "realtime"
    name = "Real-time"
    group = "Diagnose"
    icon = "realtime"
    summary = "Websocket connections, channels, and event flow."
    watcher = "realtime"

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the real-time view.

        Args:
            context: What the panel may read.

        Returns:
            Tiles, a message chart, the channel table and the event rail.
        """
        series = context.store.series["websocket"]
        channels = self._channels(context)
        open_now = sum(1 for state in channels.values() if state["open"] > 0)

        return Rendered(
            id=self.id,
            tiles=[
                tile(
                    "Connections",
                    number(sum(state["open"] for state in channels.values())),
                    delta=f"{number(open_now)} channels",
                    tone=TONE_INFO if open_now else TONE_MUTED,
                    spark=spark_of(series),
                ),
                rate_tile("Messages", series),
                tile(
                    "Bytes",
                    size(int(series.sum())),
                    delta="in the window",
                    tone=TONE_MUTED,
                    spark=spark_of(series),
                ),
                tile(
                    "Events",
                    number(context.store.count(EventKind.SIGNAL)),
                    delta=f"{number(int(context.store.series['signals'].errors()))} failed",
                    tone=TONE_MUTED,
                    spark=spark_of(context.store.series["signals"]),
                ),
            ],
            chart={
                "label": "Messages",
                "note": f"{CHART_BARS}m",
                "bars": series.counts(CHART_BARS),
            },
            table=table(
                columns(
                    "Channel",
                    "Open",
                    ("Messages", "hidden md:table-cell"),
                    ("Bytes", "hidden sm:table-cell"),
                    ("State", "hidden lg:table-cell"),
                ),
                [
                    [
                        truncate(channel, 40),
                        number(state["open"]),
                        number(state["messages"]),
                        size(state["bytes"]),
                        "healthy" if state["open"] else "stalled",
                    ]
                    for channel, state in sorted(channels.items())
                ],
            ),
            aside=self._events(context),
        )

    @staticmethod
    def _channels(context: PanelContext) -> dict[str, dict[str, int]]:
        """Per-channel connection and message counts.

        Args:
            context: What the panel may read.

        Returns:
            The channel's name to its counts.
        """
        channels: dict[str, dict[str, int]] = {}

        for event in context.store.all(EventKind.WEBSOCKET):
            state = channels.setdefault(
                event.channel, {"open": 0, "messages": 0, "bytes": 0}
            )
            if event.action == "connect":
                state["open"] += 1
            elif event.action == "closed":
                state["open"] = max(0, state["open"] - 1)
            elif event.action in {"send", "receive"}:
                state["messages"] += 1
                state["bytes"] += event.bytes

        return channels

    @staticmethod
    def _events(context: PanelContext) -> dict[str, Any]:
        """Application events, by name.

        Args:
            context: What the panel may read.

        Returns:
            The side rail.
        """
        counts: dict[str, list[int]] = {}
        for event in context.store.all(EventKind.SIGNAL):
            row = counts.setdefault(event.name, [0, 0])
            row[0] += 1
            row[1] += event.failures

        ranked = sorted(counts.items(), key=lambda item: item[1][0], reverse=True)[:6]

        return {
            "label": "Events",
            "note": f"{len(counts)}",
            "rows": [
                [name, f"{total} emitted", "degraded" if failed else "healthy"]
                for name, (total, failed) in ranked
            ],
        }


class MailPanel(Panel):
    """Everything sent or suppressed, and what happened to it."""

    id = "mail"
    name = "Mail"
    group = "Diagnose"
    icon = "mail"
    summary = "Everything sent or suppressed, listed rather than discarded."
    watcher = "mail"

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the mail view.

        Args:
            context: What the panel may read.

        Returns:
            Tiles and the message table.
        """
        messages = context.store.all(EventKind.MAIL)
        counts = {status: 0 for status in ("completed", "suppressed", "failed")}
        for event in messages:
            counts[event.status] = counts.get(event.status, 0) + 1

        return Rendered(
            id=self.id,
            tiles=[
                tile(
                    "Sent",
                    number(counts.get("completed", 0)),
                    delta="since start",
                    tone=TONE_GOOD,
                    spark=spark_of(context.store.series["mail"]),
                ),
                tile(
                    "Suppressed",
                    number(counts.get("suppressed", 0)),
                    delta="listed, not discarded",
                    tone=TONE_INFO,
                    spark=spark_of(context.store.series["mail"]),
                ),
                tile(
                    "Failed",
                    number(counts.get("failed", 0)),
                    delta="since start",
                    tone=threshold_tone(counts.get("failed", 0), 1, 5),
                    spark=spark_of(context.store.series["mail"]),
                ),
                rate_tile("Messages", context.store.series["mail"]),
            ],
            table=table(
                columns(
                    "To",
                    "Subject",
                    ("Template", "hidden md:table-cell"),
                    "Status",
                    ("Mailer", "hidden lg:table-cell"),
                ),
                [
                    [
                        truncate(event.to, 32),
                        truncate(event.subject, 40),
                        event.template or "—",
                        event.status,
                        event.mailer,
                    ]
                    for event in context.store.recent(EventKind.MAIL, limit=40)
                ],
            ),
            note="" if messages else "No mail has been sent since vise started.",
        )
