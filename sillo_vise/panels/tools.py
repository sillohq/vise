"""
sillo_vise.panels.tools — the route table and the resolved configuration.

Neither of these has a watcher. They read the application itself, so they are
always available: an application always has routes, and vise always has a
configuration. They are also the two panels that are useful before a single
request has been served, which is exactly when a person is most likely to be
looking at a development server.

Config is the panel with a rule attached. It shows every resolved value and
where it came from — a default, ``.vise``, the environment or a flag — and it
shows the token as ``***``, because this is served over HTTP and a shared
secret that arrives in a JSON payload is no longer shared with only two parties.
"""

from __future__ import annotations

import dataclasses
import platform
import sys
from typing import Any

import sillo

from .. import __version__
from ..config import CONFIG_FILENAME, ENV_PREFIX, ViseConfig
from ..introspect import walk_routes
from ..logs.format import elapsed, number, truncate
from .base import Panel, PanelContext, Rendered, columns, table
from .tiles import TONE_GOOD, TONE_INFO, TONE_MUTED, TONE_WARN, tile

__all__ = ["ConfigPanel", "RoutesPanel"]

#: Sections of the configuration, in the order ``.vise`` declares them.
_SECTIONS = ("app", "server", "dashboard", "recorder", "logs", "panels")


class RoutesPanel(Panel):
    """The route table, with what guards each route."""

    id = "routes"
    name = "Routes"
    group = "Tools"
    icon = "spark"
    summary = "Method, path, name, handler and the auth declaration."
    watcher = None

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the route table.

        Args:
            context: What the panel may read.

        Returns:
            Tiles and the table.
        """
        routes = walk_routes(context.app) if context.app is not None else []
        guarded = sum(1 for route in routes if route.auth == "required")
        named = sum(1 for route in routes if route.name)
        sockets = sum(1 for route in routes if "WEBSOCKET" in route.methods)

        return Rendered(
            id=self.id,
            tiles=[
                tile(
                    "Routes", number(len(routes)), delta="registered", tone=TONE_MUTED
                ),
                tile(
                    "Named",
                    number(named),
                    delta=f"{(named / len(routes) * 100) if routes else 0:.0f}%",
                    tone=TONE_GOOD if named == len(routes) else TONE_WARN,
                ),
                tile(
                    "Guarded",
                    number(guarded),
                    delta="auth=",
                    tone=TONE_GOOD if guarded else TONE_MUTED,
                ),
                tile(
                    "Websocket",
                    number(sockets),
                    delta="socket routes",
                    tone=TONE_INFO if sockets else TONE_MUTED,
                ),
            ],
            toolbar=[f"{len(routes)} routes", "Method", "Path", "Auth"],
            table=table(
                columns(
                    "Method",
                    "Path",
                    ("Name", "hidden md:table-cell"),
                    ("Auth", "hidden sm:table-cell"),
                    ("Handler", "hidden xl:table-cell"),
                ),
                [
                    [
                        route.method,
                        truncate(route.path, 44),
                        route.name or "—",
                        route.auth,
                        truncate(route.handler, 30) or "—",
                    ]
                    for route in routes
                ],
            ),
        )


class ConfigPanel(Panel):
    """Every resolved value, and where it came from."""

    id = "config"
    name = "Config"
    group = "Tools"
    icon = "layers"
    summary = "Every resolved value, and where it came from."
    watcher = None

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the configuration view.

        Args:
            context: What the panel may read.

        Returns:
            Tiles, the settings table and the panel-availability rail.
        """
        config = context.config
        live = context.registry.live()
        missing = context.registry.missing()

        return Rendered(
            id=self.id,
            tiles=[
                tile("vise", _vise_version(), delta="dev server", tone=TONE_MUTED),
                tile(
                    "sillo",
                    getattr(sillo, "__version__", "unknown"),
                    delta=platform.python_implementation().lower(),
                    tone=TONE_MUTED,
                ),
                tile(
                    "Panels",
                    f"{len(live)} live",
                    delta=f"{len(missing)} unavailable",
                    tone=TONE_GOOD if live else TONE_WARN,
                ),
                tile(
                    "Recorded",
                    number(sum(context.recorder.totals().values())),
                    delta=elapsed(context.recorder.uptime_seconds),
                    tone=TONE_INFO,
                ),
            ],
            toolbar=[
                config.source or f"no {CONFIG_FILENAME}",
                f"python {sys.version.split()[0]}",
                f"{ENV_PREFIX}*",
            ],
            table=table(
                columns("Key", "Value", ("Source", "hidden sm:table-cell")),
                self._settings(config),
            ),
            aside=self._panels(context),
        )

    @staticmethod
    def _settings(config: ViseConfig) -> list[list[str]]:
        """Every resolved setting, with where it came from.

        A value is attributed by comparing it against the schema's default: if
        it differs, something set it, and the file is named when there is one.
        That is an honest approximation and is described as one — a ``.vise``
        that sets a value to exactly its default is indistinguishable from one
        that leaves it out, and the difference does not matter to a reader.

        Args:
            config: The resolved configuration.

        Returns:
            Key, value and source rows.
        """
        defaults = ViseConfig()
        rows: list[list[str]] = []

        for section in _SECTIONS:
            resolved = getattr(config, section)
            default = getattr(defaults, section)

            for field in dataclasses.fields(resolved):
                value = getattr(resolved, field.name)
                if field.name == "token" and value:
                    value = "***"

                rows.append(
                    [
                        f"{section}.{field.name}",
                        _render(value),
                        "default"
                        if value == getattr(default, field.name)
                        else (config.source or "environment"),
                    ]
                )

        return rows

    @staticmethod
    def _panels(context: PanelContext) -> dict[str, Any]:
        """Every panel, and why the missing ones are missing.

        This is the answer to "where is the Queues panel", which is the
        question a live-only dashboard has to answer somewhere.

        Args:
            context: What the panel may read.

        Returns:
            The side rail.
        """
        rows = []
        for state in context.registry:
            rows.append(
                [
                    state.name,
                    state.availability.detail or state.watcher.requires,
                    "healthy" if state.live else "stalled",
                ]
            )

        return {
            "label": "Panels",
            "note": f"{len(context.registry.live())} live",
            "rows": rows,
        }


def _render(value: Any) -> str:
    """Render a configuration value for a table cell.

    Args:
        value: The value.

    Returns:
        The value as text, with tuples as comma-separated lists and booleans
        lowercased so they read as the TOML that would produce them.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (tuple, list)):
        return ", ".join(str(item) for item in value) if value else "—"
    if value is None or value == "":
        return "—"
    return str(value)


def _vise_version() -> str:
    """The installed vise version.

    Returns:
        The version.
    """
    return __version__
