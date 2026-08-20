"""
Panels: what exists, what it says, and what it refuses to say.

The promise being tested is that a panel exists only when it can show something
true. So most of these build a real application, probe it, and assert on which
panels came back — not on their contents.
"""

from __future__ import annotations

import time

import pytest
from sillo import SilloApp
from sillo.mail.client import setup_mail

from sillo_vise.config import PanelConfig, RecorderConfig, ViseConfig
from sillo_vise.panels import GROUPS, PanelRegistry, all_panels
from sillo_vise.panels.base import Rendered
from sillo_vise.panels.tiles import TONE_GOOD, TONE_WARN, duration_tile, trend_tile
from sillo_vise.recorder import Recorder, Series
from sillo_vise.watchers import WatcherRegistry


@pytest.fixture
def app() -> SilloApp:
    application = SilloApp(title="panels")

    async def home(request, response):
        return response.json({})

    application.get("/", handler=home, name="web.home")
    return application


@pytest.fixture
def registry(app):
    recorder = Recorder(RecorderConfig(buffer=200))
    watchers = WatcherRegistry(app, recorder)
    watchers.probe()
    yield PanelRegistry(watchers, recorder, ViseConfig(), app)
    watchers.detach_all()


class TestDeclaration:
    def test_fourteen_panels_are_declared(self):
        assert len(all_panels()) == 14

    def test_every_panel_has_an_id(self):
        assert all(panel.id for panel in all_panels())

    def test_ids_are_unique(self):
        ids = [panel.id for panel in all_panels()]
        assert len(ids) == len(set(ids))

    def test_every_panel_is_in_a_known_group(self):
        assert all(panel.group in GROUPS for panel in all_panels())

    def test_every_panel_has_a_summary(self):
        assert all(panel.summary for panel in all_panels())

    def test_the_crumb_is_the_group_and_the_name(self):
        panel = all_panels()[0]
        assert panel.crumb == f"{panel.group} / {panel.name}"


class TestLiveOnly:
    def test_a_panel_without_its_watcher_does_not_exist(self, registry):
        assert registry.get("queries") is None

    def test_a_panel_needing_no_watcher_always_exists(self, registry):
        assert registry.get("routes") is not None

    def test_a_panel_that_does_not_exist_cannot_be_built(self, registry):
        assert registry.build("queues") is None

    def test_a_panel_that_does_not_exist_is_not_in_the_sidebar(self, registry):
        shown = {panel["id"] for group in registry.sidebar() for panel in group["panels"]}
        assert "mail" not in shown

    def test_an_empty_group_is_dropped(self, registry):
        """A project with no database, queue or mail should see a shorter
        sidebar, not three empty headings."""
        assert "Work" not in {group["name"] for group in registry.sidebar()}

    def test_groups_come_out_in_the_declared_order(self, registry):
        names = [group["name"] for group in registry.sidebar()]
        assert names == [name for name in GROUPS if name in names]

    def test_a_missing_panel_carries_a_reason(self, registry):
        reasons = {entry["id"]: entry["reason"] for entry in registry.missing()}
        assert "database" in reasons["queries"]

    def test_mail_appears_once_it_is_configured(self, app):
        setup_mail(app)
        recorder = Recorder()
        watchers = WatcherRegistry(app, recorder)
        watchers.probe()
        try:
            assert PanelRegistry(watchers, recorder, ViseConfig(), app).get("mail")
        finally:
            watchers.detach_all()


class TestDisabling:
    def test_a_disabled_panel_does_not_exist(self, app):
        recorder = Recorder()
        watchers = WatcherRegistry(app, recorder)
        watchers.probe()
        config = ViseConfig(panels=PanelConfig(disable=("routes",)))
        try:
            assert PanelRegistry(watchers, recorder, config, app).get("routes") is None
        finally:
            watchers.detach_all()

    def test_a_disabled_panel_says_so(self, app):
        recorder = Recorder()
        watchers = WatcherRegistry(app, recorder)
        watchers.probe()
        config = ViseConfig(panels=PanelConfig(disable=("routes",)))
        try:
            registry = PanelRegistry(watchers, recorder, config, app)
            reasons = {entry["id"]: entry["reason"] for entry in registry.missing()}
            assert reasons["routes"] == "disabled in .vise"
        finally:
            watchers.detach_all()


