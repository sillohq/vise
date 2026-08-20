"""
sillo_vise.server.runner — run the application, quietly.

uvicorn does the serving. What this adds is the two things ``uvicorn app:app``
does not: vise's instrumentation around the application, and vise's logging
instead of uvicorn's.

The logging replacement is not a custom ``log_config``. Passing one would leave
uvicorn owning the configuration, so a version bump could reintroduce its
formatter, and the access logger would still be running inside uvicorn's
protocol where the duration and response size are not known. Instead
``log_config=None`` stops uvicorn installing anything, ``access_log=False`` stops
its access logger, its loggers are silenced explicitly, and the access line is
written by the recorder — which already measured both numbers.

Reload is uvicorn's, and it changes what is handed over. With reload off, the
application object is instrumented here and served. With reload on, uvicorn is
given :data:`~sillo_vise.server.factory.FACTORY` instead — because the reload
worker is a fresh process that imports the application itself, and an object
instrumented in the parent never reaches it. Handing over a factory makes
reattachment structural rather than something to remember.
"""

from __future__ import annotations

import sys
from typing import Any

import sillo
import uvicorn

from .. import __version__
from ..config import ViseConfig
from ..logs import Banner, attach_access_log, install_logging
from .factory import FACTORY, import_application, prepare_environment
from .install import Installation, install

__all__ = ["Server", "serve"]


class Server:
    """One run of ``vise serve``.

    Attributes:
        config: The resolved configuration.
        target: The application's import string, when there is one.
        installation: What vise put around the application.
    """

    __slots__ = ("config", "target", "installation", "banner")

    def __init__(self, config: ViseConfig, target: str | None = None) -> None:
        """Build a server.

        Args:
            config: The resolved configuration.
            target: The application's import string.
        """
        self.config = config
        self.target = target or config.app.target
        self.installation: Installation | None = None
        self.banner = Banner()

    def prepare(self, app: Any) -> Installation:
        """Instrument *app* and install vise's logging.

        Args:
            app: The application.

        Returns:
            What was installed.
        """
        install_logging(self.config.logs, force=True)

        installation = install(app, self.config)
        self.installation = installation

        if installation.recorder is not None:
            attach_access_log(
                installation.recorder,
                self.config.logs,
                dashboard_path=self.config.dashboard.path.rstrip("/"),
            )

        return installation

    def links(self) -> list[tuple[str, str]]:
        """The banner's rows.

        Returns:
            Label and value pairs.
        """
        server = self.config.server
        host = "127.0.0.1" if server.host in ("0.0.0.0", "::", "") else server.host

        rows = [("Local", f"http://{host}:{server.port}")]

        if self.installation and self.installation.dashboard_url:
            rows.append(("Foreman", self.installation.dashboard_url))

        rows.append(("App", self.target or "the application"))

        if self.installation and self.installation.panels:
            live = len(self.installation.live_panels)
            missing = len(self.installation.missing_panels)
            rows.append(
                (
                    "Panels",
                    f"{live} live"
                    + (f" · {missing} waiting on what they watch" if missing else ""),
                )
            )

        return rows

    def announce(self) -> None:
        """Print the startup banner."""
        if not self.config.logs.banner:
            return

        self.banner.write(
            version=__version__,
            framework=_framework_version(),
            python=sys.version.split()[0],
            links=self.links(),
            notes=self.installation.notes() if self.installation else [],
        )

    def run(self, app: Any) -> int:
        """Serve *app* until it is stopped.

        Args:
            app: The application, or an import string when reloading.

        Returns:
            The exit code.
        """
        server = self.config.server

        try:
            uvicorn.run(
                app,
                factory=isinstance(app, str) and app == FACTORY,
                host=server.host,
                port=server.port,
                reload=server.reload,
                reload_dirs=list(server.watch) if server.reload else None,
                workers=server.workers if not server.reload else None,
                root_path=server.root_path,
                # uvicorn is told to install nothing. A custom log_config would
                # leave it owning the configuration; this takes it away.
                log_config=None,
                access_log=False,
            )
        except KeyboardInterrupt:  # pragma: no cover - a person pressed ^C
            self.stop("interrupted")
            return 130

        self.stop()
        return 0

    def stop(self, reason: str = "") -> None:
        """Detach everything and say the run has ended.

        Args:
            reason: Why it ended.
        """
        if self.installation is not None:
            self.installation.shutdown()

        if self.config.logs.banner:
            self.banner.stopped(reason)


def serve(config: ViseConfig, target: str) -> int:
    """Instrument and serve the application *target* names.

    Args:
        config: The resolved configuration.
        target: The application's import string.

    Returns:
        The exit code.

    Raises:
        ValueError: If the target cannot be imported.
    """
    server = Server(config, target)

    if config.server.reload:
        # The worker imports the application and instruments it. Nothing is
        # imported here: doing so would double every import side effect the
        # project has, and would still be thrown away.
        prepare_environment(target, config)
        return server.run(FACTORY)

    server.prepare(import_application(target))
    server.announce()
    return server.run(server.installation.app)


def _framework_version() -> str:
    """The installed sillo version.

    Returns:
        The version, or ``unknown``.
    """
    return getattr(sillo, "__version__", "unknown")
