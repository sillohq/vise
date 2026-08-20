"""
sillo_vise.watchers.logs — a live tail that knows which request each line belongs to.

A ``logging.Handler`` on the root logger, which is the whole hook. What makes
the panel worth having is not the tail — a terminal already has one — but the
correlation: the handler reads the request in flight from the context variable
the request watcher set, so "every line emitted during one request" is a filter
rather than a search through timestamps.

Two things this handler must never do, both learned the hard way by everyone who
has written one:

**It must not log.** A handler that raises inside ``emit`` while handling a log
record produces a log record, and the recursion ends the process. Every failure
here is swallowed.

**It must not hold the caller.** ``emit`` runs on whatever thread logged, which
in an async application is the event loop. So it does the smallest possible
amount of work — format the message, read a context variable, append to a ring
— and never touches the network, the disk or a lock held by anything slow.

vise's own loggers are skipped. A dashboard that records its own log lines
fills its own panel, and then records that it did.
"""

from __future__ import annotations

import logging
from typing import Any

from ..recorder import Recorder
from .base import Availability, Watcher, available

__all__ = ["LogWatcher", "RecordingHandler"]

#: Logger names whose records are ignored, matched as prefixes.
_IGNORED = ("sillo_vise", "uvicorn.access")

#: Record attributes ``logging`` sets itself, and which are therefore not the
#: caller's structured extras.
_BUILTIN = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class RecordingHandler(logging.Handler):
    """A handler that files log records with the recorder.

    Attributes:
        recorder: Where events go.
    """

    def __init__(self, recorder: Recorder, level: int = logging.NOTSET) -> None:
        """Build a handler.

        Args:
            recorder: Where events go.
            level: Minimum level to record.
        """
        super().__init__(level)
        self.recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        """File one record.

        Args:
            record: The record being logged.
        """
        if record.name.startswith(_IGNORED):
            return

        try:  # noqa: SIM105
            self.recorder.log(
                record.levelname,
                record.getMessage(),
                logger=record.name,
                fields=_extras(record),
            )
        except Exception:  # noqa: BLE001
            # A handler that raises while handling a log record produces a log
            # record. Swallowing is not laziness here; it is the only way out
            # of the loop.
            #
            # Not `contextlib.suppress`: this runs on every log line the
            # application writes, and a context manager built and entered per
            # record is real cost on a path whose whole job is to be cheap.
            pass

    def handleError(self, record: logging.LogRecord) -> None:
        """Do nothing when handling fails.

        The default writes to stderr, which on a busy failure turns one broken
        record into a wall of tracebacks.

        Args:
            record: The record that could not be handled.
        """


class LogWatcher(Watcher):
    """Attaches the recording handler to the root logger.

    Attributes:
        handler: The installed handler.
    """

    name = "logs"
    requires = "nothing — every application has logging"

    __slots__ = ("handler",)

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.handler: RecordingHandler | None = None

    def probe(self, app: Any) -> Availability:
        """Report that log lines can always be observed.

        Args:
            app: The application. Unused.

        Returns:
            Always available.
        """
        return available("root logger")

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Install the handler.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)

        self.handler = RecordingHandler(recorder)
        logging.getLogger().addHandler(self.handler)

    def detach(self) -> None:
        """Remove the handler."""
        if self.handler is not None:
            logging.getLogger().removeHandler(self.handler)
        self.handler = None
        super().detach()


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """The structured fields a caller attached to a record.

    Args:
        record: The record.

    Returns:
        The extras, with anything JSON cannot hold reduced to its ``repr``.
    """
    return {
        key: value if isinstance(value, (str, int, float, bool)) else repr(value)
        for key, value in record.__dict__.items()
        if key not in _BUILTIN
    }
