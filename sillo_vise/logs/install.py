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
from .repeat import RepeatFilter

__all__ = [
    "LOG_LEVELS",
    "attach_access_log",
    "consolidate_framework_logging",
    "install_logging",
    "silence_uvicorn",
]

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

#: Loggers the framework attaches handlers to itself. ``sillo.logging``'s
#: ``create_logger`` puts a queue handler straight onto a named logger and
#: leaves it propagating, so a line written through it is printed twice: once in
#: the framework's own bracketed format, and once more by whatever the root
#: logger is using. On an unhandled exception, where two handlers each print a
#: full traceback, that is four screens of duplicate output for one failure.
_FRAMEWORK_LOGGERS = ("sillo",)


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


def consolidate_framework_logging() -> None:
    """Make the framework's own loggers report through the root logger.

    ``sillo.logging.create_logger`` adds a handler directly to a named logger
    and leaves ``propagate`` on, which means every line it writes is printed
    twice — once in its own bracketed format and once by the root handler. For
    an unhandled exception, where each handler prints a full traceback, that is
    four screens of output for one failure.

    Removing those handlers rather than reformatting them keeps one path to the
    terminal. It is a real intrusion into the application's logging setup, which
    is why it only happens under ``force`` — that is, only when a person ran
    ``vise serve``, whose whole job is to own the output.

    Clearing the handlers that exist is not enough on its own. ``create_logger``
    is called lazily — the error handler builds its logger the first time one is
    constructed, which is after this has run — so the function itself is
    replaced with one that configures the level and adds nothing. Every logger
    the framework makes from then on propagates to the root, and is printed
    once.
    """
    for name in _framework_logger_names():
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    _disarm_create_logger()


def _framework_logger_names() -> list[str]:
    """Every framework logger that already exists.

    Clearing only ``sillo`` is not enough: ``create_logger`` is called with a
    module's own name, so ``sillo.core.error.handler`` has a handler of its own
    that the parent's does not cover. The application is imported before this
    runs, so by now those children exist and can be found.

    Returns:
        The logger names, parents before children.
    """
    found = set(_FRAMEWORK_LOGGERS)

    for name in list(logging.root.manager.loggerDict):
        if any(name == root or name.startswith(f"{root}.") for root in _FRAMEWORK_LOGGERS):
            found.add(name)

    return sorted(found)


def _disarm_create_logger() -> None:
    """Stop ``sillo.logging.create_logger`` attaching handlers of its own.

    The replacement keeps the signature and returns the same logger object, so
    every caller behaves as before — it simply no longer gets a second path to
    the terminal.
    """
    try:
        from sillo import logging as framework_logging
    except ImportError:  # pragma: no cover - sillo is a hard dependency
        return

    if getattr(framework_logging.create_logger, "__vise_disarmed__", False):
        return

    def create_logger(logger_name: str = "sillo", log_level: int = logging.DEBUG, *_: object, **__: object) -> logging.Logger:
        """Return a logger that reports through the root handler."""
        logger = logging.getLogger(logger_name)
        logger.setLevel(log_level)
        logger.propagate = True
        return logger

    create_logger.__vise_disarmed__ = True  # type: ignore[attr-defined]
    framework_logging.create_logger = create_logger  # type: ignore[assignment]


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
        force: Replace handlers a project installed itself, and consolidate the
            framework's. Off by default, because a project that configured
            logging meant it.

    Returns:
        The handler that was installed, so a caller can remove it again.
    """
    silence_uvicorn()

    if force:
        consolidate_framework_logging()

    destination = stream or sys.stderr
    handler = logging.StreamHandler(destination)
    handler.addFilter(RepeatFilter())

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
