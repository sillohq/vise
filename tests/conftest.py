"""
Shared fixtures.

Everything here builds a *real* ``SilloApp`` and drives it through the
framework's own ``TestClient``. Vise is a layer over a framework it does not
own, and the mistakes that matter are the ones where the framework does not
behave the way the layer assumed — a route table that is empty until startup
finishes, a scope key that is not there, middleware ordering. A mock ASGI
application would agree with every assumption vise makes and prove none of
them.
"""

from __future__ import annotations

import pytest
from sillo import SilloApp

from sillo_vise.config import LogConfig, RecorderConfig, ViseConfig
from sillo_vise.recorder import Recorder
from sillo_vise.watchers.requests import RequestWatcher


@pytest.fixture
def app() -> SilloApp:
    """A small application with the route shapes the panels have to cope with."""
    application = SilloApp(title="Test application")

    async def home(request, response):
        return response.json({"ok": True})

    async def show(request, response, id):
        return response.json({"id": id})

    async def create(request, response):
        return response.json({"created": True}, status_code=201)

    async def boom(request, response):
        raise ValueError("deliberate")

    application.get("/", handler=home, name="web.home")
    application.get("/documents/{id}", handler=show, name="api.documents.show")
    application.post("/documents", handler=create, name="api.documents.store")
    application.get("/boom", handler=boom, name="api.boom")

    return application


@pytest.fixture
def recorder() -> Recorder:
    """A recorder with a small buffer, so eviction is reachable in a test."""
    return Recorder(RecorderConfig(buffer=100, slow_request_ms=50))


@pytest.fixture
def watched(app: SilloApp, recorder: Recorder) -> SilloApp:
    """The application, with the request watcher installed."""
    RequestWatcher().attach(app, recorder)
    return app


@pytest.fixture
def config() -> ViseConfig:
    """A configuration with the banner and access log off, so tests stay quiet."""
    return ViseConfig(logs=LogConfig(access=False, banner=False))
