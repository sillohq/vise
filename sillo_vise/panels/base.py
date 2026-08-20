"""
sillo_vise.panels.base — what a panel is, and what it is allowed to know.

A panel turns the store into the four things the interface draws: stat tiles, a
chart, a table and a side rail. It reads; it never collects. The separation is
what stops the dashboard growing a second, slightly different, measurement of
something a watcher already measures.

Each panel names the watcher it needs. That is the whole of the "live only"
rule at this layer: the registry asks which watchers are collecting, and a
panel whose watcher is not among them is never built and never appears in the
sidebar. A panel therefore never has to render an empty state for a subsystem
that does not exist — only for one that exists and has been quiet.

The rendered shape deliberately matches the mockups on sillo.build/foreman
field for field. Tiles carry a label, a value, a delta, a tone and a sparkline;
a table carries columns with responsive classes and rows of strings; the side
rail carries name, detail and state triples. The interface is then a renderer
with no opinions of its own, and a panel is the only place that decides what a
number means.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

from ..config import ViseConfig
from ..recorder import Recorder, Store
from ..watchers import WatcherRegistry

__all__ = ["Panel", "PanelContext", "Rendered"]


@dataclasses.dataclass(slots=True)
class PanelContext:
    """Everything a panel is allowed to read.

    Passed in rather than reached for, so that what a panel can see is visible
    in one place and a panel cannot quietly start collecting.

    Attributes:
        store: Recorded events and the time series.
        recorder: The recorder, for its uptime and lifetime counts.
        registry: The watchers, so a panel can reach the one that feeds it.
        config: The resolved configuration.
        app: The application, for the panels that read it directly.
        panels: The panel registry, set by it after construction. Only the
            Config panel needs it, and it needs it for a reason worth stating:
            *watchers* and *panels* are not the same count. Five watchers feed
            nine panels, and a tile labelled "Panels" that reported the watcher
            count was quietly wrong.
    """

    store: Store
    recorder: Recorder
    registry: WatcherRegistry
    config: ViseConfig
    app: Any = None
    panels: Any = None

    def watcher(self, name: str) -> Any:
        """The watcher of a given name, when it is collecting.

        Args:
            name: The watcher's name.

        Returns:
            The watcher, or None.
        """
        state = self.registry.state_of(name)
        return state.watcher if state is not None and state.live else None


@dataclasses.dataclass(slots=True)
class Rendered:
    """One panel's current contents.

    Attributes:
        id: The panel's id.
        kind: The event kind this panel's rows are, when they are events. What
            a row click opens, and empty for a panel whose rows are summaries.
        tiles: Stat tiles, four across.
        chart: A bar chart, when the panel has one.
        table: The main table.
        aside: The side rail, when the panel has one.
        toolbar: Chips above the table, for panels that are tools rather than
            monitors.
        note: A line shown in place of data, when there is a reason the panel
            is quiet that a reader should know — "workers run out of process",
            rather than an empty table that looks like a fault.
    """

    id: str
    kind: str = ""
    tiles: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    chart: dict[str, Any] | None = None
    table: dict[str, Any] | None = None
    aside: dict[str, Any] | None = None
    toolbar: list[str] = dataclasses.field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Render for the dashboard.

        Returns:
            The panel's contents as plain data.
        """
        return dataclasses.asdict(self)


class Panel:
    """One screen of the dashboard.

    Attributes:
        id: Stable identifier, used in the URL and by ``[panels] disable``.
        name: What the sidebar calls it.
        group: Which sidebar group it sits in.
        icon: Name of the icon the interface draws.
        summary: One line, shown under the heading.
        watcher: The watcher this panel needs. None means the panel reads the
            application directly and is always available.
    """

    id: str = ""
    name: str = ""
    group: str = "Monitor"
    icon: str = "grid"
    summary: str = ""
    watcher: str | None = None

    @property
    def crumb(self) -> str:
        """The breadcrumb above the heading.

        Returns:
            ``Group / Name``.
        """
        return f"{self.group} / {self.name}"

    def describe(self) -> dict[str, Any]:
        """The panel's identity, for the sidebar.

        Returns:
            Everything the interface needs before it has any data.
        """
        return {
            "id": self.id,
            "name": self.name,
            "group": self.group,
            "icon": self.icon,
            "crumb": self.crumb,
            "summary": self.summary,
            "watcher": self.watcher,
        }

    def badge(self, context: PanelContext) -> str:
        """A count shown beside the panel's name in the sidebar.

        Args:
            context: What the panel may read.

        Returns:
            The badge, or an empty string for no badge.
        """
        return ""

    def build(self, context: PanelContext) -> Rendered:
        """Assemble the panel's current contents.

        Args:
            context: What the panel may read.

        Returns:
            The tiles, chart, table and side rail.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        """A short description for debugging.

        Returns:
            The id and the watcher it needs.
        """
        return f"{type(self).__name__}(id={self.id!r}, watcher={self.watcher!r})"


def columns(*specs: tuple[str, str] | str | dict[str, str]) -> list[dict[str, str]]:
    """Build a table's column list.

    A column may carry a responsive class, which is how the mockups hide the
    less important columns on a narrow window rather than letting the table
    scroll sideways. It may also be marked as a *state* column with
    :func:`state_column`, which is what decides whether its cells get a coloured pill.

    Args:
        *specs: A label, a label and the class that hides it, or a column
            already built by :func:`state_column`.

    Returns:
        The columns.
    """
    built: list[dict[str, str]] = []
    for spec in specs:
        if isinstance(spec, dict):
            built.append(spec)
        elif isinstance(spec, tuple):
            built.append({"label": spec[0], "cls": spec[1]})
        else:
            built.append({"label": spec})
    return built


def state_column(label: str, cls: str = "") -> dict[str, str]:
    """Mark a column as holding a state, so its cells are drawn as pills.

    The alternative — letting the interface decide from the cell's text — is
    what the first version did, and it coloured a recorder buffer of ``500`` as
    an HTTP server error, because `500` looks like a status code when you have
    no idea what column you are in. A state is a property of the column, and the
    panel is the only thing that knows.

    Args:
        label: The column's heading.
        cls: The responsive class that hides it, if any.

    Returns:
        The column.
    """
    column = {"label": label, "kind": "state"}
    if cls:
        column["cls"] = cls
    return column


def table(
    columns_: Sequence[dict[str, str]],
    rows: Sequence[Sequence[Any]],
    ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a table.

    Args:
        columns_: The columns.
        rows: The rows, whose cells are coerced to text here so a panel does
            not have to remember to.
        ids: One event id per row, parallel to *rows*. A row with an id is
            clickable and opens that event; a row without one is not. Rows that
            are summaries rather than events — a queue, a channel, a
            configuration key — have no id, and pretending otherwise would give
            a reader something to click that leads nowhere.

    Returns:
        The table.
    """
    built = {
        "columns": list(columns_),
        "rows": [["" if cell is None else str(cell) for cell in row] for row in rows],
    }

    if ids is not None:
        built["ids"] = list(ids)

    return built
