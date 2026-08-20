"""
sillo_vise.watchers.registry — probe everything, attach what can see something.

This is where "live only" is enforced. Every watcher is probed against the
running application; the ones that can observe something are attached and their
panels appear, and the ones that cannot are left out with a reason a person can
act on.

Probing costs something — the queue probe opens a connection — so it happens at
startup and then on an interval, not per request. The interval is what lets a
panel appear when Redis comes up rather than at the next restart.

Nothing here raises. A watcher whose probe is wrong, or whose attach fails
against a framework version it did not expect, must cost its own panel and
nothing else: a dashboard that refuses to start because one collector is
unhappy is worse than no dashboard.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Iterator
from typing import Any

from ..config import PanelConfig
from ..recorder import Recorder
from .base import Availability, Watcher, unavailable
from .cache import CacheWatcher
from .exceptions import ExceptionWatcher
from .logs import LogWatcher
from .mail import MailWatcher
from .outgoing import OutgoingWatcher
from .queries import QueryWatcher
from .queues import QueueWatcher
from .realtime import RealtimeWatcher
from .schedules import ScheduleWatcher
from .workers import WorkerWatcher

__all__ = ["WatcherRegistry", "WatcherState", "default_watchers"]

logger = logging.getLogger("sillo_vise")


def default_watchers() -> list[Watcher]:
    """Every optional watcher, in the order their panels are grouped.

    The request watcher is not here. It is not optional — without it there is
    no request context for anything else to correlate against — so the server
    installs it before this registry is consulted.

    Returns:
        Freshly built watchers.
    """
    return [
        QueryWatcher(),
        CacheWatcher(),
        OutgoingWatcher(),
        QueueWatcher(),
        WorkerWatcher(),
        ScheduleWatcher(),
        ExceptionWatcher(),
        LogWatcher(),
        RealtimeWatcher(),
        MailWatcher(),
    ]


@dataclasses.dataclass(slots=True)
class WatcherState:
    """One watcher, and what the last probe said about it.

    Attributes:
        watcher: The watcher itself.
        availability: What the last probe answered.
        attached: Whether it is currently collecting.
        probed_at: When it was last probed.
        disabled: Whether the project turned this panel off in ``.vise``.
    """

    watcher: Watcher
    availability: Availability
    attached: bool = False
    probed_at: float = 0.0
    disabled: bool = False

    @property
    def name(self) -> str:
        """The watcher's name, which is also its panel's id.

        Returns:
            The name.
        """
        return self.watcher.name

    @property
    def live(self) -> bool:
        """Whether this watcher's panel should appear.

        Returns:
            True when the watcher is attached and not disabled.
        """
        return self.attached and not self.disabled

    def to_dict(self) -> dict[str, Any]:
        """Render for ``vise panels`` and the dashboard.

        Returns:
            The name, whether it is live, and why not when it is not.
        """
        return {
            "name": self.name,
            "live": self.live,
            "available": self.availability.ok,
            "disabled": self.disabled,
            "detail": self.availability.detail,
            "requires": self.watcher.requires,
        }


class WatcherRegistry:
    """Holds every watcher, and decides which of them collect.

    Attributes:
        app: The application being watched.
        recorder: Where events go.
        panels: The ``[panels]`` configuration, which can disable one.
        states: Every watcher, and its last probe.
    """

    __slots__ = ("app", "recorder", "panels", "states", "_probed_at")

    def __init__(
        self,
        app: Any,
        recorder: Recorder,
        panels: PanelConfig | None = None,
        watchers: list[Watcher] | None = None,
    ) -> None:
        """Build a registry.

        Args:
            app: The application to watch.
            recorder: Where events go.
            panels: The ``[panels]`` configuration.
            watchers: The watchers to consider. Defaults to
                :func:`default_watchers`.
        """
        self.app = app
        self.recorder = recorder
        self.panels = panels or PanelConfig()

        disabled = set(self.panels.disable)
        self.states = [
            WatcherState(
                watcher=watcher,
                availability=unavailable("not probed"),
                disabled=watcher.name in disabled,
            )
            for watcher in (watchers if watchers is not None else default_watchers())
        ]
        self._probed_at = 0.0

    def probe(self) -> list[WatcherState]:
        """Probe every watcher, attaching the ones that can see something.

        Returns:
            The states, in registration order.
        """
        now = time.time()

        for state in self.states:
            if state.disabled:
                continue

            state.availability = state.watcher.safe_probe(self.app)
            state.probed_at = now

            if state.availability.ok and not state.attached:
                self._attach(state)
            elif not state.availability.ok and state.attached:
                self._detach(state)

        self._probed_at = now
        return self.states

    def probe_if_due(self) -> list[WatcherState]:
        """Probe again, if enough time has passed since the last one.

        Args:
            None.

        Returns:
            The states, whether or not a probe ran.
        """
        if time.time() - self._probed_at >= self.panels.probe_seconds:
            return self.probe()
        return self.states

    def _attach(self, state: WatcherState) -> None:
        """Attach one watcher, swallowing a failure into its own panel.

        Args:
            state: The watcher's state.
        """
        try:
            state.watcher.attach(self.app, self.recorder)
        except Exception as error:  # noqa: BLE001 - one collector, not the tool
            logger.warning(
                "vise: the %s watcher could not attach: %s", state.name, error
            )
            state.availability = unavailable(f"could not attach: {error}")
            return

        state.attached = True

    def _detach(self, state: WatcherState) -> None:
        """Detach one watcher, swallowing a failure.

        Args:
            state: The watcher's state.
        """
        try:
            state.watcher.detach()
        except Exception as error:  # noqa: BLE001 - detaching is best-effort
            logger.warning(
                "vise: the %s watcher could not detach: %s", state.name, error
            )

        state.attached = False

    def detach_all(self) -> None:
        """Detach every watcher, at shutdown."""
        for state in self.states:
            if state.attached:
                self._detach(state)

    def get(self, name: str) -> Watcher | None:
        """One watcher by name.

        Args:
            name: The watcher's name.

        Returns:
            The watcher, or None.
        """
        for state in self.states:
            if state.name == name:
                return state.watcher
        return None

    def state_of(self, name: str) -> WatcherState | None:
        """One watcher's state by name.

        Args:
            name: The watcher's name.

        Returns:
            The state, or None.
        """
        for state in self.states:
            if state.name == name:
                return state
        return None

    def live(self) -> list[str]:
        """The names of the watchers currently collecting.

        Returns:
            The names, in registration order.
        """
        return [state.name for state in self.states if state.live]

    def missing(self) -> list[WatcherState]:
        """The watchers that are not collecting, and why.

        Returns:
            Their states.
        """
        return [state for state in self.states if not state.live]

    def __iter__(self) -> Iterator[WatcherState]:
        """Iterate every state.

        Returns:
            An iterator in registration order.
        """
        return iter(self.states)

    def __len__(self) -> int:
        """How many watchers are registered.

        Returns:
            The count.
        """
        return len(self.states)

    def __repr__(self) -> str:
        """A short description for debugging.

        Returns:
            How many watchers are live, out of how many.
        """
        return f"WatcherRegistry({len(self.live())} live of {len(self.states)})"
