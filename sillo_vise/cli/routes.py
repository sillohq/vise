"""
sillo_vise.cli.routes — ``vise routes``.

The Routes panel, in the terminal. ``sillo routes`` exists and lists method, path
and name; this adds what guards each route and what handles it, because those
are the two columns somebody is usually looking for when a request is being
refused and they cannot see why.
"""

from __future__ import annotations

from typing import ClassVar

from sillo.console import Command, Option

from ..config import ConfigError, load_config
from ..introspect import walk_routes
from ..server import import_application
from .discover import discover_target

__all__ = ["Routes"]


class Routes(Command):
    """List the application's routes, with what guards them."""

    name = "routes"
    help = "List the application's routes, with what guards each one"

    arguments: ClassVar[list] = [
        Option("method", short="m", help="Only routes accepting this method"),
        Option("path", short="p", help="Only paths containing this text"),
        Option(
            "guard", choices=["required", "open"], help="Only routes with this guard"
        ),
    ]

    def handle(self) -> int | None:
        """Print the table.

        Returns:
            The exit code.
        """
        try:
            config = load_config()
        except ConfigError as error:
            self.fail(str(error))

        target = discover_target(config)
        if target is None:
            self.fail("No application found. Set [app] target in .vise.")

        try:
            app = import_application(target)
        except ValueError as error:
            self.fail(str(error))

        routes = self._filtered(walk_routes(app))

        if not routes:
            self.muted("  No routes matched.")
            return 1

        self.table(
            ["method", "path", "name", "auth", "handler"],
            [
                [
                    route.method,
                    route.path,
                    route.name or "—",
                    route.auth,
                    route.handler or "—",
                ]
                for route in routes
            ],
        )
        self.blank()

        guarded = sum(1 for route in routes if route.auth == "required")
        self.muted(f"  {len(routes)} routes, {guarded} guarded")
        return 0

    def _filtered(self, routes: list) -> list:
        """Apply the command's filters.

        Args:
            routes: Every route.

        Returns:
            The routes that matched.
        """
        method = (self.option("method") or "").upper()
        path = self.option("path") or ""
        guard = self.option("guard") or ""

        return [
            route
            for route in routes
            if (not method or method in route.methods)
            and (not path or path in route.path)
            and (not guard or route.auth == guard)
        ]
