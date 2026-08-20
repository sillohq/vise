"""
sillo_vise.watchers.requests — the raw ASGI middleware every other panel hangs off.

This is the one watcher that is not optional, because it does two jobs. It
records requests, and it opens the context every other watcher correlates
against: a query event knows which request caused it only because this
middleware set a context variable before calling into the application.

It is written as **raw ASGI**, registered with ``app.use(..., raw=True)``, not
as one of sillo's dispatch middlewares. The framework's own documentation is
blunt about the difference: the dispatch form costs a ``Request``, a
``Response`` and a background task per layer per request, and this layer sits
on every single request including the ones it does not record. Raw ASGI builds
nothing on the application's behalf.

Response bytes are counted by watching ``http.response.body`` messages go past,
which is where the count actually is. uvicorn's access logger cannot report it
because it never sees the assembled body, and a middleware that read
``content-length`` would be wrong for every streamed or chunked response.

Bodies are captured the same way, by teeing both channels. The response side is
easy — the messages go past on their way out. The request side is not: reading
the body consumes it, so ``receive`` is wrapped to keep a copy of each chunk and
hand the message on untouched. Getting that wrong does not produce a missing
body, it produces an application that hangs waiting for a request it will never
see, which is why the wrapper only ever *observes*.

Both sides stop copying at ``recorder.max_body_bytes``. A streamed download must
not be buffered into the dashboard on its way to the client.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from ..introspect import RouteResolver
from ..recorder import Recorder, new_id, request_scope, set_route

__all__ = ["RequestRecorder", "RequestWatcher"]

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class RequestRecorder:
    """Raw ASGI middleware recording every HTTP request.

    Attributes:
        app: The application this wraps.
        recorder: Where events go.
        skip: Path prefixes that are not recorded at all — the dashboard's own
            requests, which would otherwise dominate every chart on the
            dashboard.
        resolver: Names the route that handled each request. Optional: without
            one, requests are recorded with an empty route name rather than
            not recorded.
    """

    __slots__ = ("app", "recorder", "skip", "resolver")

    def __init__(
        self,
        app: Any,
        recorder: Recorder,
        skip: tuple[str, ...] = (),
        resolver: RouteResolver | None = None,
    ) -> None:
        """Wrap *app*.

        Args:
            app: The next ASGI application.
            recorder: Where events go.
            skip: Path prefixes not to record at all.
            resolver: Names the route that handled each request.
        """
        self.app = app
        self.recorder = recorder
        self.skip = skip
        self.resolver = resolver

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI event.

        Args:
            scope: The connection scope.
            receive: The receive channel.
            send: The send channel.
        """
        if scope["type"] != "http":
            # Lifespan and websocket connections pass straight through. The
            # websocket watcher handles its own, and lifespan is not traffic.
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if self.skip and path.startswith(self.skip):
            await self.app(scope, receive, send)
            return

        request_id = new_id()
        started = time.perf_counter()

        capture = self.recorder.config.capture_bodies
        ceiling = self.recorder.config.max_body_bytes

        status = 0
        response_headers: list[tuple[str, str]] = []
        written = 0
        sent_body = bytearray()
        received_body = bytearray()

        async def watch(message: Message) -> None:
            """Note what goes back to the client, then send it on.

            Args:
                message: The ASGI message being sent.
            """
            nonlocal status, response_headers, written

            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers = _decode(message.get("headers", ()))
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                written += len(chunk)

                if capture and len(sent_body) < ceiling:
                    sent_body.extend(chunk[: ceiling - len(sent_body)])

            await send(message)

        async def listen() -> Message:
            """Keep a copy of what arrives, and hand it on untouched.

            Returns:
                The message the application asked for. Never a copy, and never
                withheld: reading a body consumes it, and a watcher that
                swallowed a chunk would hang the application rather than lose a
                panel.
            """
            message = await receive()

            if capture and message.get("type") == "http.request":
                chunk = message.get("body", b"")
                if chunk and len(received_body) < ceiling:
                    received_body.extend(chunk[: ceiling - len(received_body)])

            return message

        source = listen if capture else receive

        with request_scope(request_id):
            try:
                await self.app(scope, source, watch)
            except Exception as error:
                # The application raised past its own handlers. Record the
                # request as a 500 before re-raising, or the one request that
                # most needs to be on the dashboard is the one missing from it.
                self._record(
                    scope,
                    request_id,
                    status or 500,
                    started,
                    response_headers,
                    written,
                    received_body,
                    sent_body,
                )
                self.recorder.exception(error, request_id=request_id)
                raise
            else:
                self._record(
                    scope,
                    request_id,
                    status,
                    started,
                    response_headers,
                    written,
                    received_body,
                    sent_body,
                )

    def _record(
        self,
        scope: Scope,
        request_id: str,
        status: int,
        started: float,
        response_headers: list[tuple[str, str]],
        written: int,
        received_body: bytearray,
        sent_body: bytearray,
    ) -> None:
        """Store the finished request.

        Args:
            scope: The connection scope.
            request_id: The id events were correlated against.
            status: The response status.
            started: ``perf_counter`` reading from before the application ran.
            response_headers: Headers sent back.
            written: Body bytes sent back.
            received_body: What the client sent, up to the ceiling.
            sent_body: What went back, up to the ceiling.
        """
        client = scope.get("client")

        self.recorder.request(
            id=request_id,
            request_id=request_id,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
            query=scope.get("query_string", b"").decode("latin-1"),
            route=self.resolver.resolve(scope) if self.resolver else "",
            status=status,
            duration_ms=(time.perf_counter() - started) * 1000,
            response_bytes=written,
            client=f"{client[0]}:{client[1]}" if client else "",
            headers=_decode(scope.get("headers", ())),
            response_headers=response_headers,
            user=_user(scope),
            # `or ""` so an empty capture is the empty *string*, not empty
            # bytes: the field is declared `str`, and `b""` is falsy enough to
            # skip redaction and then serialise as the literal text `b''`.
            body=bytes(received_body) or "",
            response_body=bytes(sent_body) or "",
        )


