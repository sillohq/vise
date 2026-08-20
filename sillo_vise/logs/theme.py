"""
sillo_vise.logs.theme — how vise's output looks, named by meaning.

Colours are declared once, here, as :class:`~sillo.console.style.Style` values
from the framework's own palette. Everything that writes a line names an
intent — ``STATUS_OK``, ``SLOW``, ``PATH`` — rather than a colour, so that
restyling the server is one edit rather than one edit per call site. It is the
same discipline ``sillo.console.style`` uses for the CLI, and it is why ``vise
serve`` and ``sillo routes`` look like the same tool.

The brand red is ``#fc0345``. It is emitted as true colour where the terminal
advertises it and downsampled to the 256-colour cube where it does not, which
:class:`~sillo.console.style.Palette` handles — so the red survives on a
terminal that has never heard of ``COLORTERM``, and disappears entirely in a
pipe.
"""

from __future__ import annotations

from sillo.console.style import Style

__all__ = [
    "ACCENT",
    "ARROW",
    "BYTES",
    "DIM",
    "DURATION",
    "LABEL",
    "LEVELS",
    "METHOD",
    "PATH",
    "SLOW",
    "STATUS_CLIENT_ERROR",
    "STATUS_OK",
    "STATUS_REDIRECT",
    "STATUS_SERVER_ERROR",
    "TIMESTAMP",
    "TITLE",
    "level_style",
    "status_style",
]

#: The brand red, used for the mark, the accent rule and server errors.
ACCENT = Style(fg="#fc0345")

#: The banner's title.
TITLE = Style(bold=True)

#: A banner label — "Local", "Foreman", "App".
LABEL = Style(fg="#fc0345")

#: The arrow that introduces a banner line.
ARROW = Style(fg="#fc0345", dim=True)

#: Anything present for completeness rather than for reading: the timestamp,
#: the byte count, the trailing notes.
DIM = Style(fg="grey")

#: The time an access line was written.
TIMESTAMP = Style(fg="grey")

#: The HTTP method.
METHOD = Style(fg="cyan")

#: The path. Left unstyled — it is the one field on the line a reader is
#: actually scanning for, and the way to make it stand out among coloured
#: neighbours is to leave it alone.
PATH = Style()

#: How long a request took.
DURATION = Style(fg="grey")

#: How many bytes went back.
BYTES = Style(fg="grey")

#: The marker on a request past the slow threshold.
SLOW = Style(fg="yellow")

#: 2xx.
STATUS_OK = Style(fg="green")

#: 3xx.
STATUS_REDIRECT = Style(fg="cyan")

#: 4xx — the client's problem, so amber rather than red.
STATUS_CLIENT_ERROR = Style(fg="yellow")

#: 5xx — the application's problem, in the brand red, because on this line it
#: is the thing that most needs to catch an eye.
STATUS_SERVER_ERROR = Style(fg="#fc0345", bold=True)

#: One style per log level.
LEVELS: dict[str, Style] = {
    "debug": Style(fg="grey"),
    "info": Style(fg="cyan"),
    "warning": Style(fg="yellow"),
    "error": Style(fg="#fc0345"),
    "critical": Style(fg="white", bg="#fc0345", bold=True),
}


def status_style(status: int) -> Style:
    """The style for an HTTP status code.

    Args:
        status: The response status.

    Returns:
        The style its class deserves.
    """
    if status >= 500:
        return STATUS_SERVER_ERROR
    if status >= 400:
        return STATUS_CLIENT_ERROR
    if status >= 300:
        return STATUS_REDIRECT
    if status >= 200:
        return STATUS_OK
    return DIM


def level_style(level: str) -> Style:
    """The style for a log level.

    Args:
        level: The level's name, in any case.

    Returns:
        The style, falling back to the muted one for a level nobody declared.
    """
    return LEVELS.get(level.lower(), DIM)
