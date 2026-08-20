"""
sillo_vise.logs.formatter — application log lines, in the same shape as the access lines.

``logging``'s default is ``INFO:sillo.record:query returned 412 rows``: the
level and the logger name each take a variable amount of room, so nothing lines
up and the message — the only part anybody is reading — starts at a different
column every line.

This puts the level in a fixed field, the logger in a fixed field, and the
message where the eye already is::

    09:14:22  warn   sillo.record       query returned 412 rows without a limit
    09:14:23  error  app.insights       upstream returned 503 after 3 retries

Levels are abbreviated to four characters — ``warn``, ``crit`` — because
``WARNING`` in a column of its own width pushes every message eight characters
right to say something the colour already said.

Three styles are offered. ``vise`` is the above. ``plain`` is the same
alignment without colour, for a file or a CI log where escape sequences are
noise. ``json`` is one object per line for a collector.
"""

from __future__ import annotations

import json
import logging
import time

from sillo.console.style import Palette, strip_ansi

from .format import truncate
from .theme import DIM, TIMESTAMP, level_style

__all__ = ["JSONFormatter", "ViseFormatter"]

#: Level names, shortened to fit one fixed column.
_SHORT = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warn",
    "ERROR": "error",
    "CRITICAL": "crit",
}

_LEVEL_WIDTH = 5
_LOGGER_WIDTH = 18


class ViseFormatter(logging.Formatter):
    """Formats a log record into an aligned, optionally coloured line.

    Attributes:
        palette: Decides whether lines carry colour. A palette built with
            ``enabled=False`` produces the ``plain`` style.
    """

    def __init__(self, palette: Palette | None = None) -> None:
        """Build a formatter.

        Args:
            palette: Colour decision for the destination stream.
        """
        super().__init__()
        self.palette = palette or Palette()

    def format(self, record: logging.LogRecord) -> str:
        """Render one record.

        Args:
            record: The record to render.

        Returns:
            The line, with any traceback indented beneath it.
        """
        paint = self.palette.render
        level = _SHORT.get(record.levelname, record.levelname.lower()[:_LEVEL_WIDTH])

        line = "  ".join(
            [
                "  " + paint(time.strftime("%H:%M:%S", time.localtime(record.created)), TIMESTAMP),
                paint(level.ljust(_LEVEL_WIDTH), level_style(level)),
                paint(truncate(record.name, _LOGGER_WIDTH).ljust(_LOGGER_WIDTH), DIM),
                record.getMessage(),
            ]
        )

        if record.exc_info:
            # Indented to the message column, so a traceback reads as belonging
            # to the line above it rather than as a new thing that happened.
            traceback = self.formatException(record.exc_info)
            line += "\n" + "\n".join(f"      {row}" for row in traceback.splitlines())

        return line


class JSONFormatter(logging.Formatter):
    """Formats a log record as one JSON object per line.

    The schema is narrow and stable: a collector should not have to cope with
    the field set changing because a caller passed a new keyword. Structured
    extras arrive under ``fields`` rather than at the top level, so nothing a
    caller invents can shadow ``level`` or ``at``.
    """

    #: Attributes ``logging`` puts on every record, which are therefore not
    #: the caller's structured extras.
    _BUILTIN = frozenset(
        logging.LogRecord("", 0, "", 0, "", None, None).__dict__
    ) | {"message", "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        """Render one record.

        Args:
            record: The record to render.

        Returns:
            One line of JSON.
        """
        payload = {
            "at": round(record.created, 3),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": strip_ansi(record.getMessage()),
        }

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self._BUILTIN
        }
        if extras:
            payload["fields"] = {key: _plain(value) for key, value in extras.items()}

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"), default=str)


def _plain(value: object) -> object:
    """Reduce a structured extra to something JSON can hold.

    Args:
        value: Whatever a caller attached to the record.

    Returns:
        The value, or its ``repr`` when JSON cannot take it.
    """
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return repr(value)
