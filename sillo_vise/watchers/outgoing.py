"""
sillo_vise.watchers.outgoing — every call the application made to somebody else.

sillo's HTTP client already has a middleware layer, and that is where this
should hook. It does not, for one reason: middleware is configured per client
instance, and a project builds its clients wherever it likes. Registering into
that layer would mean finding every client the application will ever construct,
which is not a thing a watcher can do.

``HTTPClient._send`` is wrapped at the class level instead, so every instance is
covered including ones built after vise attached.

``_send`` is private, and picking it is deliberate rather than careless. The
public ``request`` returns *parsed JSON*, not the response — so a wrapper around
it can see the URL and never the status code, which is the single most important
column on this panel. ``_send`` returns the ``httpx.Response``. When that
private name changes, the probe stops finding it and the panel disappears with a
reason, which is the failure mode to want.

Retries are read from the client's own ``HTTPClientStats``, as a delta across
the call, because the retry loop lives above ``_send`` and each attempt arrives
here as a separate call.
"""

from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import urlsplit

from ..recorder import Recorder
from .base import Availability, Watcher, available, unavailable

__all__ = ["OutgoingWatcher"]

#: The method that actually puts a request on the wire.
_SEND = "_send"


class OutgoingWatcher(Watcher):
    """Records every outgoing HTTP call.

    Attributes:
        client: The class that was wrapped.
        original: The method taken off it.
    """

    name = "outgoing"
    requires = "sillo.http.client, which needs httpx"

    __slots__ = ("client", "original")

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.client: type | None = None
        self.original: Callable | None = None

    def probe(self, app: Any) -> Availability:
        """Report whether the HTTP client can be instrumented.

        Args:
            app: The application. Unused — the client is a class, not
                something the application owns.

        Returns:
            Whether the seam is there.
        """
        client = _client_class()
        if client is None:
            return unavailable("sillo.http.client is unavailable — httpx is not installed")

        if not callable(getattr(client, _SEND, None)):
            return unavailable(
                f"HTTPClient.{_SEND} is gone — this build of sillo cannot be instrumented"
            )

        return available("sillo.http.client")

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Wrap the client's send method.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)

        client = _client_class()
        if client is None:  # pragma: no cover - probe said yes
            return

        original = client.__dict__.get(_SEND)
        if original is None or getattr(original, "__vise_wrapped__", False):
            return

        self.client = client
        self.original = original
        setattr(client, _SEND, self._wrap(original))

    def _wrap(self, original: Callable) -> Callable:
        """Build the replacement for the send method.

        Args:
            original: What it was.

        Returns:
            A coroutine function that times the call and records it.
        """

        async def wrapped(client: Any, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
            """Send the request, then record what it cost."""
            started = time.perf_counter()
            before = _retries(client)

            try:
                response = await original(client, method, url, *args, **kwargs)
            except Exception as error:
                self._record(
                    method,
                    url,
                    started,
                    status=0,
                    retries=_retries(client) - before,
                    error=f"{type(error).__name__}: {error}",
                )
                raise

            self._record(
                method,
                url,
                started,
                status=getattr(response, "status_code", 0),
                retries=_retries(client) - before,
                response_bytes=len(getattr(response, "content", b"") or b""),
            )
            return response

        wrapped.__name__ = _SEND
        wrapped.__qualname__ = f"vise:HTTPClient.{_SEND}"
        wrapped.__vise_wrapped__ = True  # type: ignore[attr-defined]
        return wrapped

    def _record(
        self,
        method: str,
        url: Any,
        started: float,
        *,
        status: int,
        retries: int,
        response_bytes: int = 0,
        error: str = "",
    ) -> None:
        """Store one outgoing call.

        Args:
            method: HTTP method.
            url: The target.
            started: ``perf_counter`` reading from before the call.
            status: The response status, or zero when it never completed.
            retries: Attempts after the first.
            response_bytes: Bytes received.
            error: Message, when the call failed without a status.
        """
        if self.recorder is None:  # pragma: no cover - detached
            return

        target = str(url)
        self.recorder.outgoing(
            target,
            method=method.upper(),
            host=urlsplit(target).netloc,
            status=status,
            duration_ms=(time.perf_counter() - started) * 1000,
            retries=max(0, retries),
            response_bytes=response_bytes,
            error=error,
        )

    def detach(self) -> None:
        """Put the send method back."""
        if self.client is not None and self.original is not None:
            setattr(self.client, _SEND, self.original)
        self.client = None
        self.original = None
        super().detach()


def _client_class() -> type | None:
    """sillo's HTTP client class, if it can be imported.

    Returns:
        The class, or None when httpx is not installed.
    """
    try:
        from sillo.http.client.client import HTTPClient
    except ImportError:
        return None
    return HTTPClient


def _retries(client: Any) -> int:
    """The client's lifetime retry count.

    Read as a delta across one call, because the retry loop sits above the
    method being wrapped and each attempt arrives here separately.

    Args:
        client: The HTTP client.

    Returns:
        The count, or zero when the client does not keep one.
    """
    stats = getattr(client, "stats", None)
    return int(getattr(stats, "retries_total", 0) or 0)
