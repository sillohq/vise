"""
sillo_vise.cli.serve — ``vise serve``.

Flags override the ``.vise`` file, and only the ones actually typed do. That
constraint is why the boolean settings are declared as *pairs* — ``--reload``
and ``--no-reload`` — rather than as one flag with a default.

A console flag always has a value: absent from the command line, it reports its
default, and nothing distinguishes that from somebody typing it. A single
``--reload`` defaulting to true would therefore override ``reload = false`` in
``.vise`` on every run, which is precisely the bug this file is arranged to
avoid. Two flags, both defaulting off, mean "neither was typed" is a state that
can be observed.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sillo.console import Command, Flag, Option

from ..config import ConfigError, load_config
from ..server import serve
from .discover import DEFAULT_APPS, discover_target

__all__ = ["Serve"]

#: Paired boolean flags, and the setting each one resolves to.
_PAIRED: dict[str, tuple[str, str]] = {
    "reload": ("server", "reload"),
    "dashboard": ("dashboard", "enabled"),
    "record": ("recorder", "enabled"),
}


class Serve(Command):
    """Run the application, with Foreman alongside it."""

    name = "serve"
    help = "Run the application, with the Foreman dashboard alongside it"
    description = (
        "Serves the project's application and mounts the Foreman operations "
        "dashboard beside it. Configuration comes from .vise; anything given "
        "here wins over the file."
    )

    arguments: ClassVar[list] = [
        Option("app", help="Import string, e.g. app.main:app. Default: discovered"),
        # No short alias: -h is help, and a server that binds to an
        # unexpected address because a person typed the obvious thing is a
        # bad trade for one saved character.
        Option("host", help="Address to bind"),
        Option("port", short="p", type=int, help="Port to bind"),
        Flag("reload", help="Restart when watched files change"),
        Flag("no-reload", help="Do not restart on changes"),
        Flag("dashboard", help="Mount the Foreman dashboard"),
        Flag("no-dashboard", help="Do not mount the dashboard"),
        Flag("record", help="Collect what the application does"),
        Flag("no-record", help="Collect nothing, and instrument nothing"),
        Option("access", choices=["local", "token", "open"], help="Who may reach the dashboard"),
        Option("level", choices=["debug", "info", "warning", "error"], help="Log level"),
        Option("style", choices=["vise", "plain", "json"], help="Log style"),
        Flag("quiet", short="q", help="No banner and no access log"),
    ]

    def handle(self) -> int | None:
        """Run the server.

        Returns:
            The exit code.
        """
        try:
            config = load_config(overrides=self._overrides())
        except ConfigError as error:
            self.fail(str(error))

        target = discover_target(config)
        if target is None:
            self.fail(
                "No application found. Point at one with --app, set [app] target "
                f"in .vise, or name it in pyproject.toml. Looked for: "
                f"{', '.join(DEFAULT_APPS)}."
            )

        try:
            return serve(config, target)
        except ValueError as error:
            self.fail(str(error))

    def _overrides(self) -> dict[str, dict[str, Any]]:
        """Turn the flags that were typed into configuration overrides.

        Only what was actually given. A flag left at its default must not
        override the file, or ``vise serve`` would reset every setting the
        project configured.

        Returns:
            Tables keyed by section name.
        """
        overrides: dict[str, dict[str, Any]] = {}

        def put(section: str, key: str, value: Any) -> None:
            """Record one override."""
            overrides.setdefault(section, {})[key] = value

        if (app := self.option("app")) is not None:
            put("app", "target", app)
        if (host := self.option("host")) is not None:
            put("server", "host", host)
        if (port := self.option("port")) is not None:
            put("server", "port", port)
        if (access := self.option("access")) is not None:
            put("dashboard", "access", access)
        if (level := self.option("level")) is not None:
            put("logs", "level", level)
        if (style := self.option("style")) is not None:
            put("logs", "style", style)

        for flag, (section, key) in _PAIRED.items():
            wanted = self._paired(flag)
            if wanted is not None:
                put(section, key, wanted)

        if self.flag("quiet"):
            put("logs", "banner", False)
            put("logs", "access", False)

        return overrides

    def _paired(self, name: str) -> bool | None:
        """Resolve a ``--x`` / ``--no-x`` pair.

        Args:
            name: The positive flag's name.

        Returns:
            True, False, or None when neither was typed.

        Raises:
            SystemExit: Through :meth:`fail`, when both were typed. Guessing
                which one a person meant is worse than telling them.
        """
        on = self.flag(name)
        off = self.flag(f"no-{name}")

        if on and off:
            self.fail(f"--{name} and --no-{name} cannot both be given.")

        return True if on else False if off else None
