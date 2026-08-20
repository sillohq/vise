"""
sillo_vise.logs.install — switch uvicorn's logging off and put vise's in its place.

Two things have to happen, and doing only the first is why most attempts at
this end up with two log lines per request.

**uvicorn is told not to log.** ``log_config=None`` stops it installing its own
handlers and formatters at startup, and ``access_log=False`` stops the access
logger entirely. Passing a custom ``log_config`` instead would leave uvicorn
owning the configuration, so a version bump could quietly reintroduce its
formatter.

**The access line moves to the recorder.** uvicorn's access logger runs inside
its protocol implementation, where the duration is not known and the response
size is not counted. Vise's line is written from the recorder's ``RequestEvent``
instead — one thing measures a request, and the log reads it. That is why
turning the recorder off also turns the access log off, and why the two can
never disagree.

Loggers that already have handlers are left alone. A project that configured
its own logging asked for that, and a development server is not the place to
overrule it.
"""

from __future__ import annotations

import logging
import sys
from typing import IO

from sillo.console.style import Palette

from ..config import LogConfig
from ..recorder import Event, EventKind, Recorder, RequestEvent
from .access import AccessLog
from .formatter import JSONFormatter, ViseFormatter

__all__ = ["LOG_LEVELS", "attach_access_log", "install_logging", "silence_uvicorn"]

#: Level names accepted in ``[logs] level``.
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

#: uvicorn's own loggers. Silenced rather than reformatted: everything they
#: report that matters is either in the banner or in the access line.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")


def silence_uvicorn() -> None:
    """Stop uvicorn's loggers writing anything.

    ``log_config=None`` prevents uvicorn *installing* handlers, but the loggers
    still exist and still propagate to the root — so a project that configured
    the root logger would get uvicorn's lines through it, in the root's format,
    which is exactly the double-logging this is meant to prevent.
    """
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = False
        # Not disabled outright: a failure to bind the port is reported through
        # uvicorn.error, and swallowing that would turn a clear message into a
        # process that exits with no explanation.
        logger.setLevel(logging.WARNING if name == "uvicorn.error" else logging.CRITICAL)


def install_logging(
    config: LogConfig,
    *,
    stream: IO[str] | None = None,
    force: bool = False,
) -> logging.Handler:
    """Put vise's formatter on the root logger.

    Args:
        config: The ``[logs]`` section.
        stream: Where lines go. Defaults to stderr, which is where diagnostics
            belong — the access log goes to stdout separately.
        force: Replace handlers a project installed itself. Off, because a
            project that configured logging meant it.

    Returns:
        The handler that was installed, so a caller can remove it again.
    """
    silence_uvicorn()

    destination = stream or sys.stderr
    handler = logging.StreamHandler(destination)

    if config.style == "json":
        handler.setFormatter(JSONFormatter())
    else:
        # `plain` is the same alignment with the colour decision forced off,
        # rather than a second formatter that could drift from the first.
        enabled = False if config.style == "plain" else None
        handler.setFormatter(ViseFormatter(Palette(destination, enabled=enabled)))

    root = logging.getLogger()
    root.setLevel(LOG_LEVELS.get(config.level.lower(), logging.INFO))

    if force or not root.handlers:
        root.handlers.clear()
        root.addHandler(handler)

    return handler


def attach_access_log(
    recorder: Recorder,
    config: LogConfig,
    *,
    dashboard_path: str = "",
    stream: IO[str] | None = None,
) -> AccessLog | None:
    """Write an access line for every request the recorder sees.

    Args:
        recorder: The recorder to read from.
        config: The ``[logs]`` section.
        dashboard_path: Prefix the dashboard is mounted under, so its own
            asset requests can be left out.
        stream: Where lines go. Defaults to stdout.

    Returns:
        The access log, or None when access logging is off.
    """
    if not config.access:
        return None

    log = AccessLog(
        stream,
        show_query=config.show_query,
        static=config.static,
        dashboard_path=dashboard_path,
        json=config.style == "json",
    )

    def write(event: Event) -> None:
        """Write the line for a request, ignoring every other kind."""
        if event.kind is EventKind.REQUEST and isinstance(event, RequestEvent):
            log.write(event)

    recorder.on_event(write)
    return log