def _decode(headers: Any) -> list[tuple[str, str]]:
    """Turn ASGI's byte header pairs into text, keeping their order.

    Order is part of what a reader comes to the Requests panel for — the
    headers a client sent, as it sent them — so this stays a list.

    Args:
        headers: Pairs of bytes.

    Returns:
        Pairs of text.
    """
    return [
        (name.decode("latin-1"), value.decode("latin-1")) for name, value in headers
    ]


def _user(scope: Scope) -> str:
    """Identify the authenticated user, when there is one.

    Reads ``display_name`` and ``identity``, which are the read-only properties
    sillo's ``UserBaseModel`` exposes, and falls back to ``str`` on anything
    else that turned up in ``scope["user"]``.

    Args:
        scope: The connection scope.

    Returns:
        A short identity, or an empty string for an anonymous request.
    """
    user = scope.get("user")
    if user is None or not getattr(user, "is_authenticated", False):
        return ""

    return str(
        getattr(user, "display_name", "") or getattr(user, "identity", "") or user
    )


class RequestWatcher:
    """Registers :class:`RequestRecorder` on the application.

    Not a :class:`~sillo_vise.watchers.base.Watcher` subclass, because it is
    not optional and does not probe: without it there is no request context,
    and every other watcher would have nothing to correlate against. It is
    installed by the server before any other watcher is considered.

    Attributes:
        skip: Path prefixes not recorded.
        resolver: Built from the application at attach time, and shared so
            that a reload can invalidate one cache rather than several.
    """

    name = "requests"

    __slots__ = ("skip", "resolver")

    def __init__(self, skip: tuple[str, ...] = ()) -> None:
        """Build the watcher.

        Args:
            skip: Path prefixes not to record.
        """
        self.skip = skip
        self.resolver: RouteResolver | None = None

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Wrap the application.

        Args:
            app: The application.
            recorder: Where events go.
        """
        self.resolver = RouteResolver(app)
        app.use(RequestRecorder, recorder, self.skip, self.resolver, raw=True)

    @staticmethod
    def name_route(name: str) -> None:
        """Name the route handling the request in flight.

        Exposed so a project or another watcher can attribute events to a
        route that the scope did not carry.

        Args:
            name: The route's name.
        """
        set_route(name)
