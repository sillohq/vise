"""
sillo_vise.cli — the ``vise`` command.

Built on ``sillo.console``, the framework's own console toolkit, rather than on
click or typer. That is not tidiness for its own sake: ``vise serve`` and
``sillo routes`` are run by the same person in the same terminal minutes apart,
and a table, a heading and an error message that look different between them
make two tools out of what should be one.
"""

from __future__ import annotations

from sillo.console import Command, Console

from .. import __version__
from .bench import Bench
from .discover import discover_target
from .doctor import Doctor, Panels
from .init import Init
from .routes import Routes
from .serve import Serve
from .version import Version

__all__ = ["COMMANDS", "build_console", "discover_target"]

#: Every command ``vise`` offers, in the order the help lists them.
COMMANDS: list[type[Command]] = [Serve, Init, Doctor, Panels, Routes, Bench, Version]


def build_console() -> Console:
    """Assemble the ``vise`` console.

    Returns:
        The console, with every command registered.
    """
    console = Console(
        prog="vise",
        description="The Sillo development server, with Foreman built in.",
        version=__version__,
    )
    console.add_many(COMMANDS)
    return console
