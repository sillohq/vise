"""
sillo_vise.server.install — put vise around an application, in the right order.

Middleware order is the whole content of this module, and sillo builds its chain
inside-out: the *last* registered runs *first*. Getting that backwards produces a
dashboard that records itself and an application whose requests are missing from
their own log, so it is worth being explicit.

Registration order here is:

1. ``RequestRecorder`` — registered first, so it runs *innermost*, closest to the
   application. Everything it measures is the application's own time rather than
   vise's.
2. Optional watchers — no middleware of their own, except the websocket recorder.
3. ``Dashboard`` — registered last, so it runs *outermost*. Its own requests are
   answered before the recorder is ever reached, which is why the dashboard
   cannot appear in its own charts.

Watchers are attached twice: once now, and once when the application starts.
Both are necessary and neither is enough. A query watcher cannot find a database
connection before startup because there are none open; a routes panel is wrong if
it reads the table before the admin panel has registered its routes. Attaching at
startup is what makes both correct, and attaching now is what makes ``vise
doctor`` able to answer without running a server.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import ViseConfig
from ..dashboard import Dashboard
from ..panels import PanelRegistry
from ..recorder import Recorder
from ..watchers import RequestWatcher, WatcherRegistry

__all__ = ["Installation", "install"]

logger = logging.getLogger("sillo_vise")


class Installation:
    """Everything vise put around one application.

    Held so the server can read it for the banner, the CLI can report it, and
    tests can take it apart.

    Attributes:
        app: The application.
        config: The resolved configuration.
        recorder: The recorder, or None when recording is off.
        watchers: The watcher registry, or None when recording is off.
        panels: The panel registry, or None when there is no dashboard.
        dashboard_url: Where the dashboard answers, or an empty string.
    """

    __slots__ = ("app", "config", "recorder", "watchers", "panels", "dashboard_url")

    def __init__(self, app: Any, config: ViseConfig) -> None:
        """Build an empty installation.

        Args:
            app: The application.
            config: The resolved configuration.
        """
        self.app = app
        self.config = config
        self.recorder: Recorder | None = None
        self.watchers: WatcherRegistry | None = None
        self.panels: PanelRegistry | None = None
        self.dashboard_url = ""

    @property
    def live_panels(self) -> list[str]:
        """The panels that currently exist.

        Returns:
            Their ids, or an empty list when there is no dashboard.
        """
        return [panel.id for panel in self.panels.live()] if self.panels else []

    @property
    def missing_panels(self) -> list[dict[str, Any]]:
        """The panels that do not exist, and why.

        Returns:
            One entry per missing panel.
        """
        return self.panels.missing() if self.panels else []

    def notes(self) -> list[str]:
        """Short facts for the banner's footer.

        Returns:
            The notes, in the order they are printed.
        """
        notes = []
        notes.append("reload on" if self.config.server.reload else "reload off")
        notes.append("recorder on" if self.recorder else "recorder off")

        if self.panels:
            # The panel count is already on its own banner row. Repeating it
            # here would spend a third of the footer restating it.
            notes.append(f"dashboard {self.config.dashboard.access}")

        notes.append(self.config.source or "no .vise")
        return notes

    def shutdown(self) -> None:
        """Detach every watcher."""
        if self.watchers is not None:
            self.watchers.detach_all()


def install(app: Any, config: ViseConfig) -> Installation:
    """Put the recorder, the watchers and the dashboard around *app*.

    Args:
        app: The application to instrument.
        config: The resolved configuration.

    Returns:
        What was installed.
    """
    installation = Installation(app, config)

    if not config.recorder.enabled:
        # Nothing is constructed and nothing is wrapped. "Disabled means
        # compiled out" is not a slogan here: with the recorder off there is no
        # middleware in the chain and no branch on the request path to skip.
        logger.debug("vise: the recorder is off, so nothing was instrumented")
        return installation

    recorder = Recorder(config.recorder)
    installation.recorder = recorder

    prefix = config.dashboard.path.rstrip("/")
    skip = (prefix,) if config.dashboard.enabled and prefix else ()

    # Innermost: closest to the application, so what it measures is the
    # application's own time.
    RequestWatcher(skip=skip).attach(app, recorder)

    watchers = WatcherRegistry(app, recorder, config.panels)
    watchers.probe()
    installation.watchers = watchers

    if config.dashboard.enabled:
        panels = PanelRegistry(watchers, recorder, config, app)
        installation.panels = panels

        # Outermost: registered last, so the dashboard answers before the
        # recorder is reached and never appears in its own charts.
        app.use(Dashboard, config, recorder, watchers, panels, app, raw=True)

        installation.dashboard_url = (
            f"http://{_host(config.server.host)}:{config.server.port}{prefix}"
        )

    _reprobe_on_startup(app, watchers)
    return installation


def _reprobe_on_startup(app: Any, watchers: WatcherRegistry) -> None:
    """Probe again once the application has finished starting.

    The first probe runs before startup, when a database has no connections
    open and a scheduler has not started. Probing again afterwards is what makes
    the Queries and Schedules panels appear on the first page load rather than
    on the first probe interval.

    Args:
        app: The application.
        watchers: The watcher registry.
    """
    hook = getattr(app, "on_startup", None)
    if not callable(hook):  # pragma: no cover - every SilloApp has one
        return

    async def probe_again() -> None:
        """Re-probe every watcher."""
        watchers.probe()

    try:
        hook(probe_again)
    except Exception as error:  # noqa: BLE001 - a startup hook is not worth failing over
        logger.debug("vise: could not register the startup probe: %s", error)


def _host(host: str) -> str:
    """A host a browser can actually open.

    ``0.0.0.0`` means "every interface" to a server and nothing to a browser,
    so a banner printing it as a link prints a link that does not work.

    Args:
        host: The bind address.

    Returns:
        The address to put in a URL.
    """
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
