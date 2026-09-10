"""
Installation order, which is where the subtle failures live.

sillo builds its middleware chain inside-out — the last registered runs first —
so getting this backwards produces a dashboard that records itself and an
application whose requests are missing from their own log. Both look almost
right, which is why they are tested rather than reasoned about.
"""

from __future__ import annotations

import pytest
from sillo import SilloApp, json
from sillo.testclient import TestClient

from sillo_vise.config import (
    DashboardConfig,
    LogConfig,
    PanelConfig,
    RecorderConfig,
    ViseConfig,
)
from sillo_vise.recorder import EventKind
from sillo_vise.server.install import install

PREFIX = "/__sillo/foreman"


@pytest.fixture
def app() -> SilloApp:
    application = SilloApp(title="Install test")

    async def home(ctx):
        return json({"ok": True})

    application.get("/", handler=home, name="web.home")
    return application


def config(**overrides) -> ViseConfig:
    base = {
        "dashboard": DashboardConfig(access="open"),
        "logs": LogConfig(banner=False, access=False),
        "recorder": RecorderConfig(buffer=50),
    }
    base.update(overrides)
    return ViseConfig(**base)


class TestOrdering:
    def test_the_dashboard_answers_before_the_recorder(self, app):
        """Registered last, so it runs first. Its own requests never reach the
        recorder at all, rather than being recorded and filtered out later."""
        installation = install(app, config())
        try:
            with TestClient(app) as client:
                client.get(f"{PREFIX}/api/meta")
            assert installation.recorder.store.count(EventKind.REQUEST) == 0
        finally:
            installation.shutdown()

    def test_the_recorder_measures_the_application_not_the_dashboard(self, app):
        installation = install(app, config())
        try:
            with TestClient(app) as client:
                client.get("/")
            events = installation.recorder.store.all(EventKind.REQUEST)
            assert [event.path for event in events] == ["/"]
        finally:
            installation.shutdown()

    def test_the_application_still_answers_normally(self, app):
        installation = install(app, config())
        try:
            with TestClient(app) as client:
                assert client.get("/").json() == {"ok": True}
        finally:
            installation.shutdown()


class TestRecorderOff:
    def test_nothing_is_constructed(self, app):
        installation = install(app, config(recorder=RecorderConfig(enabled=False)))
        assert (installation.recorder, installation.watchers) == (None, None)

    def test_no_dashboard_either(self, app):
        """Without a recorder there is nothing for the dashboard to show."""
        installation = install(app, config(recorder=RecorderConfig(enabled=False)))
        assert installation.panels is None
        assert installation.dashboard_url == ""

    def test_the_notes_say_so(self, app):
        installation = install(app, config(recorder=RecorderConfig(enabled=False)))
        assert "recorder off" in installation.notes()


class TestDashboardOff:
    def test_the_recorder_still_runs(self, app):
        installation = install(app, config(dashboard=DashboardConfig(enabled=False)))
        try:
            with TestClient(app) as client:
                client.get("/")
            assert installation.recorder.store.count(EventKind.REQUEST) == 1
        finally:
            installation.shutdown()

    def test_the_prefix_is_not_claimed(self, app):
        installation = install(app, config(dashboard=DashboardConfig(enabled=False)))
        try:
            with TestClient(app) as client:
                assert client.get(f"{PREFIX}/api/meta").status_code == 404
        finally:
            installation.shutdown()

    def test_and_that_request_is_recorded_like_any_other(self, app):
        """With no dashboard there is no reason to exclude its prefix."""
        installation = install(app, config(dashboard=DashboardConfig(enabled=False)))
        try:
            with TestClient(app) as client:
                client.get(f"{PREFIX}/api/meta")
            assert installation.recorder.store.count(EventKind.REQUEST) == 1
        finally:
            installation.shutdown()


class TestStartupProbe:
    def test_watchers_are_probed_again_once_the_application_has_started(self, app):
        """The first probe runs before startup, when a database has no
        connections open and a scheduler has not started."""
        installation = install(app, config())
        try:
            before = installation.watchers.state_of("logs").probed_at
            with TestClient(app):
                pass
            assert installation.watchers.state_of("logs").probed_at >= before
        finally:
            installation.shutdown()


class TestReporting:
    def test_the_dashboard_url_is_one_a_browser_can_open(self, app):
        """0.0.0.0 means every interface to a server and nothing to a browser."""
        from sillo_vise.config import ServerConfig

        installation = install(app, config(server=ServerConfig(host="0.0.0.0")))
        try:
            assert "127.0.0.1" in installation.dashboard_url
        finally:
            installation.shutdown()

    def test_the_live_panels_are_listed(self, app):
        installation = install(app, config())
        try:
            assert "overview" in installation.live_panels
        finally:
            installation.shutdown()

    def test_the_missing_panels_carry_reasons(self, app):
        installation = install(app, config())
        try:
            assert all(entry["reason"] for entry in installation.missing_panels)
        finally:
            installation.shutdown()

    def test_a_disabled_panel_is_not_live(self, app):
        installation = install(app, config(panels=PanelConfig(disable=("routes",))))
        try:
            assert "routes" not in installation.live_panels
        finally:
            installation.shutdown()

    def test_shutdown_detaches_everything(self, app):
        installation = install(app, config())
        installation.shutdown()
        assert installation.watchers.live() == []
