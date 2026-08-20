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

Reload is uvicorn's, driven from an import string. That is why the application
has to be *named* rather than handed over: ``--reload`` re-imports it in a fresh
process, and an already-imported object cannot survive that. Vise reattaches on
each reload because the child process runs this module again from the top.
"""

from __future__ import annotations

import sys
from typing import Any

import sillo
import uvicorn

from .. import __version__
from ..config import ViseConfig
from ..logs import Banner, attach_access_log, install_logging
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


def serve(app: Any, config: ViseConfig, target: str | None = None) -> int:
    """Instrument and serve an application.

    Args:
        app: The application. When *config* asks for reload this must be the
            import string rather than the object, because uvicorn re-imports it
            in a fresh process and an imported object cannot survive that.
        config: The resolved configuration.
        target: The import string, for the banner.

    Returns:
        The exit code.
    """
    server = Server(config, target)

    if not isinstance(app, str):
        server.prepare(app)
        server.announce()
        return server.run(app)

    # With reload on, uvicorn imports the application in a worker process, and
    # that process runs the factory below — so instrumentation happens there,
    # once per reload, rather than here where it would be thrown away.
    server.announce()
    return server.run(app)


def _framework_version() -> str:
    """The installed sillo version.

    Returns:
        The version, or ``unknown``.
    """
    return getattr(sillo, "__version__", "unknown")
