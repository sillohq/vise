"""
The request watcher, against a real application.

These drive the framework's own TestClient rather than a stub ASGI callable.
The bugs worth catching here are the ones where sillo does not do what vise
assumed — and a stub would agree with every assumption.
"""

from __future__ import annotations

import time

from sillo.testclient import TestClient

from sillo_vise.recorder import EventKind
from sillo_vise.watchers.requests import RequestWatcher


def requests_of(recorder):
    return recorder.store.recent(EventKind.REQUEST)


class TestRecording:
    def test_a_request_is_recorded(self, watched, recorder):
        with TestClient(watched) as client:
            client.get("/")
        assert len(requests_of(recorder)) == 1

    def test_the_method_and_path_are_kept(self, watched, recorder):
        with TestClient(watched) as client:
            client.get("/documents/8f21")
        event = requests_of(recorder)[0]
        assert (event.method, event.path) == ("GET", "/documents/8f21")

    def test_the_status_comes_from_the_response(self, watched, recorder):
        with TestClient(watched) as client:
            client.post("/documents")
        assert requests_of(recorder)[0].status == 201

    def test_the_duration_is_measured(self, watched, recorder):
        with TestClient(watched) as client:
            client.get("/")
        assert requests_of(recorder)[0].duration_ms > 0

    def test_response_bytes_are_counted(self, watched, recorder):
        """Counted from the body messages, not from content-length, so a
        streamed response is measured too."""
        with TestClient(watched) as client:
            response = client.get("/")
        assert requests_of(recorder)[0].response_bytes == len(response.content)

    def test_the_route_name_is_resolved(self, watched, recorder):
        with TestClient(watched) as client:
            client.get("/documents/8f21")
        assert requests_of(recorder)[0].route == "api.documents.show"

    def test_an_unmatched_path_has_no_route_name(self, watched, recorder):
        with TestClient(watched) as client:
            client.get("/nothing-here")
        assert requests_of(recorder)[0].route == ""

    def test_request_headers_are_kept_in_order(self, watched, recorder):
        with TestClient(watched) as client:
            client.get("/", headers={"x-one": "1", "x-two": "2"})
        names = [name for name, _ in requests_of(recorder)[0].headers]
        assert names.index("x-one") < names.index("x-two")

    def test_response_headers_are_kept(self, watched, recorder):
        with TestClient(watched) as client:
            client.get("/")
        names = {name for name, _ in requests_of(recorder)[0].response_headers}
        assert "content-type" in names


class TestSlowRequests:
    def test_a_slow_request_is_marked(self, app, recorder):
        async def slow(request, response):
            time.sleep(0.06)
            return response.json({})

        app.get("/slow", handler=slow, name="api.slow")
        RequestWatcher().attach(app, recorder)

        with TestClient(app) as client:
            client.get("/slow")
        assert requests_of(recorder)[0].slow is True

    def test_a_fast_request_is_not(self, watched, recorder):
        with TestClient(watched) as client:
            client.get("/")
        assert requests_of(recorder)[0].slow is False


class TestFailures:
    def test_a_raising_handler_is_still_recorded(self, watched, recorder):
        """The one request that most needs to be on the dashboard is the one
        that would otherwise be missing from it."""
        with TestClient(watched, raise_server_exceptions=False) as client:
            client.get("/boom")
        assert [event.path for event in requests_of(recorder)] == ["/boom"]

    def test_the_exception_is_recorded_too(self, watched, recorder):
        with TestClient(watched, raise_server_exceptions=False) as client:
            client.get("/boom")
        assert recorder.store.recent(EventKind.EXCEPTION)[0].type == "ValueError"

    def test_the_exception_is_correlated_with_its_request(self, watched, recorder):
        with TestClient(watched, raise_server_exceptions=False) as client:
            client.get("/boom")
        request = requests_of(recorder)[0]
        kinds = {event.kind for event in recorder.store.correlated(request.id)}
        assert EventKind.EXCEPTION in kinds


class TestSkipping:
    def test_a_skipped_prefix_is_not_recorded(self, app, recorder):
        RequestWatcher(skip=("/documents",)).attach(app, recorder)
        with TestClient(app) as client:
            client.get("/documents/1")
        assert requests_of(recorder) == []

    def test_everything_else_still_is(self, app, recorder):
        RequestWatcher(skip=("/documents",)).attach(app, recorder)
        with TestClient(app) as client:
            client.get("/")
        assert len(requests_of(recorder)) == 1


class TestCorrelation:
    def test_events_emitted_during_a_request_carry_its_id(self, app, recorder):
        async def busy(request, response):
            recorder.query("SELECT 1", duration_ms=1.0)
            recorder.cache("k", result="hit")
            return response.json({})

        app.get("/busy", handler=busy, name="api.busy")
        RequestWatcher().attach(app, recorder)

        with TestClient(app) as client:
            client.get("/busy")

        request = requests_of(recorder)[0]
        assert len(recorder.store.correlated(request.id)) == 2

    def test_two_requests_do_not_share_a_context(self, app, recorder):
        async def busy(request, response, id):
            recorder.query(f"SELECT {id}", duration_ms=1.0)
            return response.json({})

        app.get("/busy/{id}", handler=busy, name="api.busy")
        RequestWatcher().attach(app, recorder)

        with TestClient(app) as client:
            client.get("/busy/1")
            client.get("/busy/2")

        for event in requests_of(recorder):
            assert len(recorder.store.correlated(event.id)) == 1
