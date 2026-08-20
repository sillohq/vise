"""
sillo_vise.watchers.exceptions — grouped by type and location, with what raised them.

Most exceptions are already recorded: the request middleware catches whatever
escapes the application, and the queue middleware catches whatever a job raises.
This watcher exists for the ones that do not escape — an exception the
application *handled*, through ``add_exception_handler`` or a ``try`` inside a
route — because "handled" and "did not happen" are very different things and
only one of them belongs on a dashboard.

The framework's exception handlers are wrapped for that. Grouping is by type and
origin, and the origin is the *deepest* frame rather than the shallowest: the
top of a traceback is whichever middleware caught the exception, and grouping on
that puts every failure in the application into one group named after the
middleware.
"""

from __future__ import annotations

import inspect
import traceback
from collections.abc import Callable
from typing import Any

from ..recorder import Recorder
from .base import Availability, Watcher, available

__all__ = ["ExceptionWatcher"]


class ExceptionWatcher(Watcher):
    """Records exceptions the application handled rather than raised past.

    Attributes:
        handlers: The application's exception handler table, and the entries
            replaced in it.
    """

    name = "exceptions"
    requires = "nothing — the request path records what escapes"

    __slots__ = ("app", "wrapped")

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.app: Any = None
        self.wrapped: list[tuple[Any, Any, Callable]] = []

    def probe(self, app: Any) -> Availability:
        """Report that exceptions can always be observed.

        Args:
            app: The application. Unused.

        Returns:
            Always available — an application that raises nothing simply has
            an empty panel, which is a true statement about it.
        """
        return available("request and job paths")

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Wrap the application's exception handlers.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)
        self.app = app

        table = _handlers(app)
        if table is None:
            return

        for key, handler in list(table.items()):
            if getattr(handler, "__vise_wrapped__", False):
                continue

            table[key] = self._wrap(handler)
            self.wrapped.append((table, key, handler))

    def _wrap(self, handler: Callable) -> Callable:
        """Build the replacement for one exception handler.

        The handler is called either way. A watcher that swallowed an
        exception, or changed what the client received, would be changing the
        application it is supposed to be observing.

        Args:
            handler: The application's handler.

        Returns:
            A handler that records first and then defers.
        """
        recording = self._record

        if inspect.iscoroutinefunction(handler):

            async def wrapped_async(
                request: Any, error: Any, *args: Any, **kwargs: Any
            ) -> Any:
                """Record the exception, then run the real handler."""
                recording(error, handled=True)
                return await handler(request, error, *args, **kwargs)

            wrapped_async.__vise_wrapped__ = True  # type: ignore[attr-defined]
            return wrapped_async

        def wrapped(request: Any, error: Any, *args: Any, **kwargs: Any) -> Any:
            """Record the exception, then run the real handler."""
            recording(error, handled=True)
            return handler(request, error, *args, **kwargs)

        wrapped.__vise_wrapped__ = True  # type: ignore[attr-defined]
        return wrapped

    def _record(self, error: Any, *, handled: bool) -> None:
        """Store one exception.

        Args:
            error: The exception.
            handled: Whether a handler dealt with it.
        """
        if self.recorder is None or not isinstance(error, BaseException):
            return

        self.recorder.exception(
            error,
            handled=handled,
            traceback="".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        )

    def detach(self) -> None:
        """Put every wrapped handler back."""
        for table, key, handler in reversed(self.wrapped):
            table[key] = handler
        self.wrapped.clear()
        self.app = None
        super().detach()


def _handlers(app: Any) -> dict[Any, Any] | None:
    """The application's exception handler table.

    Args:
        app: The application.

    Returns:
        The mutable mapping of handlers, or None when it cannot be found —
        in which case the panel still works from what the request path
        records, and only handled exceptions go unseen.
    """
    for name in ("exception_handlers", "_exception_handlers"):
        table = getattr(app, name, None)
        if isinstance(table, dict):
            return table
    return None
