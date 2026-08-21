"""
The live feed.

Framing is tested directly and the stream is driven with a real panel registry.
What matters is that a browser receives *rendered panels* rather than raw
events — that division is what keeps one implementation of "what is the p95".
"""

from __future__ import annotations

import json

import anyio
import pytest
from sillo import SilloApp

from sillo_vise.config import PanelConfig, ViseConfig
from sillo_vise.dashboard.stream import EventStream, sse
from sillo_vise.lifecycle import begin_shutdown, is_shutting_down
from sillo_vise.lifecycle import reset as reset_shutdown
from sillo_vise.panels import PanelRegistry
from sillo_vise.recorder import Recorder
from sillo_vise.watchers import WatcherRegistry


@pytest.fixture(autouse=True)
def _not_shutting_down():
    """Clear the process-wide stop flag around every test.

    It outlives a single server by design — that is the whole point of it — so
    a test that sets it would otherwise close the next test's stream on the
    first frame.
    """
    reset_shutdown()
    yield
    reset_shutdown()


@pytest.fixture
def stream():
    app = SilloApp(title="stream")

    async def home(request, response):
        return response.json({})

    app.get("/", handler=home, name="web.home")

    recorder = Recorder()
    watchers = WatcherRegistry(app, recorder, PanelConfig(refresh_ms=250))
    watchers.probe()
    panels = PanelRegistry(watchers, recorder, ViseConfig(), app)

    yield EventStream(panels, recorder, interval=0.05), recorder, panels
    watchers.detach_all()


class TestFraming:
    def test_an_event_carries_its_name(self):
        assert sse("panel", {"x": 1}).startswith(b"event: panel\n")

    def test_the_payload_is_json(self):
        frame = sse("panel", {"x": 1}).decode()
        body = frame.split("data: ", 1)[1].strip()
        assert json.loads(body) == {"x": 1}

    def test_a_frame_ends_with_a_blank_line(self):
        """Without the blank line the browser never dispatches the event."""
        assert sse("panel", {}).endswith(b"\n\n")

    def test_a_retry_hint_comes_first(self):
        assert sse("open", {}, retry=2000).startswith(b"retry: 2000\n")

    def test_something_json_cannot_hold_does_not_break_the_frame(self):
        frame = sse("panel", {"when": object()})
        assert frame.startswith(b"event: panel")


class TestStreaming:
    def test_the_first_frame_opens_the_connection(self, stream):
        feed, _, _ = stream

        async def read():
            async for frame in feed.frames("overview"):
                return frame
            return b""

        assert b"event: open" in anyio.run(read)

    def test_a_snapshot_is_a_rendered_panel(self, stream):
        """Not raw events — a browser receiving those would have to reimplement
        every panel to know what to do with them."""
        feed, recorder, _ = stream
        recorder.request(method="GET", path="/", status=200, duration_ms=5.0)

        async def read():
            frames = []
            async for frame in feed.frames("overview"):
                frames.append(frame)
                if len(frames) >= 2:
                    break
            return frames

        frames = anyio.run(read)
        snapshot = [f for f in frames if b"event: panel" in f][0].decode()
        payload = json.loads(snapshot.split("data: ", 1)[1])
        assert "tiles" in payload and "table" in payload

    def test_a_panel_that_stops_existing_says_so(self, stream):
        """A queue backend goes away and the Queues panel goes with it. Saying
        so lets the interface move rather than poll a dead id forever."""
        feed, _, panels = stream

        async def read():
            frames = []
            async for frame in feed.frames("routes"):
                frames.append(frame)
                # Remove the panel between the open frame and the first
                # snapshot.
                panels.panels = [p for p in panels.panels if p.id != "routes"]
                if len(frames) >= 2:
                    break
            return frames

        assert b"event: gone" in anyio.run(read)[-1]

    def test_the_subscription_is_closed_when_the_reader_leaves(self, stream):
        feed, recorder, _ = stream

        async def read():
            async for _ in feed.frames("overview"):
                break

        anyio.run(read)
        assert recorder.store.subscribers == 0

    def test_the_sidebar_is_sent_alongside(self, stream):
        """A panel can appear while somebody is looking at another one."""
        feed, _, _ = stream

        async def read():
            frames = []
            async for frame in feed.frames("overview"):
                frames.append(frame)
                if len(frames) >= 3:
                    break
            return frames

        assert any(b"event: sidebar" in frame for frame in anyio.run(read))


class TestStoppingTheServer:
    """Ctrl-C has to end the run even with a browser watching.

    Uvicorn's graceful shutdown waits for every open connection to finish, and
    this stream is deliberately parked for up to ten minutes. Before the stream
    learned to notice, a tab left on the dashboard was enough to make Ctrl-C
    look like it had been ignored — the server had begun stopping and was
    politely waiting for a connection that had no idea.
    """

    def test_a_shutdown_closes_the_connection(self, stream):
        feed, _, _ = stream
        begin_shutdown()

        async def read():
            frames = []
            async for frame in feed.frames("overview"):
                frames.append(frame)
                if len(frames) >= 4:  # more than it should ever send
                    break
            return frames

        frames = anyio.run(read)
        assert b"event: closing" in frames[-1]
        # Opened, then closed. It must not have gone on rendering panels.
        assert len(frames) == 2

    def test_a_running_server_keeps_the_connection(self, stream):
        """The flag is the only thing that ends it early."""
        feed, _, _ = stream

        async def read():
            frames = []
            async for frame in feed.frames("overview"):
                frames.append(frame)
                if len(frames) >= 3:
                    break
            return frames

        assert all(b"event: closing" not in frame for frame in anyio.run(read))


class TestShutdownFlag:
    def test_it_starts_clear(self):
        assert is_shutting_down() is False

    def test_beginning_a_shutdown_raises_it(self):
        begin_shutdown()
        assert is_shutting_down() is True

    def test_beginning_twice_is_harmless(self):
        begin_shutdown()
        begin_shutdown()
        assert is_shutting_down() is True

    def test_resetting_lets_another_server_run_in_this_process(self):
        """Which is what a test suite is, and what `serve` twice would be."""
        begin_shutdown()
        reset_shutdown()
        assert is_shutting_down() is False
