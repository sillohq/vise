"""
sillo_vise.config.schema — what a ``.vise`` file can say.

Each section of the file is one frozen dataclass, and :class:`ViseConfig` holds
them all. Frozen because configuration is read at boot and then handed to the
recorder, the dashboard and the server; a mutable copy passed to three places
is three places that can disagree about the port.

Defaults live here and nowhere else. The loader layers a file, the environment
and command-line flags on top, so anything absent from all three is whatever
these classes say — which makes ``vise serve`` in an empty directory a
meaningful thing to run.
"""

from __future__ import annotations

import dataclasses
from typing import Any

__all__ = [
    "AppConfig",
    "DashboardConfig",
    "LogConfig",
    "PanelConfig",
    "RecorderConfig",
    "ServerConfig",
    "ViseConfig",
]

#: Header names redacted before an event reaches the store. Anything a browser
#: or a client puts a credential in belongs here; the list is a floor, and
#: ``recorder.redact`` extends it rather than replacing it.
DEFAULT_REDACT: tuple[str, ...] = (
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
    "x-xsrf-token",
)

#: Directories watched for changes when reload is on and nothing else is named.
DEFAULT_WATCH: tuple[str, ...] = ("app", "config", "src")


@dataclasses.dataclass(frozen=True)
class AppConfig:
    """Where the application is.

    Attributes:
        target: An import string, ``module:attribute``. None means fall back to
            the framework's own discovery, which reads ``SILLO_APP`` and
            ``[tool.sillo] app`` before trying the usual filenames.
        name: What to call the application in the interface. None means take
            the title off the application itself.
    """

    target: str | None = None
    name: str | None = None


