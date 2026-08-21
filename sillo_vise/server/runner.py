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

import dataclasses
import sys
from typing import Any

import sillo
import uvicorn

from .. import __version__
from ..config import ViseConfig
from ..lifecycle import begin_shutdown
from ..lifecycle import reset as reset_shutdown
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

    @staticmethod
    def _announce_shutdown_to_streams() -> None:
        """Have uvicorn tell the dashboard the moment a stop is asked for.

        Uvicorn shuts down in a fixed order: it asks every connection to close,
        waits for them, and *only then* runs the application's lifespan
        shutdown. So an ``on_shutdown`` hook cannot help a connection that is
        parked — by the time it runs, the waiting is already over.

        The one moment early enough is uvicorn's own signal handler, and
        uvicorn offers no hook into it. So this wraps the method. It is additive
        — the original is still called, and it is wrapped once — and if a future
        uvicorn changes the signature this quietly does nothing.

        It only reaches the server when reload is off. With reload on, uvicorn
        supervises a *child* process, and on any platform that spawns rather
        than forks that child is a fresh interpreter which never sees this. So
        the graceful timeout below is not a fallback for an unlikely case — it
        is what does the work in the default configuration, and it is short for
        that reason.
        """
        server_cls = uvicorn.Server
        original = getattr(server_cls, "handle_exit", None)
        if original is None or getattr(original, "_vise_wrapped", False):
            return

        def handle_exit(self: Any, sig: Any, frame: Any) -> Any:
            """Flag the shutdown, then let uvicorn do what it was going to."""
            begin_shutdown()
            return original(self, sig, frame)

        handle_exit._vise_wrapped = True  # type: ignore[attr-defined]
        server_cls.handle_exit = handle_exit  # type: ignore[method-assign]

    def run(self, app: Any) -> int:
        """Serve *app* until it is stopped.

        Args:
            app: The application, or an import string when reloading.

        Returns:
            The exit code.
        """
        server = self.config.server
        reset_shutdown()
        self._announce_shutdown_to_streams()

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
                # uvicorn is told to install nothing. A custom log_config
                # would leave it owning the configuration; this takes it away.
                log_config=None,
                access_log=False,
                # `Config` sets the level on uvicorn's loggers itself, after
                # `silence_uvicorn` has already set them — so uvicorn wins and
                # this is where the bar is actually decided. `error` keeps
                # "address already in use" visible, in vise's format, and drops
                # the reloader's "detected changes" line, which is a WARNING and
                # which vise reports itself with the panel count attached.
                log_level="error",
                # Uvicorn waits forever by default. One connection that does
                # not notice the shutdown — and a live dashboard stream is
                # exactly that — is enough to make Ctrl-C look broken. The
                # dashboard's own stream now closes itself the moment shutdown
                # begins; this is the backstop for everything that does not,
                # including the served application's own long-lived requests.
                timeout_graceful_shutdown=server.graceful_timeout,
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
    # The target is usually discovered rather than configured, and the
    # dashboard reports it — so put it back on the configuration rather than
    # letting `meta` show an empty string for something vise definitely knows.
    if not config.app.target:
        config = dataclasses.replace(
            config, app=dataclasses.replace(config.app, target=target)
        )

    server = Server(config, target)

    if config.server.reload:
        # The worker imports the application and instruments it. Nothing is
        # imported here: doing so would double every import side effect the
        # project has, and would still be thrown away.
        #
        # The logging is installed in this process as well as in the worker,
        # because the *reloader* runs here — and its "StatReload detected
        # changes" line would otherwise arrive through the root logger's
        # last-resort handler, in the one format vise exists to replace.
        install_logging(config.logs, force=True)

        prepare_environment(target, config)
        return server.run(FACTORY)

    # The instrumented application is the one that gets served. Reading it back
    # off the installation rather than off the local name, because `prepare`
    # is where the middleware chain is built and only it knows what came out.
    installation = server.prepare(import_application(target))
    server.announce()
    return server.run(installation.app)


def _framework_version() -> str:
    """The installed sillo version.

    Returns:
        The version, or ``unknown``.
    """
    return getattr(sillo, "__version__", "unknown")
