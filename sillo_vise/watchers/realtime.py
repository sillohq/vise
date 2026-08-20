"""
sillo_vise.watchers.realtime — connections, channels, and event flow.

Two subsystems, one panel, because they are the same question from a reader's
point of view: what is moving through this application that is not a request?

Websockets are observed with a second raw ASGI middleware. It has to be
separate from the request one rather than folded into it: a websocket has no
status code, no response bytes and no single duration, and a connection lives
for minutes while requests live for milliseconds. Recording both through one
code path would mean a request event with most of its fields meaningless.

Application events are observed by wrapping ``Event.trigger`` and its async
twin. Wrapping the emitter's ``_dispatch`` looks like the obvious seam and is
the wrong one: it runs only on the *receive* side of a networked transport, so
on the memory backend — which is what every project starts with — it is never
called at all, and the panel recorded nothing while reporting itself live.

``trigger`` is where the listeners actually run, on every backend, and it
returns the execution statistics the panel wants.

The probe asks whether the application registers any websocket route. Every
sillo application *can* accept one; a panel for something the project never
built is a panel that will always read zero.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from ..introspect import walk_routes
from ..recorder import Recorder
from .base import Availability, Watcher, available, unavailable

__all__ = ["RealtimeWatcher", "WebsocketRecorder"]

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]


class WebsocketRecorder:
    """Raw ASGI middleware recording websocket traffic.

    Attributes:
        app: The application this wraps.
        recorder: Where events go.
    """

    __slots__ = ("app", "recorder")

    def __init__(self, app: Any, recorder: Recorder) -> None:
        """Wrap *app*.

        Args:
            app: The next ASGI application.
            recorder: Where events go.
        """
        self.app = app
        self.recorder = recorder

    async def __call__(
        self,
        scope: Scope,
        receive: Callable[[], Awaitable[Message]],
        send: Callable[[Message], Awaitable[None]],
    ) -> None:
        """Handle one ASGI event.

        Args:
            scope: The connection scope.
            receive: The receive channel.
            send: The send channel.
        """
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        channel = scope.get("path", "")
        opened = time.perf_counter()
        sent = 0

        async def watch_send(message: Message) -> None:
            """Note what the server sends, then send it.

            Args:
                message: The ASGI message.
            """
            nonlocal sent

            if message["type"] == "websocket.accept":
                self.recorder.websocket(channel, action="connect")
            elif message["type"] == "websocket.send":
                sent += _payload_size(message)
                self.recorder.websocket(
                    channel, action="send", bytes=_payload_size(message)
                )
            elif message["type"] == "websocket.close":
                self.recorder.websocket(
                    channel,
                    action="disconnect",
                    close_code=message.get("code", 1000),
                    bytes=sent,
                )

            await send(message)

        async def watch_receive() -> Message:
            """Note what the client sends, then return it.

            Returns:
                The ASGI message.
            """
            message = await receive()
            if message["type"] == "websocket.receive":
                self.recorder.websocket(
                    channel, action="receive", bytes=_payload_size(message)
                )
            return message

        try:
            await self.app(scope, watch_receive, watch_send)
        finally:
            # A connection that ends without a close message — the client
            # vanished, or the handler raised — still ended, and a panel that
            # only counts clean closes shows connections that never go away.
            self.recorder.websocket(
                channel,
                action="closed",
                bytes=sent,
                duration_ms=(time.perf_counter() - opened) * 1000,
            )


class RealtimeWatcher(Watcher):
    """Records websocket traffic and application events.

    Attributes:
        emitter: The event emitter, when the application has one.
        patched: Classes and methods wrapped, so detaching restores them.
    """

    name = "realtime"
    requires = "at least one websocket route, or an event emitter"

    __slots__ = ("emitter", "patched")

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.emitter: Any = None
        self.patched: list[tuple[type, str, Any]] = []

    def probe(self, app: Any) -> Availability:
        """Report whether the application does anything real-time.

        Args:
            app: The application.

        Returns:
            Whether there is traffic worth a panel.
        """
        sockets = [route for route in walk_routes(app) if "WEBSOCKET" in route.methods]
        emitter = _emitter(app)
        events = _event_names(emitter) if emitter is not None else []

        # An emitter with no events registered is what every sillo application
        # has, whether or not it uses one. Counting its mere existence as
        # availability would put this panel on every project in the world and
        # leave it reading zero on most of them.
        if not sockets and not events:
            return unavailable("no websocket routes and no registered events")

        parts = []
        if sockets:
            parts.append(
                f"{len(sockets)} socket route{'s' if len(sockets) != 1 else ''}"
            )
        if events:
            parts.append(f"{len(events)} event{'s' if len(events) != 1 else ''}")

        return available(", ".join(parts))

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Install the websocket middleware and wrap the emitter.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)

        app.use(WebsocketRecorder, recorder, raw=True)

        self.emitter = _emitter(app)
        if self.emitter is None:
            return

        self._wrap_trigger()

    def _wrap_trigger(self) -> None:
        """Wrap the method that actually runs an event's listeners."""
        try:
            from sillo.events.core import Event as SilloEvent
        except ImportError:  # pragma: no cover - events are first-party
            return

        for name in ("trigger", "trigger_async"):
            original = SilloEvent.__dict__.get(name)
            if original is None or getattr(original, "__vise_wrapped__", False):
                continue

            setattr(SilloEvent, name, self._wrap(name, original))
            self.patched.append((SilloEvent, name, original))

    def _wrap(self, name: str, original: Callable) -> Callable:
        """Build the replacement for one trigger method.

        Args:
            name: The method's name.
            original: What it was.

        Returns:
            A callable that times the dispatch and records it.
        """
        if name == "trigger_async":

            async def wrapped_async(event: Any, *args: Any, **kwargs: Any) -> Any:
                """Run the listeners, then record how it went."""
                started = time.perf_counter()
                try:
                    stats = await original(event, *args, **kwargs)
                except Exception:
                    self._record(event, started, {}, failed=True)
                    raise

                self._record(event, started, stats)
                return stats

            wrapped_async.__vise_wrapped__ = True  # type: ignore[attr-defined]
            return wrapped_async

        def wrapped(event: Any, *args: Any, **kwargs: Any) -> Any:
            """Run the listeners, then record how it went."""
            started = time.perf_counter()
            try:
                stats = original(event, *args, **kwargs)
            except Exception:
                self._record(event, started, {}, failed=True)
                raise

            self._record(event, started, stats)
            return stats

        wrapped.__vise_wrapped__ = True  # type: ignore[attr-defined]
        return wrapped

    def _record(
        self, event: Any, started: float, stats: Any, *, failed: bool = False
    ) -> None:
        """Store one emitted event.

        Args:
            event: The ``Event`` that was triggered.
            started: ``perf_counter`` reading from before the listeners ran.
            stats: Whatever ``trigger`` returned.
            failed: Whether the trigger itself raised.
        """
        if self.recorder is None:  # pragma: no cover - detached
            return

        counts = stats if isinstance(stats, dict) else {}

        # `trigger` returns `listeners_executed` and `execution_time`; a
        # cancelled event returns neither and says `cancelled` instead.
        self.recorder.signal(
            str(getattr(event, "name", "") or "event"),
            listeners=int(counts.get("listeners_executed", 0) or 0),
            duration_ms=(time.perf_counter() - started) * 1000,
            failures=1 if failed or counts.get("cancelled") else 0,
            transport=_transport(self.emitter),
        )

    def detach(self) -> None:
        """Put the wrapped trigger methods back.

        The websocket middleware stays in the chain: sillo has no interface for
        removing one, and reaching into the chain to invent it is not a
        watcher's business.
        """
        for owner, name, original in reversed(self.patched):
            setattr(owner, name, original)
        self.patched.clear()

        self.emitter = None
        super().detach()


