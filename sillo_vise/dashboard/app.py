"""
sillo_vise.dashboard.app — the dashboard, as one raw ASGI middleware.

It is a middleware rather than a mounted router, and that is not a stylistic
choice. sillo's mounted routers claim their whole prefix subtree, and a router
mounted at the wrong moment can shadow routes registered later during startup —
which is a documented way to break an application, and an unacceptable thing for
an observability tool to do to the thing it is observing.

As a middleware it does one comparison: does the path start with the dashboard's
prefix? If not it delegates, and the application behaves exactly as though vise
were not installed.

It is registered *last*, so it runs *first* — sillo builds the chain inside-out.
That puts it outside the request recorder, which is why the dashboard's own
requests never reach the recorder at all rather than being recorded and then
filtered out of every chart.

Everything is written by hand rather than through the framework's ``Response``
objects. The dashboard must keep working while somebody is debugging the
application it is mounted in, so it depends on as little of that application as
possible.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, MutableMapping

from ..config import ViseConfig
from ..panels import PanelRegistry
from ..recorder import Recorder
from ..watchers import WatcherRegistry
from .api import DashboardAPI
from .assets import Assets
from .security import AccessGate
from .stream import EventStream, sse

__all__ = ["Dashboard"]

logger = logging.getLogger("sillo_vise")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

#: Headers on every response. The dashboard renders data captured from a live
#: application, so it says plainly that it must not be framed, sniffed or
#: indexed.
BASE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-robots-tag", b"noindex, nofollow"),
)


class Dashboard:
    """Raw ASGI middleware serving Foreman under its prefix.

    Attributes:
        app: The application this wraps.
        prefix: Where the dashboard answers.
        gate: Who is admitted.
        api: The JSON endpoints.
        assets: The built interface.
        stream: The live feed.
    """

    __slots__ = ("app", "prefix", "gate", "api", "assets", "stream", "config")

    def __init__(
        self,
        app: Any,
        config: ViseConfig,
        recorder: Recorder,
        watchers: WatcherRegistry,
        panels: PanelRegistry | None = None,
        target: Any = None,
    ) -> None:
        """Wrap *app*.

        Args:
            app: The next ASGI application.
            config: The resolved configuration.
            recorder: The recorder.
            watchers: The watcher registry, re-probed as the dashboard is used.
            panels: The panel registry. Built from the others when absent.
            target: The application being watched, for the panels that read it.
        """
        self.app = app
        self.config = config
        self.prefix = config.dashboard.path.rstrip("/") or "/__sillo/foreman"

        self.gate = AccessGate(config.dashboard.access, config.dashboard.token)
        registry = panels or PanelRegistry(watchers, recorder, config, target)

        self.api = DashboardAPI(registry, recorder, config, target)
        self.assets = Assets()
        self.stream = EventStream(registry, recorder, config.panels.refresh_ms / 1000)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI event.

        Args:
            scope: The connection scope.
            receive: The receive channel.
            send: The send channel.
        """
        if scope["type"] != "http" or not _under(scope.get("path", ""), self.prefix):
            await self.app(scope, receive, send)
            return

        if not self.gate.allows(scope):
            await _json(send, 403, {"error": "The vise dashboard refused this request."})
            return

        # Re-probe on the way past. This is what lets the Queues panel appear
        # when Redis comes up, without a restart and without a background task
        # running on an idle server.
        self.api.panels.watchers.probe_if_due()

        rest = scope["path"][len(self.prefix) :] or "/"

        try:
            await self._route(rest, scope, receive, send)
        except Exception as error:  # noqa: BLE001 - the dashboard must not take
            # the application down with it. The one place a traceback here
            # would matter is the log, so it goes there.
            logger.exception("vise: the dashboard failed serving %s", rest)
            await _json(send, 500, {"error": f"{type(error).__name__}: {error}"})

    async def _route(self, path: str, scope: Scope, receive: Receive, send: Send) -> None:
        """Dispatch one dashboard request.

        Written as a chain of comparisons rather than a route table, because
        there are eight of them and a table would be more machinery than the
        thing it dispatches.

        Args:
            path: The path after the prefix.
            scope: The connection scope.
            receive: The receive channel.
            send: The send channel.
        """
        method = scope.get("method", "GET")

        if path == "":
            # The built index references its assets relatively — `./assets/…`
            # — because the mount point is configurable and an absolute base
            # would bake one path into the bundle. Relative only resolves
            # correctly under a trailing slash: opened at `/__sillo/foreman`,
            # the browser would ask for `/__sillo/assets/…` and get nothing.
            await _redirect(send, f"{self.prefix}/")
            return

        if path == "/":
            body, kind, cache = self.assets.index(self.prefix)
            await _bytes(send, 200, body, kind, cache)
            return

        if path == "/api/meta":
            await _json(send, 200, self.api.meta())
            return

        if path.startswith("/api/panels/"):
            panel = self.api.panel(path[len("/api/panels/") :])
            await _json(send, 200 if panel else 404, panel or {"error": "No such panel."})
            return

        if path.startswith("/api/requests/"):
            detail = self.api.request(path[len("/api/requests/") :])
            await _json(
                send,
                200 if detail else 404,
                detail or {"error": "That request is no longer retained."},
            )
            return

        if path.startswith("/api/events/"):
            events = self.api.events(path[len("/api/events/") :], _limit(scope))
            await _json(send, 200 if events else 404, events or {"error": "No such kind."})
            return

        if path.startswith("/api/actions/") and method == "POST":
            result = self.api.action(path[len("/api/actions/") :])
            await _json(send, 200 if result else 404, result or {"error": "No such action."})
            return

        if path == "/api/stream":
            await self._sse(scope, send)
            return

        asset = self.assets.find(path)
        if asset is not None:
            await _bytes(send, 200, *asset)
            return

        # An unknown path under the prefix is the interface's own client-side
        # routing, so it gets the index and the browser sorts it out. An
        # unknown path under /api is a mistake and says so.
        if path.startswith("/api/"):
            await _json(send, 404, {"error": "No such endpoint."})
            return

        body, kind, cache = self.assets.index(self.prefix)
        await _bytes(send, 200, body, kind, cache)

    async def _sse(self, scope: Scope, send: Send) -> None:
        """Stream panel snapshots until the browser goes away.

        Args:
            scope: The connection scope.
            send: The send channel.
        """
        panel = _query(scope, "panel") or self.api.panels.initial()

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-store"),
                    # Nginx buffers proxied responses by default, which turns a
                    # live stream into a stream that arrives all at once when
                    # the connection closes.
                    (b"x-accel-buffering", b"no"),
                    *BASE_HEADERS,
                ],
            }
        )

        try:
            async for frame in self.stream.frames(panel):
                await send({"type": "http.response.body", "body": frame, "more_body": True})
        except Exception:  # noqa: BLE001 - the browser went away mid-frame
            pass

        await send({"type": "http.response.body", "body": b"", "more_body": False})