class TestRendering:
    def test_every_live_panel_builds(self, registry):
        for panel in registry.live():
            assert isinstance(registry.build(panel.id), Rendered)

    def test_a_panel_builds_before_any_traffic(self, registry):
        """A development server is looked at before it has served anything,
        which is exactly when a divide-by-zero would surface."""
        assert registry.build("overview").tiles

    def test_tiles_are_four_across(self, registry):
        for panel in registry.live():
            rendered = registry.build(panel.id)
            assert len(rendered.tiles) in (0, 4)

    def test_table_rows_are_all_strings(self, registry):
        rendered = registry.build("routes")
        assert all(isinstance(cell, str) for row in rendered.table["rows"] for cell in row)

    def test_a_row_has_a_cell_per_column(self, registry):
        rendered = registry.build("routes")
        width = len(rendered.table["columns"])
        assert all(len(row) == width for row in rendered.table["rows"])

    def test_a_broken_panel_costs_only_itself(self, registry):
        class Broken:
            id = "broken"
            name = "Broken"
            group = "Tools"
            icon = "grid"
            summary = ""
            watcher = None

            def describe(self):
                return {"id": self.id}

            def badge(self, context):
                return ""

            def build(self, context):
                raise RuntimeError("nope")

        registry.panels.append(Broken())
        rendered = registry.build("broken")
        assert "nope" in rendered.note
        assert registry.build("routes").table is not None


class TestContents:
    def test_a_recorded_request_reaches_the_requests_table(self, registry):
        registry.context.recorder.request(
            method="GET", path="/documents", status=200, duration_ms=38.0
        )
        rows = registry.build("requests").table["rows"]
        assert rows[0][:3] == ["GET", "/documents", "200"]

    def test_the_overview_health_rail_lists_only_live_watchers(self, registry):
        names = {row[0] for row in registry.build("overview").aside["rows"]}
        assert "queries" not in names and "logs" in names

    def test_the_config_panel_hides_the_token(self, app):
        from sillo_vise.config import DashboardConfig

        recorder = Recorder()
        watchers = WatcherRegistry(app, recorder)
        watchers.probe()
        config = ViseConfig(dashboard=DashboardConfig(token="hunter2"))
        try:
            registry = PanelRegistry(watchers, recorder, config, app)
            values = {row[0]: row[1] for row in registry.build("config").table["rows"]}
            assert values["dashboard.token"] == "***"
        finally:
            watchers.detach_all()

    def test_the_config_panel_says_why_each_panel_is_missing(self, registry):
        rows = {row[0]: row[1] for row in registry.build("config").aside["rows"]}
        assert "database" in rows["queries"]

    def test_the_routes_panel_lists_the_applications_routes(self, registry):
        paths = {row[1] for row in registry.build("routes").table["rows"]}
        assert "/" in paths

    def test_the_exceptions_panel_groups_repeats(self, registry):
        for _ in range(4):
            try:
                raise ValueError("same")
            except ValueError as error:
                registry.context.recorder.exception(error)

        rows = registry.build("exceptions").table["rows"]
        assert len(rows) == 1 and rows[0][2] == "4"

    def test_the_exceptions_badge_counts_groups(self, registry):
        try:
            raise KeyError("k")
        except KeyError as error:
            registry.context.recorder.exception(error)

        panel = registry.get("exceptions")
        assert panel.badge(registry.context) == "1"

    def test_the_initial_panel_is_the_first_live_one(self, registry):
        assert registry.initial() == "overview"


class TestTones:
    def test_a_falling_latency_is_good_news(self):
        series = Series("x")
        for _ in range(20):
            series.add(400.0, at=time.time() - 400)
        for _ in range(20):
            series.add(10.0)

        assert duration_tile("p95", series)["tone"] == TONE_GOOD

    def test_a_rising_latency_is_not(self):
        series = Series("x")
        for _ in range(20):
            series.add(10.0, at=time.time() - 400)
        for _ in range(20):
            series.add(400.0)

        assert duration_tile("p95", series)["tone"] == TONE_WARN

    def test_a_rising_queue_is_not_good_news(self):
        """Colouring by direction rather than by meaning would make a queue
        draining look like a problem, which is the one thing an operations
        dashboard must never do."""
        series = Series("x")
        for _ in range(20):
            series.add(1.0)

        assert trend_tile("Queue size", "20", series, higher_is_better=False)["tone"] == TONE_WARN

    def test_a_rising_throughput_is(self):
        series = Series("x")
        for _ in range(20):
            series.add(1.0)

        assert trend_tile("Requests", "20", series, higher_is_better=True)["tone"] == TONE_GOOD
