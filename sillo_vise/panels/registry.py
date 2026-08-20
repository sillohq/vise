"""
sillo_vise.panels.registry — assemble the sidebar, and only from what is live.

Fourteen panels are declared. How many exist at any moment is decided here, by
asking the watcher registry what is collecting: a panel naming a watcher that is
not live is not in the sidebar, not reachable by URL, and not built.

That last part matters for more than tidiness. A panel that cannot be built is a
panel that cannot raise while being built, so a project without Redis cannot
produce a traceback from the Queues panel — there is nothing to traceback from.

Groups come out in the order the Foreman page uses: Monitor, Work, Diagnose,
Tools. A group with no live panels is dropped, so a project with no database, no
queue and no mail sees a shorter sidebar rather than three empty headings.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from ..config import ViseConfig
from ..recorder import Recorder
from ..watchers import WatcherRegistry
from .base import Panel, PanelContext, Rendered
from .diagnose import ExceptionsPanel, LogsPanel, MailPanel, RealtimePanel
from .monitor import (
    CachePanel,
    OutgoingPanel,
    OverviewPanel,
    QueriesPanel,
    RequestsPanel,
)
from .tools import ConfigPanel, RoutesPanel
from .work import QueuesPanel, SchedulesPanel, WorkersPanel

__all__ = ["GROUPS", "PanelRegistry", "all_panels"]

logger = logging.getLogger("sillo_vise")

#: Sidebar groups, in the order the Foreman page uses them.
GROUPS = ("Monitor", "Work", "Diagnose", "Tools")


def all_panels() -> list[Panel]:
    """Every panel vise knows how to draw.

    Returns:
        Freshly built panels, in sidebar order.
    """
    return [
        OverviewPanel(),
        RequestsPanel(),
        QueriesPanel(),
        CachePanel(),
        OutgoingPanel(),
        QueuesPanel(),
        WorkersPanel(),
        SchedulesPanel(),
        ExceptionsPanel(),
        LogsPanel(),
        RealtimePanel(),
        MailPanel(),
        RoutesPanel(),
        ConfigPanel(),
    ]


class PanelRegistry:
    """Decides which panels exist, and builds them on request.

    Attributes:
        panels: Every declared panel, live or not.
        watchers: The watcher registry that decides which are live.
        context: What panels are allowed to read.
    """

    __slots__ = ("panels", "watchers", "context")

    def __init__(
        self,
        watchers: WatcherRegistry,
        recorder: Recorder,
        config: ViseConfig,
        app: Any = None,
        panels: list[Panel] | None = None,
    ) -> None:
        """Build a registry.

        Args:
            watchers: The watcher registry.
            recorder: The recorder, whose store the panels read.
            config: The resolved configuration.
            app: The application, for the panels that read it directly.
            panels: The panels to consider. Defaults to :func:`all_panels`.
        """
        self.panels = panels if panels is not None else all_panels()
        self.watchers = watchers
        self.context = PanelContext(
            store=recorder.store,
            recorder=recorder,
            registry=watchers,
            config=config,
            app=app,
        )

    def is_live(self, panel: Panel) -> bool:
        """Whether a panel should exist right now.

        Args:
            panel: The panel.

        Returns:
            True when it needs no watcher, or when the watcher it needs is
            collecting and the project has not disabled it.
        """
        if panel.id in self.context.config.panels.disable:
            return False

        if panel.watcher is None:
            return True

        state = self.watchers.state_of(panel.watcher)
        return state is not None and state.live

    def live(self) -> list[Panel]:
        """Every panel that currently exists.

        Returns:
            The panels, in sidebar order.
        """
        return [panel for panel in self.panels if self.is_live(panel)]

    def get(self, panel_id: str) -> Panel | None:
        """One live panel by id.

        A panel that is not live returns None rather than the declared object,
        so a URL cannot reach a panel the sidebar does not show.

        Args:
            panel_id: The panel's id.

        Returns:
            The panel, or None.
        """
        for panel in self.panels:
            if panel.id == panel_id and self.is_live(panel):
                return panel
        return None

    def build(self, panel_id: str) -> Rendered | None:
        """Build one panel's current contents.

        Args:
            panel_id: The panel's id.

        Returns:
            The rendered panel, or None when it does not exist.
        """
        panel = self.get(panel_id)
        if panel is None:
            return None

        try:
            return panel.build(self.context)
        except Exception as error:  # noqa: BLE001 - one panel, not the tool
            logger.warning("vise: the %s panel could not be built: %s", panel_id, error)
            return Rendered(
                id=panel_id,
                note=f"This panel could not be built: {type(error).__name__}: {error}",
            )

    def sidebar(self) -> list[dict[str, Any]]:
        """The groups and their live panels, for the interface.

        Returns:
            One entry per group that has at least one live panel.
        """
        live = self.live()
        groups = []

        for name in GROUPS:
            members = [panel for panel in live if panel.group == name]
            if not members:
                continue

            groups.append(
                {
                    "name": name,
                    "panels": [self._describe(panel) for panel in members],
                }
            )

        return groups

    def _describe(self, panel: Panel) -> dict[str, Any]:
        """One panel's sidebar entry, with its badge.

        Args:
            panel: The panel.

        Returns:
            Its description.
        """
        described = panel.describe()
        try:
            described["badge"] = panel.badge(self.context)
        except Exception:  # noqa: BLE001 - a badge is not worth a failure
            described["badge"] = ""
        return described

    def missing(self) -> list[dict[str, Any]]:
        """The panels that are not live, and why.

        Returns:
            One entry per declared panel that does not currently exist.
        """
        entries = []

        for panel in self.panels:
            if self.is_live(panel):
                continue

            if panel.id in self.context.config.panels.disable:
                reason = "disabled in .vise"
            else:
                state = self.watchers.state_of(panel.watcher or "")
                reason = state.availability.detail if state else "no watcher"

            entries.append(
                {
                    "id": panel.id,
                    "name": panel.name,
                    "group": panel.group,
                    "reason": reason,
                }
            )

        return entries

    def initial(self) -> str:
        """The panel the dashboard opens on.

        Returns:
            The first live panel's id, or an empty string when none are.
        """
        live = self.live()
        return live[0].id if live else ""

    def __iter__(self) -> Iterator[Panel]:
        """Iterate every declared panel.

        Returns:
            An iterator in sidebar order.
        """
        return iter(self.panels)

    def __len__(self) -> int:
        """How many panels are declared.

        Returns:
            The count, live or not.
        """
        return len(self.panels)

    def __repr__(self) -> str:
        """A short description for debugging.

        Returns:
            How many panels are live, out of how many.
        """
        return f"PanelRegistry({len(self.live())} live of {len(self.panels)})"