def _under(path: str, prefix: str) -> bool:
    """Whether a path belongs to the dashboard.

    Checked as an exact match or a match followed by ``/``, so a prefix of
    ``/__sillo/foreman`` does not claim an application's own
    ``/__sillo/foremanager``.

    Args:
        path: The request's path.
        prefix: The dashboard's prefix.

    Returns:
        True when the dashboard should answer.
    """
    return path == prefix or path.startswith(f"{prefix}/")


def _query(scope: Scope, key: str) -> str:
    """One query-string value.

    Args:
        scope: The connection scope.
        key: The parameter's name.

    Returns:
        The value, or an empty string.
    """
    for part in scope.get("query_string", b"").decode("latin-1").split("&"):
        name, _, value = part.partition("=")
        if name == key:
            return value
    return ""


def _limit(scope: Scope) -> int:
    """The ``limit`` query parameter, defaulted and clamped.

    Args:
        scope: The connection scope.

    Returns:
        A sane limit.
    """
    try:
        return max(1, min(200, int(_query(scope, "limit") or 50)))
    except ValueError:
        return 50


async def _redirect(send: Send, location: str) -> None:
    """Send a redirect.

    307 rather than 301: a permanent redirect is cached by the browser, and a
    development server whose mount point moves would keep sending people to the
    old one until they cleared it.

    Args:
        send: The send channel.
        location: Where to go.
    """
    await send(
        {
            "type": "http.response.start",
            "status": 307,
            "headers": [
                (b"location", location.encode()),
                (b"content-length", b"0"),
                (b"cache-control", b"no-store"),
                *BASE_HEADERS,
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


async def _json(send: Send, status: int, payload: Any) -> None:
    """Send a JSON response.

    Args:
        send: The send channel.
        status: The status code.
        payload: The body.
    """
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    await _bytes(send, status, body, "application/json", "no-store")


async def _bytes(send: Send, status: int, body: bytes, kind: str, cache: str) -> None:
    """Send a response with a body.

    Args:
        send: The send channel.
        status: The status code.
        body: The body.
        kind: The content type.
        cache: The cache-control header.
    """
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", kind.encode()),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", cache.encode()),
                *BASE_HEADERS,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
