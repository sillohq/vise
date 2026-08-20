"""
sillo_vise.cli.doctor — ``vise doctor`` and ``vise panels``.

Both answer the same question from different angles: *what will vise be able to
show me, and why not the rest?* On a live-only dashboard that question comes up
constantly — "where is the Queues panel" — and answering it in the terminal is
faster than answering it in a browser.

Both import the application and probe against it for real. A doctor that
reported what *ought* to work would be worse than no doctor: the entire value is
that it exercises the same probes ``vise serve`` will.
"""

from __future__ import annotations

import platform
import sys
from typing import ClassVar

import sillo
from sillo.console import Command, Option

from .. import __version__
from ..config import CONFIG_FILENAME, ConfigError, load_config
from ..panels import PanelRegistry, all_panels
from ..recorder import Recorder
from ..server import import_application
from ..watchers import WatcherRegistry
from .discover import discover_target

__all__ = ["Doctor", "Panels"]


class _Inspects(Command):
    """Shared plumbing for the commands that probe a real application."""

    def _probe(self) -> tuple[object, WatcherRegistry, PanelRegistry, object]:
        """Load the configuration, import the application and probe it.

        Returns:
            The configuration, the watcher registry, the panel registry and
            the application.
        """
        try:
            config = load_config()
        except ConfigError as error:
            self.fail(str(error))

        target = discover_target(config)
        if target is None:
            self.fail(
                "No application found. Set [app] target in .vise, or name it in "
                "pyproject.toml under [tool.sillo] app."
            )

        try:
            app = import_application(target)
        except ValueError as error:
            self.fail(str(error))

        recorder = Recorder(config.recorder)
        watchers = WatcherRegistry(app, recorder, config.panels)
        watchers.probe()

        return config, watchers, PanelRegistry(watchers, recorder, config, app), app


class Doctor(_Inspects):
    """Report what vise can see, and what it cannot."""

    name = "doctor"
    help = "Report what vise can observe in this project, and what it cannot"
    description = (
        "Imports the application and runs every availability probe against it, "
        "the same ones vise serve runs. Nothing is served and nothing is left "
        "attached."
    )

    def handle(self) -> int | None:
        """Print the report.

        Returns:
            0 when every declared panel is live, 1 when any is missing — so
            this is usable in a check without parsing the output.
        """
        config, watchers, panels, app = self._probe()

        try:
            self._versions(config)
            self._panels(panels)
            self._settings(config)
        finally:
            watchers.detach_all()

        missing = panels.missing()
        return 1 if missing else 0

    def _versions(self, config) -> None:
        """Print what is installed.

        Args:
            config: The resolved configuration.
        """
        self.line("Environment")
        self.pairs(
            [
                ("vise", __version__),
                ("sillo", getattr(sillo, "__version__", "unknown")),
                (
                    "python",
                    f"{sys.version.split()[0]} ({platform.python_implementation()})",
                ),
                ("config", config.source or f"no {CONFIG_FILENAME} — using defaults"),
            ]
        )
        self.blank()

    def _panels(self, panels: PanelRegistry) -> None:
        """Print each panel and its state.

        Args:
            panels: The panel registry.
        """
        live = {panel.id for panel in panels.live()}
        reasons = {entry["id"]: entry["reason"] for entry in panels.missing()}

        # Three columns, not four. The group is on the panel's own name in
        # the sidebar and adds nothing here, and a fourth column costs the
        # reason column the width it needs on an ordinary terminal — where a
        # truncated reason is the one thing this table exists to show.
        self.line(f"Panels — {len(live)} of {len(all_panels())} live")
        self.table(
            ["panel", "state", "why not"],
            [
                [
                    panel.id,
                    "live" if panel.id in live else "—",
                    "" if panel.id in live else reasons.get(panel.id, ""),
                ]
                for panel in panels
            ],
        )
        self.blank()

    def _settings(self, config) -> None:
        """Print the settings most likely to explain a surprise.

        Not every setting — that is the Config panel's job, and a doctor that
        printed sixty rows would bury the four that matter.

        Args:
            config: The resolved configuration.
        """
        self.line("Settings")
        self.pairs(
            [
                (
                    "dashboard",
                    f"{config.dashboard.path} ({config.dashboard.access})"
                    if config.dashboard.enabled
                    else "off",
                ),
                (
                    "recorder",
                    f"{config.recorder.buffer} events per kind"
                    if config.recorder.enabled
                    else "off",
                ),
                ("reload", "on" if config.server.reload else "off"),
                ("logs", f"{config.logs.style} at {config.logs.level}"),
            ]
        )


class Panels(_Inspects):
    """List the panels, live or not."""

    name = "panels"
    help = "List the dashboard's panels, and why any are missing"

    arguments: ClassVar[list] = [
        Option("group", short="g", help="Only panels in this sidebar group"),
    ]

    def handle(self) -> int | None:
        """Print the list.

        Returns:
            The exit code.
        """
        _, watchers, panels, _ = self._probe()

        try:
            wanted = (self.option("group") or "").lower()
            live = {panel.id for panel in panels.live()}
            reasons = {entry["id"]: entry["reason"] for entry in panels.missing()}

            rows = [
                [
                    panel.id,
                    panel.group,
                    "live" if panel.id in live else reasons.get(panel.id, "missing"),
                ]
                for panel in panels
                if not wanted or panel.group.lower() == wanted
            ]

            if not rows:
                self.muted(f"No panels in a group called {self.option('group')!r}.")
                return 1

            self.table(["panel", "group", "state"], rows)
            self.blank()
            self.muted(f"  {len(live)} live, {len(reasons)} waiting on what they watch")
        finally:
            watchers.detach_all()

        return 0
