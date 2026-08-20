"""
sillo_vise.watchers.base — what a watcher is, and how it proves it can see anything.

A watcher is a small object over a hook the framework already has. It does not
own storage, does not decide what is a secret and does not render anything; it
builds an event and hands it to the recorder. That is what keeps this one
dashboard rather than fourteen small products.

Every watcher answers one question before it is attached: *can I actually
observe this application?* A queue watcher on a project with no Redis cannot,
and vise's answer to that is to leave the panel out rather than to show a panel
full of zeroes. So :meth:`Watcher.probe` returns an :class:`Availability` —
either yes with a detail line, or no with the reason, which is what ``vise
panels`` prints and what the dashboard shows in place of a missing panel.

Probes re-run on an interval. A panel should appear when Redis comes up, not at
the next restart.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from ..recorder import Recorder

__all__ = ["Availability", "Watcher", "available", "unavailable"]


@dataclasses.dataclass(frozen=True, slots=True)
class Availability:
    """Whether a watcher can observe this application, and why.

    Attributes:
        ok: Whether the watcher can attach.
        detail: One line. When *ok*, what it found — ``"redis 7.4"``. When not,
            what is missing, phrased so a reader knows what to do about it.
    """

    ok: bool
    detail: str = ""

    def __bool__(self) -> bool:
        """Whether the watcher can attach.

        Returns:
            :attr:`ok`.
        """
        return self.ok


def available(detail: str = "") -> Availability:
    """Say a watcher can attach.

    Args:
        detail: What it found.

    Returns:
        A positive availability.
    """
    return Availability(True, detail)


def unavailable(reason: str) -> Availability:
    """Say a watcher cannot attach.

    Args:
        reason: What is missing. This is shown to a person deciding whether
            they wanted the panel, so it should name the thing, not the
            symptom: "no queue backend configured", not "probe failed".

    Returns:
        A negative availability.
    """
    return Availability(False, reason)


class Watcher:
    """One collection path, over one hook the framework already has.

    Subclasses implement :meth:`probe` and :meth:`attach`, and override
    :meth:`detach` when attaching left something behind.

    Attributes:
        name: Matches the id of the panel this feeds, so that a missing panel
            and the watcher that would have fed it are obviously the same
            thing.
        recorder: Set by :meth:`attach`.
        attached: Whether the hook is currently installed.
    """

    #: Matches the panel this watcher feeds.
    name: str = ""

    #: What this watcher needs, in prose, for ``vise doctor``.
    requires: str = ""

    __slots__ = ("recorder", "attached")

    def __init__(self) -> None:
        """Build a detached watcher."""
        self.recorder: Recorder | None = None
        self.attached = False

    def probe(self, app: Any) -> Availability:
        """Report whether this watcher can observe *app*.

        Must not raise. A probe that throws because an optional dependency is
        missing is a probe that answers the question it was asked, badly — so
        subclasses catch, and :meth:`safe_probe` catches whatever they miss.

        Args:
            app: The application.

        Returns:
            Whether the watcher can attach, and why.
        """
        return unavailable("not implemented")

    def safe_probe(self, app: Any) -> Availability:
        """Probe, turning any exception into an unavailability.

        A watcher whose probe raises must not take the dashboard down with it.
        The exception becomes the reason the panel is missing, which is both
        honest and the fastest way to find out that a probe is wrong.

        Args:
            app: The application.

        Returns:
            The probe's answer, or the exception as a reason.
        """
        try:
            return self.probe(app)
        except Exception as error:  # noqa: BLE001 - a probe must never propagate
            return unavailable(f"probe failed: {type(error).__name__}: {error}")

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Install the hook.

        Only called after :meth:`probe` said yes.

        Args:
            app: The application.
            recorder: Where events go.
        """
        self.recorder = recorder
        self.attached = True

    def detach(self) -> None:
        """Remove the hook, if it left something behind."""
        self.attached = False
        self.recorder = None

    def __repr__(self) -> str:
        """A short description for debugging.

        Returns:
            The name and whether it is attached.
        """
        state = "attached" if self.attached else "detached"
        return f"{type(self).__name__}({self.name!r}, {state})"