@dataclasses.dataclass(frozen=True)
class ServerConfig:
    """How the application is served.

    Attributes:
        host: Address to bind.
        port: Port to bind.
        reload: Restart the process when watched files change.
        watch: Directories to watch. Only meaningful when *reload* is on.
        workers: Process count. More than one is refused alongside *reload*,
            and alongside the recorder, which is in-process and would then
            hold a different fraction of the traffic in each worker.
        root_path: Mount prefix when something else is proxying to this.
        graceful_timeout: Seconds to let open connections finish once a stop
            has been asked for, before they are closed anyway.

            Uvicorn's own default is to wait forever, which is why Ctrl-C used
            to look ignored: the dashboard parks a server-sent event stream for
            up to ten minutes, and a browser left on the Foreman tab is enough
            to hold the whole process open.

            One second, because this is a development server. There is no
            traffic worth draining here — the thing on the other end of that
            connection is your own browser, and it reconnects by itself.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = True
    watch: tuple[str, ...] = DEFAULT_WATCH
    workers: int = 1
    root_path: str = ""
    graceful_timeout: float = 1.0


@dataclasses.dataclass(frozen=True)
class DashboardConfig:
    """Whether Foreman is mounted, where, and who may reach it.

    Attributes:
        enabled: Mount the dashboard at all.
        path: Prefix it is mounted under.
        access: ``"local"`` admits only loopback clients, ``"token"`` requires
            *token*, ``"open"`` admits anyone. The default is deliberately the
            narrow one: this is a dashboard showing every request the
            application has served.
        token: Shared secret for ``access = "token"``.
        title: Window title, so two dashboards in two tabs are distinguishable.
    """

    enabled: bool = True
    path: str = "/__sillo/foreman"
    access: str = "local"
    token: str | None = None
    title: str | None = None


@dataclasses.dataclass(frozen=True)
class RecorderConfig:
    """What is collected, how much is kept, and what never reaches the store.

    Attributes:
        enabled: Collect anything at all. When this is off no middleware is
            wrapped around the application and no watcher is attached, so the
            cost is a boolean at boot rather than a branch per request.
        buffer: Events kept per kind. Memory is bounded by this and the number
            of kinds, not by how much traffic arrives.
        window_minutes: How far back the per-minute series runs.
        slow_request_ms: Requests at or above this are marked slow.
        slow_query_ms: Queries at or above this are marked slow.
        redact: Header names to redact, on top of :data:`DEFAULT_REDACT`.
        redact_params: Query-string and form keys whose values are redacted.
        redact_bindings: Replace SQL bind parameters with their types.
        capture_bodies: Keep request and response bodies, capped at
            *max_body_bytes* and passed through the same redaction as
            everything else.

            On by default, which is a deliberate choice rather than an
            oversight. A body is where the credential that is not in a header
            lives, and that argues for off — but this is a development server,
            the dashboard is loopback-only by default, and "what did the server
            actually send back" is the question the Requests panel exists to
            answer. Off is one line in ``.vise`` for anyone whose threat model
            differs.
        max_body_bytes: Ceiling on a captured body. A streamed download must
            not be buffered into the dashboard on its way to the client.
    """

    enabled: bool = True
    buffer: int = 2000
    window_minutes: int = 60
    slow_request_ms: int = 500
    slow_query_ms: int = 100
    redact: tuple[str, ...] = ()
    redact_params: tuple[str, ...] = ("token", "secret", "password", "api_key")
    redact_bindings: bool = False
    capture_bodies: bool = True
    max_body_bytes: int = 16 * 1024

    @property
    def redacted_headers(self) -> frozenset[str]:
        """Every header name to redact, lowercased.

        Returns:
            The default list extended by whatever the project added.
        """
        return frozenset(
            name.lower() for name in (*DEFAULT_REDACT, *self.redact) if name
        )


@dataclasses.dataclass(frozen=True)
class LogConfig:
    """How the server talks.

    Attributes:
        style: ``"vise"`` is the aligned, coloured format; ``"plain"`` drops
            the colour and keeps the alignment, for a file or a CI log;
            ``"json"`` emits one object per line for a collector.
        level: Minimum level for application logs.
        access: Emit a line per request.
        banner: Print the startup banner.
        show_query: Include the query string in an access line.
        static: Emit access lines for the dashboard's own asset requests. Off,
            because a dashboard that logs itself drowns the log it is showing.
    """

    style: str = "vise"
    level: str = "info"
    access: bool = True
    banner: bool = True
    show_query: bool = True
    static: bool = False


@dataclasses.dataclass(frozen=True)
class PanelConfig:
    """Which panels may appear.

    A panel still has to prove it can observe something before it is
    registered; this only removes panels that would otherwise qualify.

    Attributes:
        disable: Panel ids to leave out.
        refresh_ms: How often the interface asks for a new snapshot.
        probe_seconds: How often availability is re-checked, so a panel can
            appear when its backend comes up rather than at the next restart.
    """

    disable: tuple[str, ...] = ()
    refresh_ms: int = 2000
    probe_seconds: int = 15


@dataclasses.dataclass(frozen=True)
class ViseConfig:
    """A whole ``.vise`` file, resolved.

    Attributes:
        app: Where the application is.
        server: How it is served.
        dashboard: Whether Foreman is mounted, and to whom.
        recorder: What is collected.
        logs: How the server talks.
        panels: Which panels may appear.
        source: The file this came from, or None when nothing was found.
    """

    app: AppConfig = dataclasses.field(default_factory=AppConfig)
    server: ServerConfig = dataclasses.field(default_factory=ServerConfig)
    dashboard: DashboardConfig = dataclasses.field(default_factory=DashboardConfig)
    recorder: RecorderConfig = dataclasses.field(default_factory=RecorderConfig)
    logs: LogConfig = dataclasses.field(default_factory=LogConfig)
    panels: PanelConfig = dataclasses.field(default_factory=PanelConfig)
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the whole configuration as plain data.

        Used by the Config panel and by ``vise doctor``. The token is replaced
        rather than shown: the dashboard reads this over HTTP, and a shared
        secret that arrives in a JSON payload is no longer shared with only two
        parties.

        Returns:
            Nested dictionaries mirroring the file's sections.
        """
        data = dataclasses.asdict(self)
        if data["dashboard"].get("token"):
            data["dashboard"]["token"] = "***"
        return data
