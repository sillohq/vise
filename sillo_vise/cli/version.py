"""
sillo_vise.cli.version — ``vise version``.

What is installed, and what each optional piece would enable. The second half is
the useful one: a person wondering why the Queues panel is missing is often one
``pip install redis`` away, and a version command that only printed version
numbers would not tell them.
"""

from __future__ import annotations

import platform
import sys
from importlib.util import find_spec

import sillo
from sillo.console import Command

from .. import __version__

__all__ = ["Version"]

#: Optional packages, the import that proves each is installed, and the panel
#: or behaviour it unlocks.
OPTIONAL: tuple[tuple[str, str, str], ...] = (
    ("tortoise-orm", "tortoise", "the Queries panel"),
    ("redis", "redis", "the Queues and Cache panels, with a shared backend"),
    ("httpx", "httpx", "the Outgoing panel"),
    ("watchfiles", "watchfiles", "faster reload"),
    ("psutil", "psutil", "memory per worker on the Workers panel"),
)


class Version(Command):
    """Report what is installed."""

    name = "version"
    help = "Show what is installed, and what each optional piece would add"
    aliases = ["about"]

    def handle(self) -> int | None:
        """Print the report.

        Returns:
            The exit code.
        """
        self.pairs(
            [
                ("vise", __version__),
                ("sillo", getattr(sillo, "__version__", "unknown")),
                (
                    "python",
                    f"{sys.version.split()[0]} ({platform.python_implementation()})",
                ),
                ("platform", platform.platform(terse=True)),
            ]
        )

        self.blank()
        self.line("Optional")

        for name, module, unlocks in OPTIONAL:
            if _installed(module):
                self.bullet(f"{name} — {unlocks}")
            else:
                self.muted(f"    {name} — not installed, would enable {unlocks}")

        return 0


def _installed(module: str) -> bool:
    """Whether a module can be imported.

    Checked with ``find_spec`` rather than by importing, so asking the question
    does not run somebody's package as a side effect.

    Args:
        module: The module's name.

    Returns:
        True when it is installed.
    """
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False
