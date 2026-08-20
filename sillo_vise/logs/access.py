"""
sillo_vise.logs.access — one line per request, aligned so a column can be scanned.

uvicorn writes ``INFO:     127.0.0.1:54118 - "GET /api/v1/documents HTTP/1.1" 200 OK``.
Everything on that line is true and almost none of it is what a person watching
a development server wants: the level is always INFO, the protocol is always
HTTP/1.1, the reason phrase restates the code, and the two facts that actually
matter — how long it took and how much came back — are not there at all,
because uvicorn's access logger does not measure them.

Vise writes this instead::

    09:14:22  GET   /api/v1/documents           200    38ms   12.4 kB
    09:14:23  GET   /api/v1/insights            503   2.10s       84 B  slow

Fixed columns, so status codes line up and a 5xx is visible without reading;
duration and size, because the recorder already measured both; and a marker on
anything past the slow threshold. The line is written *from the recorder's
event*, not from a second measurement — one thing measures a request, and the
log is a reader of it.
"""

from __future__ import annotations

import json
import sys
import time
from typing import IO

from sillo.console.style import Palette

from ..recorder import RequestEvent
from .format import duration, size, truncate
from .theme import BYTES, DIM, DURATION, METHOD, PATH, SLOW, TIMESTAMP, status_style

__all__ = ["AccessLog"]

#: Column widths. The method column fits DELETE and OPTIONS; the path column is
#: the one that gives, because it is the only field whose length carries
#: information worth keeping.
_METHOD_WIDTH = 6
_PATH_WIDTH = 44
_STATUS_WIDTH = 3
_DURATION_WIDTH = 7
_SIZE_WIDTH = 9


class AccessLog:
    """Writes one line per request.

    Attributes:
        stream: Where lines go.
        palette: Decides whether those lines carry colour.
        show_query: Include the query string, redacted, after the path.
        static: Write lines for the dashboard's own asset requests.
        dashboard_path: The prefix those requests arrive under.
    """

    __slots__ = ("stream", "palette", "show_query", "static", "dashboard_path", "_json")

    def __init__(
        self,
        stream: IO[str] | None = None,
        *,
        palette: Palette | None = None,
        show_query: bool = True,
        static: bool = False,
        dashboard_path: str = "",
        json: bool = False,
    ) -> None:
        """Build an access log.

        Args:
            stream: Where lines go. Defaults to stdout — access lines are the
                server's ordinary output, not its diagnostics, so they should
                survive ``2>/dev/null`` and should be pipeable.
            palette: Colour decision for the stream. Built from it otherwise.
            show_query: Include the redacted query string.
            static: Write lines for the dashboard's own requests.
            dashboard_path: Prefix the dashboard is mounted under, so its
                requests can be recognised.
            json: Emit one object per line instead of a formatted line.
        """
        self.stream = stream or sys.stdout
        self.palette = palette or Palette(self.stream)
        self.show_query = show_query
        self.static = static
        self.dashboard_path = dashboard_path
        self._json = json

    def write(self, event: RequestEvent) -> None:
        """Write the line for one request.

        Args:
            event: The recorded request.
        """
        if not self.static and self.dashboard_path and event.path.startswith(self.dashboard_path):
            return

        self.stream.write(f"{self.render(event)}\n")
        self.stream.flush()

    def render(self, event: RequestEvent) -> str:
        """Build the line for one request.

        Separated from :meth:`write` so tests can assert on the text without
        capturing a stream, and so the same rendering can be reused by
        anything that wants a request on one line.

        Args:
            event: The recorded request.

        Returns:
            The line, without its newline.
        """
        if self._json:
            return self._as_json(event)

        paint = self.palette.render
        path = event.full_path if self.show_query else event.path

        parts = [
            paint(time.strftime("%H:%M:%S", time.localtime(event.at)), TIMESTAMP),
            paint(event.method.ljust(_METHOD_WIDTH), METHOD),
            paint(truncate(path, _PATH_WIDTH).ljust(_PATH_WIDTH), PATH),
            paint(str(event.status).rjust(_STATUS_WIDTH), status_style(event.status)),
            paint(duration(event.duration_ms).rjust(_DURATION_WIDTH), DURATION),
            paint(size(event.response_bytes).rjust(_SIZE_WIDTH), BYTES),
        ]

        line = "  " + "  ".join(parts)

        if event.slow:
            line += "  " + paint("slow", SLOW)
        elif event.route:
            line += "  " + paint(event.route, DIM)

        return line

    def _as_json(self, event: RequestEvent) -> str:
        """Render one request as a single JSON object.

        Hand-built rather than passed through ``json.dumps`` on the event's
        full dictionary, because the whole point of the JSON style is a stable,
        narrow schema for a collector — dumping every field the recorder holds
        would put headers and bodies into the log, which is the one place they
        were carefully kept out of.

        Args:
            event: The recorded request.

        Returns:
            One line of JSON.
        """
        return json.dumps(
            {
                "at": round(event.at, 3),
                "method": event.method,
                "path": event.path,
                "query": event.query,
                "status": event.status,
                "duration_ms": round(event.duration_ms, 2),
                "bytes": event.response_bytes,
                "route": event.route,
                "request_id": event.request_id,
                "slow": event.slow,
            },
            separators=(",", ":"),
        )
