"""
sillo_vise.dashboard.stream — the live feed, as server-sent events.

SSE rather than a websocket, for three reasons that all point the same way. The
traffic is one-directional, so half a websocket would go unused. It reconnects
by itself, which matters for a dev server that restarts every time a file is
saved. And it is one ``EventSource`` in the browser with no library.

The stream sends *panel snapshots*, not raw events. A browser receiving raw
events would have to reimplement every panel to know what to do with them, and
then there would be two implementations of "what is the p95" that could
disagree. So the server renders and the browser draws.

A slow reader costs only itself. The subscription's buffer drops from the front
when it fills, and the drop count is sent along so the interface can say it
missed some rather than quietly showing a gap.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import anyio

from ..lifecycle import is_shutting_down
from ..panels import PanelRegistry
from ..recorder import Recorder

__all__ = ["EventStream", "sse"]

#: Sent when nothing has happened, to keep proxies from closing an idle
#: connection. A comment line, which EventSource ignores.
HEARTBEAT = b": heartbeat\n\n"

#: Longest a connection is held open. Browsers reconnect an EventSource by
#: themselves, and a connection that lives forever is a connection that
#: survives a reload it should not have survived.
MAX_SECONDS = 600.0


def sse(event: str, data: Any, *, retry: int | None = None) -> bytes:
    """Frame one server-sent event.

    Args:
        event: The event's name, which the browser listens for.
        data: The payload, serialised as JSON.
        retry: Reconnection delay to suggest, in milliseconds.

    Returns:
        The framed bytes.
    """
    body = json.dumps(data, separators=(",", ":"), default=str)
    head = f"retry: {retry}\n" if retry else ""
    return f"{head}event: {event}\ndata: {body}\n\n".encode()


class EventStream:
    """Renders panel snapshots to a connected browser.

    Attributes:
        panels: The panel registry, which decides what exists.
        recorder: The recorder, for its store's live feed.
        interval: Seconds between snapshots.
    """

    __slots__ = ("panels", "recorder", "interval")

    def __init__(
        self, panels: PanelRegistry, recorder: Recorder, interval: float = 2.0
    ) -> None:
        """Build a stream.

        Args:
            panels: The panel registry.
            recorder: The recorder.
            interval: Seconds between snapshots.
        """
        self.panels = panels
        self.recorder = recorder
        self.interval = max(0.25, interval)

    async def frames(self, panel_id: str) -> AsyncIterator[bytes]:
        """Yield frames for one open connection.

        Args:
            panel_id: The panel the browser is showing. It follows the reader
                rather than the server pushing all fourteen, because rendering
                a panel nobody is looking at is work nobody asked for.

        Yields:
            Framed bytes.
        """
        subscription = self.recorder.store.subscribe()
        opened = time.monotonic()

        try:
            yield sse("open", {"panel": panel_id}, retry=int(self.interval * 1000))

            while time.monotonic() - opened < MAX_SECONDS:
                await anyio.sleep(self.interval)

                # Checked every time round rather than only at the deadline.
                # Uvicorn will not exit until every connection closes, and
                # without this one the browser's stream would sit for the rest
                # of its ten minutes while Ctrl-C appeared to be ignored.
                if is_shutting_down():
                    yield sse("closing", {"panel": panel_id})
                    return

                dropped = subscription.dropped
                subscription.drain()

                rendered = self.panels.build(panel_id)
                if rendered is None:
                    # The panel stopped existing while the browser was looking
                    # at it — a queue backend went away, say. Saying so lets
                    # the interface move rather than poll a dead id forever.
                    yield sse("gone", {"panel": panel_id})
                    return

                yield sse(
                    "panel",
                    {
                        "panel": panel_id,
                        "at": time.time(),
                        "dropped": dropped,
                        **rendered.to_dict(),
                    },
                )

                yield sse("sidebar", {"groups": self.panels.sidebar()})
        finally:
            self.recorder.store.unsubscribe(subscription)