def _payload_size(message: Message) -> int:
    """How large a websocket frame's payload is.

    Args:
        message: The ASGI message.

    Returns:
        The byte count, counting text as its UTF-8 length.
    """
    data = message.get("bytes")
    if data:
        return len(data)

    text = message.get("text") or ""
    return len(text.encode("utf-8"))


#: Where an application might keep an event emitter.
_EMITTER_KEYS = ("emitter", "events", "event_emitter")


def _emitter(app: Any) -> Any:
    """The application's event emitter, if it has one.

    Duck-typed rather than taken from a fixed key, because ``setup_work``
    already puts something else at ``state["events"]`` — a queue
    ``EventDispatcher``, which has neither ``event_names`` nor ``_dispatch``.
    Reading that key and hoping meant wrapping a method that was not there and
    reporting a panel that could see nothing.

    Args:
        app: The application.

    Returns:
        The emitter, or None.
    """
    state = getattr(app, "state", None) or {}

    for key in _EMITTER_KEYS:
        candidate = state.get(key)
        if _is_emitter(candidate):
            return candidate

    candidate = getattr(app, "events", None)
    return candidate if _is_emitter(candidate) else None


def _is_emitter(candidate: Any) -> bool:
    """Whether *candidate* is an event emitter vise can watch.

    Args:
        candidate: Whatever was found.

    Returns:
        True when it has the members this watcher uses.
    """
    return candidate is not None and callable(getattr(candidate, "event_names", None))


def _event_names(emitter: Any) -> list[str]:
    """Every event name an emitter knows.

    Args:
        emitter: The event emitter.

    Returns:
        The names, or an empty list.
    """
    names = getattr(emitter, "event_names", None)
    if not callable(names):
        return []

    try:
        return list(names())
    except Exception:  # noqa: BLE001 - an emitter mid-start is not a crash
        return []


def _listener_count(emitter: Any, channel: str) -> int:
    """How many listeners an event has.

    Args:
        emitter: The event emitter.
        channel: The event's name.

    Returns:
        The count, or zero when it cannot be read.
    """
    try:
        event = emitter[channel]
    except Exception:  # noqa: BLE001 - an unknown channel has no listeners
        return 0

    listeners = getattr(event, "listeners", None) or ()
    return len(listeners)


def _transport(emitter: Any) -> str:
    """Name an emitter's transport.

    Args:
        emitter: The event emitter.

    Returns:
        A short lowercase name.
    """
    transport = getattr(emitter, "transport", None)
    return (
        type(transport).__name__.removesuffix("Transport").lower()
        if transport
        else "memory"
    )
