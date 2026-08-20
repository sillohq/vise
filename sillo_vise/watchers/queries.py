"""
sillo_vise.watchers.queries — SQL, with its duration and the code that ran it.

Tortoise logs every statement to ``tortoise.db_client`` at DEBUG, which sounds
like the hook and is not: the log line carries the query and its bindings, and
neither the duration nor the row count, because the logging call happens before
the statement runs. A Queries panel whose whole point is "p95 duration" and
"slowest queries" cannot be built on it.

So this wraps the connection instead. Tortoise's concrete database clients each
implement ``execute_query``, ``execute_query_dict`` and ``execute_insert``, and
wrapping those gives the three facts the panel needs — the statement, how long
it took, and how many rows came back.

The wrapping is done on the *classes of live connections*, discovered at attach
time, and undone on detach. Two consequences worth stating plainly:

* It has to happen after the database is initialised, because before that there
  are no connections to find the classes of. The watcher is attached during
  application startup for exactly this reason.
* It is a monkeypatch. That is a real cost, and it buys the only measurement
  point that exists — Tortoise offers no query hook, no event and no
  middleware. It is confined to three methods, it is reversible, and it is only
  installed when a person ran ``vise serve``.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ..recorder import Recorder
from .base import Availability, Watcher, available, unavailable

__all__ = ["QueryWatcher"]

#: The methods on a Tortoise client that actually run SQL.
_WRAPPED = ("execute_query", "execute_query_dict", "execute_insert")


class QueryWatcher(Watcher):
    """Records every statement the ORM runs.

    Attributes:
        patched: The classes that were wrapped, and the methods taken off them,
            so detaching restores exactly what was there.
    """

    name = "queries"
    requires = "a database configured through sillo.record"

    __slots__ = ("patched",)

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.patched: list[tuple[type, str, Callable]] = []

    def probe(self, app: Any) -> Availability:
        """Report whether the application has a database to watch.

        Args:
            app: The application.

        Returns:
            Whether a Tortoise connection can be found.
        """
        state = getattr(app, "state", None) or {}
        if "record" not in state:
            return unavailable("no database — sillo.record is not set up")

        connections = _connections()
        if connections is None:
            return unavailable("tortoise-orm is not installed")
        if not connections:
            return unavailable("no database connection is open yet")

        engines = sorted({type(connection).__name__ for connection in connections})
        return available(", ".join(engines))

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Wrap the query methods of every live connection's class.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)

        for client in _connections() or ():
            self._patch(type(client))

    def _patch(self, client: type) -> None:
        """Wrap one client class, once.

        Args:
            client: The Tortoise client class.
        """
        if any(patched is client for patched, _, _ in self.patched):
            return

        for name in _WRAPPED:
            # `__dict__` rather than getattr: a subclass that does not override
            # the method would otherwise get the base's wrapped onto it as
            # well, and one statement would be recorded twice.
            original = client.__dict__.get(name)
            if original is None or getattr(original, "__vise_wrapped__", False):
                continue

            setattr(client, name, self._wrap(name, original))
            self.patched.append((client, name, original))

    def _wrap(self, name: str, original: Callable) -> Callable:
        """Build the replacement for one query method.

        Args:
            name: The method's name.
            original: What it was.

        Returns:
            A coroutine function that times the call and records it.
        """

        async def wrapped(client: Any, query: str, *args: Any, **kwargs: Any) -> Any:
            """Run the statement, then record what it cost."""
            started = time.perf_counter()
            try:
                result = await original(client, query, *args, **kwargs)
            except Exception as error:
                self._record(client, query, args, started, rows=0, error=error)
                raise

            self._record(client, query, args, started, rows=_rows(result))
            return result

        wrapped.__name__ = name
        wrapped.__qualname__ = f"vise:{name}"
        # Marked so a second attach — after a reload, say — recognises a method
        # it already wrapped rather than wrapping the wrapper.
        wrapped.__vise_wrapped__ = True  # type: ignore[attr-defined]
        return wrapped

    def _record(
        self,
        client: Any,
        query: str,
        values: Any,
        started: float,
        *,
        rows: int,
        error: Exception | None = None,
    ) -> None:
        """Store one statement.

        Args:
            client: The connection that ran it.
            query: The statement.
            values: Its bindings.
            started: ``perf_counter`` reading from before it ran.
            rows: Rows returned or affected.
            error: The exception, when it failed.
        """
        if self.recorder is None:  # pragma: no cover - detached
            return

        self.recorder.query(
            query,
            params=values[0] if len(values) == 1 else (list(values) or None),
            duration_ms=(time.perf_counter() - started) * 1000,
            rows=rows,
            connection=getattr(client, "connection_name", "") or "default",
            source=type(error).__name__ if error else "",
        )

    def detach(self) -> None:
        """Put every wrapped method back."""
        for client, name, original in reversed(self.patched):
            setattr(client, name, original)
        self.patched.clear()
        super().detach()


def _connections() -> list[Any] | None:
    """Every open Tortoise connection.

    Returns:
        The connections, an empty list when the ORM is installed but has none
        open, or None when it is not installed at all. Those are three
        different problems and the probe reports them differently.
    """
    try:
        from tortoise import connections
    except ImportError:
        return None

    try:
        return list(connections.all())
    except Exception:  # noqa: BLE001 - an uninitialised ORM is not a crash
        return []


def _rows(result: Any) -> int:
    """How many rows a query method returned.

    The three wrapped methods return three different shapes: a list of rows, a
    ``(count, rows)`` pair, or an inserted key. Counting each correctly is
    worth more than it looks — "412 rows without a limit" is the warning the
    Queries panel exists to raise.

    Args:
        result: Whatever the method returned.

    Returns:
        The row count, or zero when there is nothing to count.
    """
    if result is None:
        return 0
    if isinstance(result, tuple) and result and isinstance(result[0], int):
        return result[0]
    if isinstance(result, (list, tuple)):
        return len(result)
    return 1
