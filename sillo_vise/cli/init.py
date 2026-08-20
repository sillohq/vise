"""
sillo_vise.cli.init — ``vise init``.

Writes a ``.vise`` with every setting present and commented out at its default.
The one value written live is the application's import string, when it could be
found, because that is the only thing the project cannot look up for itself.

It refuses to overwrite. A ``.vise`` is a file somebody edited, and a scaffolding
command that silently replaces one is a scaffolding command nobody runs twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from sillo.console import Command, Flag

from ..config import CONFIG_FILENAME, load_config, render_template
from .discover import discover_target

__all__ = ["Init"]


class Init(Command):
    """Write a starter ``.vise``."""

    name = "init"
    help = f"Write a starter {CONFIG_FILENAME} in this directory"
    description = (
        f"Creates a {CONFIG_FILENAME} with every setting shown at its default "
        "and commented out. The application's import string is written live "
        "when one can be found."
    )

    arguments: ClassVar[list] = [
        Flag("force", short="f", help=f"Overwrite an existing {CONFIG_FILENAME}"),
    ]

    def handle(self) -> int | None:
        """Write the file.

        Returns:
            The exit code.
        """
        path = Path.cwd() / CONFIG_FILENAME

        if path.exists() and not self.flag("force"):
            self.fail(
                f"{CONFIG_FILENAME} already exists here. Pass --force to replace it."
            )

        target = discover_target(load_config())
        path.write_text(render_template(target), encoding="utf-8")

        self.success(f"Wrote {CONFIG_FILENAME}")
        self.blank()

        if target:
            self.pairs([("application", target)])
        else:
            self.muted(
                "  No application was found, so [app] target is left commented. "
                "vise will look for app.main:app, main:app and app:app at startup."
            )

        self.blank()
        self.muted("  Next: vise serve")
        return 0
