"""
The dashboard, driven through a real application.

Every request here goes through the framework's TestClient into a real
``SilloApp`` with vise installed, so the middleware ordering, the prefix
matching and the access gate are all exercised the way they will be in
``vise serve``.
"""

from __future__ import annotations

import json

import pytest
from sillo import SilloApp
from sillo.testclient import TestClient

from sillo_vise.config import (
    DashboardConfig,
    LogConfig,
    RecorderConfig,
    ServerConfig,
    ViseConfig,
)
from sillo_vise.dashboard import AccessGate
from sillo_vise.server.install import install

PREFIX = "/__sillo/foreman"


def build(**dashboard) -> tuple[SilloApp, object]:
    """An application with vise installed, admitting the test client."""
    app = SilloApp(title="Dashboard test")

    async def home(request, response):
        return response.json({"ok": True})

    async def show(request, response, id):
        return response.json({"id": id})

    app.get("/", handler=home, name="web.home")
    app.get("/documents/{id}", handler=show, name="api.documents.show")

    config = ViseConfig(
        dashboard=DashboardConfig(**{"access": "open", **dashboard}),
        recorder=RecorderConfig(buffer=200),
        logs=LogConfig(access=False, banner=False),
        server=ServerConfig(reload=False),
    )
    return app, install(app, config)


@pytest.fixture
def client():
    app, installation = build()
    with TestClient(app) as test_client:
        yield test_client
    installation.shutdown()


def get(client, path: str):
    return client.get(f"{PREFIX}{path}")


class TestMounting:
    def test_the_dashboard_answers_under_its_prefix(self, client):
        assert get(client, "/api/meta").status_code == 200

    def test_the_application_still_works(self, client):
        assert client.get("/").json() == {"ok": True}

    def test_a_similar_path_is_not_claimed(self):
        """A prefix of /__sillo/foreman must not claim /__sillo/foremanager."""
        app, installation = build()

        async def other(request, response):
            return response.json({"mine": True})

        app.get("/__sillo/foremanager", handler=other, name="web.other")
        try:
            with TestClient(app) as test_client:
                assert test_client.get("/__sillo/foremanager").json() == {"mine": True}
        finally:
            installation.shutdown()

    def test_the_prefix_can_be_moved(self):
        app, installation = build(path="/_ops")
        try:
            with TestClient(app) as test_client:
                assert test_client.get("/_ops/api/meta").status_code == 200
        finally:
            installation.shutdown()


class TestMeta:
    def test_it_names_the_application(self, client):
        assert get(client, "/api/meta").json()["app"]["name"] == "Dashboard test"

    def test_it_reports_the_versions(self, client):
        versions = get(client, "/api/meta").json()["versions"]
        assert {"vise", "sillo", "python"} <= set(versions)

    def test_it_carries_the_sidebar(self, client):
        groups = get(client, "/api/meta").json()["groups"]
        assert "Monitor" in {group["name"] for group in groups}

    def test_the_sidebar_holds_only_live_panels(self, client):
        groups = get(client, "/api/meta").json()["groups"]
        shown = {panel["id"] for group in groups for panel in group["panels"]}
        assert "queries" not in shown

    def test_missing_panels_are_listed_with_reasons(self, client):
        missing = get(client, "/api/meta").json()["missing"]
        assert all(entry["reason"] for entry in missing)

    def test_it_names_the_panel_to_open_first(self, client):
        assert get(client, "/api/meta").json()["initial"] == "overview"


class TestPanels:
    def test_a_live_panel_is_served(self, client):
        assert get(client, "/api/panels/overview").status_code == 200

    def test_a_panel_that_does_not_exist_is_404(self, client):
        assert get(client, "/api/panels/queues").status_code == 404

    def test_an_unknown_panel_is_404(self, client):
        assert get(client, "/api/panels/nonsense").status_code == 404

    def test_a_panel_carries_its_tiles(self, client):
        assert len(get(client, "/api/panels/requests").json()["tiles"]) == 4

    def test_a_panel_carries_a_table_with_columns(self, client):
        table = get(client, "/api/panels/routes").json()["table"]
        assert table["columns"] and table["rows"]


