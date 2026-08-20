"""
sillo_vise.dashboard.api — what the interface asks for, and what it gets back.

Five endpoints. The shapes are deliberately close to what the interface draws,
so the browser is a renderer rather than a second implementation of the panels:
a tile arrives as a tile, a table arrives with its columns, and nothing on the
client side decides what a number means.

Two things are true of every response here and are worth stating once:

* **Nothing is redacted at this layer.** It cannot be, because nothing reaching
  it is un-redacted — the recorder redacted on capture. A second redaction pass
  here would be a second place that could be forgotten, and would make the first
  one look optional.
* **Errors are JSON.** The interface polls; an HTML error page arriving where a
  panel was expected produces a parse failure in a browser console rather than a
  message on the screen.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

import sillo

from .. import __version__
from ..config import ViseConfig
from ..panels import PanelRegistry
from ..recorder import EventKind, Recorder

__all__ = ["DashboardAPI"]

#: Kinds a request detail page assembles, in the order it shows them.
_DETAIL_KINDS = (
    EventKind.QUERY,
    EventKind.CACHE,
    EventKind.OUTGOING,
    EventKind.JOB,
    EventKind.LOG,
    EventKind.MAIL,
    EventKind.EXCEPTION,
)


class DashboardAPI:
    """Answers the interface's questions from the store.

    Attributes:
        panels: Which panels exist, and how to build them.
        recorder: The recorder, for its store and its counters.
        config: The resolved configuration.
        app: The application being watched.
    """

    __slots__ = ("panels", "recorder", "config", "app")

    def __init__(
        self,
        panels: PanelRegistry,
        recorder: Recorder,
        config: ViseConfig,
        app: Any = None,
    ) -> None:
        """Build the API.

        Args:
            panels: The panel registry.
            recorder: The recorder.
            config: The resolved configuration.
            app: The application being watched.
        """
        self.panels = panels
        self.recorder = recorder
        self.config = config
        self.app = app

    def meta(self) -> dict[str, Any]:
        """Everything the interface needs before it draws anything.

        Returns:
            The application's identity, the sidebar, and the panels that are
            missing along with why.
        """
        return {
            "app": {
                "name": self.config.app.name or _app_title(self.app),
                "target": self.config.app.target or "",
                "url": f"{self.config.server.host}:{self.config.server.port}",
                "environment": "local",
            },
            "versions": {
                "vise": __version__,
                "sillo": getattr(sillo, "__version__", "unknown"),
                "python": sys.version.split()[0],
                "implementation": platform.python_implementation(),
            },
            "dashboard": {
                "path": self.config.dashboard.path,
                "title": self.config.dashboard.title or "Foreman",
                "refresh_ms": self.config.panels.refresh_ms,
            },
            "groups": self.panels.sidebar(),
            "initial": self.panels.initial(),
            "missing": self.panels.missing(),
            "recorder": {
                "enabled": self.recorder.enabled,
                "buffer": self.config.recorder.buffer,
                "uptime": int(self.recorder.uptime_seconds),
                "totals": self.recorder.totals(),
            },
        }

    def panel(self, panel_id: str) -> dict[str, Any] | None:
        """One panel's current contents.

        Args:
            panel_id: The panel's id.

        Returns:
            The rendered panel, or None when it does not exist — which is the
            same answer for a panel that was never declared and one whose
            watcher is not live, because from outside they are the same thing.
        """
        rendered = self.panels.build(panel_id)
        return rendered.to_dict() if rendered is not None else None

    def request(self, request_id: str) -> dict[str, Any] | None:
        """One request, and everything it caused.

        Args:
            request_id: The request's id.

        Returns:
            The request and its correlated events, or None when it has fallen
            out of the ring.
        """
        event = self.recorder.store.request(request_id)
        if event is None:
            return None

        correlated = self.recorder.store.correlated(request_id)
        grouped: dict[str, list[dict[str, Any]]] = {kind.value: [] for kind in _DETAIL_KINDS}

        for caused in correlated:
            bucket = grouped.get(caused.kind.value)
            if bucket is not None:
                bucket.append(caused.to_dict())

        return {
            "request": event.to_dict(),
            "caused": {kind: rows for kind, rows in grouped.items() if rows},
            "counts": {kind: len(rows) for kind, rows in grouped.items() if rows},
        }

    def events(self, kind: str, limit: int = 50) -> dict[str, Any] | None:
        """Recent events of one kind, as raw data.

        For the interface's detail views, which want the whole event rather
        than the table cell a panel reduced it to.

        Args:
            kind: The kind's name.
            limit: How many to return.

        Returns:
            The events, or None when the kind is not one vise records.
        """
        try:
            wanted = EventKind(kind)
        except ValueError:
            return None

        return {
            "kind": wanted.value,
            "events": [
                event.to_dict()
                for event in self.recorder.store.recent(wanted, limit=min(limit, 200))
            ],
            "total": self.recorder.store.count(wanted),
            "retained": self.recorder.store.retained(wanted),
        }

    def action(self, name: str) -> dict[str, Any] | None:
        """Perform one of the recorder's own actions.

        These are the only writes the dashboard offers, and they are all about
        the recorder rather than about the application. A dashboard that could
        retry a job or flush a cache would be an interesting product and a
        different one; a development server's dashboard changing production
        state by accident is a story nobody wants to be in.

        Args:
            name: ``pause``, ``resume`` or ``clear``.

        Returns:
            The recorder's new state, or None when the action is unknown.
        """
        if name == "pause":
            self.recorder.pause()
        elif name == "resume":
            self.recorder.resume()
        elif name == "clear":
            self.recorder.store.clear()
        else:
            return None

        return {
            "enabled": self.recorder.enabled,
            "totals": self.recorder.totals(),
        }


def _app_title(app: Any) -> str:
    """What to call the application.

    Args:
        app: The application.

    Returns:
        Its title, or a generic name.
    """
    if app is None:
        return "Application"
    return str(getattr(app, "title", "") or "Application")
