"""Knowing that the server is stopping.

One process-wide flag, set the moment a stop is asked for, and readable by
anything that would otherwise keep the process alive.

It exists because of Ctrl-C. Uvicorn's graceful shutdown waits for every open
connection to finish, and the dashboard holds one open on purpose: an
``EventSource`` for the live panels, parked for up to ten minutes. A browser
left on the Foreman tab therefore made Ctrl-C look like it did nothing at all —
the server had begun shutting down and was politely waiting for a stream that
had no idea anything had changed.

It sits at the top of the package rather than beside the runner that sets it,
because the dashboard reads it: ``dashboard`` already imports from ``server``
and ``server`` imports the dashboard back, so a flag either side of that seam
closes the circle. Nothing here imports anything, which is what makes it safe
to import from both.

A plain boolean rather than an ``asyncio.Event``: the flag is read from
whichever loop happens to be serving, and an Event binds itself to the first
loop that awaits it and then refuses every other one. That is a real failure —
two servers in one process, which is what a test suite is — traded for an
instant wake this does not need. The stream already comes up for air on its own
interval, and :attr:`ServerConfig.graceful_timeout` bounds the wait regardless.
"""

from __future__ import annotations

__all__ = ["begin_shutdown", "is_shutting_down", "reset"]

_stopping = False


def begin_shutdown() -> None:
    """Say the server is stopping. Idempotent."""
    global _stopping
    _stopping = True


def is_shutting_down() -> bool:
    """Whether a stop has been asked for."""
    return _stopping


def reset() -> None:
    """Forget that a shutdown happened, so another server can run in this process."""
    global _stopping
    _stopping = False