class TestRequestCorrelation:
    def test_a_request_can_be_opened(self, client):
        client.get("/documents/8f21")
        events = get(client, "/api/events/request").json()["events"]
        detail = get(client, f"/api/requests/{events[0]['id']}")
        assert detail.json()["request"]["path"] == "/documents/8f21"

    def test_an_evicted_request_is_404(self, client):
        assert get(client, "/api/requests/nope").status_code == 404

    def test_the_caused_events_are_grouped_by_kind(self, client):
        app, installation = build()

        async def busy(request, response):
            installation.recorder.query("SELECT 1", duration_ms=1.0)
            installation.recorder.cache("k", result="hit")
            return response.json({})

        app.get("/busy", handler=busy, name="api.busy")
        try:
            with TestClient(app) as test_client:
                test_client.get("/busy")
                events = test_client.get(f"{PREFIX}/api/events/request").json()[
                    "events"
                ]
                caused = test_client.get(
                    f"{PREFIX}/api/requests/{events[0]['id']}"
                ).json()["caused"]
            assert set(caused) == {"query", "cache"}
        finally:
            installation.shutdown()


class TestTheDashboardIsNotInItsOwnCharts:
    def test_its_requests_are_not_recorded(self, client):
        get(client, "/api/meta")
        get(client, "/api/panels/overview")
        events = get(client, "/api/events/request").json()["events"]
        assert not any(event["path"].startswith(PREFIX) for event in events)

    def test_application_requests_are(self, client):
        client.get("/")
        events = get(client, "/api/events/request").json()["events"]
        assert any(event["path"] == "/" for event in events)


class TestAccess:
    def test_a_non_loopback_caller_is_refused_in_local_mode(self):
        """The TestClient reports no peer address, which local mode refuses —
        the safe answer when there is nothing to check."""
        app = SilloApp(title="gated")

        async def home(request, response):
            return response.json({})

        app.get("/", handler=home, name="web.home")
        config = ViseConfig(
            dashboard=DashboardConfig(access="local"),
            logs=LogConfig(access=False, banner=False),
        )
        installation = install(app, config)
        try:
            with TestClient(app) as test_client:
                assert test_client.get(f"{PREFIX}/api/meta").status_code == 403
        finally:
            installation.shutdown()

    def test_token_mode_admits_the_right_token(self):
        app, installation = build(access="token", token="hunter2")
        try:
            with TestClient(app) as test_client:
                response = test_client.get(
                    f"{PREFIX}/api/meta", headers={"authorization": "Bearer hunter2"}
                )
            assert response.status_code == 200
        finally:
            installation.shutdown()

    def test_token_mode_refuses_the_wrong_one(self):
        app, installation = build(access="token", token="hunter2")
        try:
            with TestClient(app) as test_client:
                response = test_client.get(
                    f"{PREFIX}/api/meta", headers={"authorization": "Bearer nope"}
                )
            assert response.status_code == 403
        finally:
            installation.shutdown()

    def test_a_refusal_does_not_say_which_mode_refused(self):
        app, installation = build(access="token", token="hunter2")
        try:
            with TestClient(app) as test_client:
                body = test_client.get(f"{PREFIX}/api/meta").json()
            assert "token" not in body["error"].lower()
        finally:
            installation.shutdown()

    def test_an_unknown_mode_fails_at_startup(self):
        with pytest.raises(ValueError, match="access must be one of"):
            AccessGate("everyone")

    def test_token_mode_without_a_token_fails_at_startup(self):
        """Silently falling back to open because the token was missing would be
        the worst possible outcome of a typo."""
        with pytest.raises(ValueError, match="needs a token"):
            AccessGate("token")

    def test_a_forwarded_header_does_not_satisfy_local_mode(self):
        gate = AccessGate("local")
        scope = {
            "headers": [(b"x-forwarded-for", b"127.0.0.1")],
            "client": ("8.8.8.8", 1),
        }
        assert not gate.allows(scope)

    def test_a_real_loopback_peer_does(self):
        assert AccessGate("local").allows(
            {"client": ("127.0.0.1", 5000), "headers": []}
        )


class TestSecurityHeaders:
    def test_the_dashboard_refuses_to_be_framed(self, client):
        assert get(client, "/api/meta").headers["x-frame-options"] == "DENY"

    def test_it_is_not_indexed(self, client):
        assert "noindex" in get(client, "/api/meta").headers["x-robots-tag"]

    def test_api_responses_are_not_cached(self, client):
        assert get(client, "/api/meta").headers["cache-control"] == "no-store"


class TestErrors:
    def test_an_unknown_endpoint_is_json(self, client):
        response = get(client, "/api/nonsense")
        assert response.status_code == 404
        assert json.loads(response.content)["error"]

    def test_an_unknown_page_gets_the_interface(self, client):
        """Anything not under /api is the interface's own client-side routing,
        so the browser gets the index and sorts it out."""
        assert get(client, "/requests/8f21").status_code == 200


class TestActions:
    def test_the_recorder_can_be_paused(self, client):
        assert client.post(f"{PREFIX}/api/actions/pause").json()["enabled"] is False
        client.post(f"{PREFIX}/api/actions/resume")

    def test_pausing_stops_recording(self, client):
        client.post(f"{PREFIX}/api/actions/pause")
        try:
            client.get("/")
            events = get(client, "/api/events/request").json()["events"]
            assert not any(event["path"] == "/" for event in events)
        finally:
            client.post(f"{PREFIX}/api/actions/resume")

    def test_clearing_empties_the_store(self, client):
        client.get("/")
        client.post(f"{PREFIX}/api/actions/clear")
        assert get(client, "/api/events/request").json()["events"] == []

    def test_an_unknown_action_is_404(self, client):
        assert client.post(f"{PREFIX}/api/actions/drop-database").status_code == 404

    def test_an_action_is_not_performed_on_a_get(self, client):
        """The dashboard is opened by clicking a link. A GET that cleared the
        store would make a bookmark destructive."""
        client.get("/")
        get(client, "/api/actions/clear")
        events = get(client, "/api/events/request").json()["events"]
        assert any(event["path"] == "/" for event in events)


class TestEvents:
    def test_a_known_kind_is_served(self, client):
        assert get(client, "/api/events/query").status_code == 200

    def test_an_unknown_kind_is_404(self, client):
        assert get(client, "/api/events/nonsense").status_code == 404

    def test_the_limit_is_clamped(self, client):
        for _ in range(5):
            client.get("/")
        assert (
            len(get(client, "/api/events/request?limit=99999").json()["events"]) <= 200
        )


class TestRecorderOff:
    def test_nothing_is_installed(self):
        app = SilloApp(title="off")

        async def home(request, response):
            return response.json({"ok": True})

        app.get("/", handler=home, name="web.home")
        config = ViseConfig(
            recorder=RecorderConfig(enabled=False),
            logs=LogConfig(access=False, banner=False),
        )
        installation = install(app, config)

        assert installation.recorder is None
        assert installation.panels is None

    def test_the_application_is_untouched(self):
        app = SilloApp(title="off")

        async def home(request, response):
            return response.json({"ok": True})

        app.get("/", handler=home, name="web.home")
        install(app, ViseConfig(recorder=RecorderConfig(enabled=False)))

        with TestClient(app) as test_client:
            assert test_client.get("/").json() == {"ok": True}
            assert test_client.get(f"{PREFIX}/api/meta").status_code == 404


class TestAssets:
    """The interface is a Vite build committed into the package."""

    def test_the_index_is_served_at_the_mount(self, client):
        response = get(client, "/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_the_index_is_never_cached(self, client):
        """A cached index serves the old bundle forever after a rebuild."""
        assert "no-store" in get(client, "/").headers["cache-control"]

    def test_a_hashed_asset_is_cached_forever(self):
        from sillo_vise.dashboard.assets import _is_hashed

        assert _is_hashed("index-Bgs7zFoq.css")

    def test_a_hash_containing_a_hyphen_is_still_recognised(self):
        """Vite's hashes are base64url and may contain a hyphen. Splitting on
        the last one leaves three characters and misses the hash entirely."""
        from sillo_vise.dashboard.assets import _is_hashed

        assert _is_hashed("index-BLYE-R0z.js")

    def test_a_deliberate_hyphenated_name_is_not(self):
        from sillo_vise.dashboard.assets import _is_hashed

        assert not _is_hashed("icons-outline.svg")

    def test_traversal_out_of_the_static_directory_is_refused(self):
        from sillo_vise.dashboard.assets import Assets

        assert Assets().find("/../../../../etc/passwd") is None

    def test_an_encoded_traversal_is_refused(self):
        from sillo_vise.dashboard.assets import Assets

        assert Assets().find("/..%2f..%2fetc/passwd") is None

    def test_a_missing_file_is_none(self):
        from sillo_vise.dashboard.assets import Assets

        assert Assets().find("/assets/never-existed.js") is None


class TestTrailingSlash:
    """The built index references its assets relatively, because the mount point
    is configurable. Relative only resolves under a trailing slash."""

    def test_the_bare_prefix_redirects(self, client):
        response = client.get(PREFIX, follow_redirects=False)
        assert response.status_code == 307

    def test_it_redirects_to_the_slash(self, client):
        response = client.get(PREFIX, follow_redirects=False)
        assert response.headers["location"] == f"{PREFIX}/"

    def test_the_redirect_is_not_cached(self):
        """A permanent redirect would survive the mount point moving."""
        app, installation = build()
        try:
            with TestClient(app) as test_client:
                response = test_client.get(PREFIX, follow_redirects=False)
            assert response.headers["cache-control"] == "no-store"
        finally:
            installation.shutdown()

    def test_a_panel_url_serves_the_interface(self, client):
        """The interface routes itself, so /queries has to reach the index."""
        assert get(client, "/queries").status_code == 200
