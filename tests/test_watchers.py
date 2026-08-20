"""
Availability, proved by building applications with and without each subsystem.

"Live only" is the promise this file has to keep: a panel appears when its
watcher can observe something and not otherwise. The way to break that promise
is a probe that answers a slightly different question from the one the panel
asks — "is the import available" rather than "does this application use it" —
so every test here builds a real application and asks.
"""

from __future__ import annotations

import logging

import pytest
from sillo import SilloApp
from sillo.mail.client import setup_mail
from sillo.testclient import TestClient

from sillo_vise.config import PanelConfig
from sillo_vise.recorder import EventKind, Recorder
from sillo_vise.watchers import (
    CacheWatcher,
    LogWatcher,
    MailWatcher,
    QueryWatcher,
    QueueWatcher,
    RealtimeWatcher,
    ScheduleWatcher,
    WatcherRegistry,
    WorkerWatcher,
)


@pytest.fixture
def bare() -> SilloApp:
    """An application with nothing but a route."""
    application = SilloApp(title="bare")

    async def home(request, response):
        return response.json({})

    application.get("/", handler=home, name="web.home")
    return application


class TestBareApplication:
    """A project with no database, no queue, no cache and no mail should not be
    shown panels for any of them."""

    def test_queries_is_not_available(self, bare):
        assert not QueryWatcher().probe(bare)

    def test_cache_is_not_available(self, bare):
        assert not CacheWatcher().probe(bare)

    def test_queues_is_not_available(self, bare):
        assert not QueueWatcher().probe(bare)

    def test_workers_is_not_available(self, bare):
        assert not WorkerWatcher().probe(bare)

    def test_schedules_is_not_available(self, bare):
        assert not ScheduleWatcher().probe(bare)

    def test_mail_is_not_available(self, bare):
        assert not MailWatcher().probe(bare)

    def test_realtime_is_not_available(self, bare):
        """Every sillo application has an event emitter. Having one is not the
        same as using one, and only the second should produce a panel."""
        assert not RealtimeWatcher().probe(bare)

    def test_logs_is_always_available(self, bare):
        assert LogWatcher().probe(bare)


class TestReasons:
    """A missing panel has to say what is missing, not that a probe failed."""

    def test_queries_names_the_database(self, bare):
        assert "database" in QueryWatcher().probe(bare).detail

    def test_cache_names_the_cache(self, bare):
        assert "cache" in CacheWatcher().probe(bare).detail

    def test_schedules_names_the_call_that_would_fix_it(self, bare):
        assert "setup_work" in ScheduleWatcher().probe(bare).detail

    def test_mail_names_the_call_that_would_fix_it(self, bare):
        assert "setup_mail" in MailWatcher().probe(bare).detail


class TestMail:
    def test_a_configured_mailer_makes_the_panel_available(self, bare):
        setup_mail(bare)
        assert MailWatcher().probe(bare)


class TestCache:
    def test_probing_does_not_bring_a_cache_into_existence(self, bare):
        """get_default_backend() creates a MemoryCache on first access, so a
        probe that went through it would make the panel appear because
        something looked to see whether it should."""
        from sillo.cache import config

        config.reset_cache_config()
        CacheWatcher().probe(bare)
        assert getattr(config, "_DEFAULT", None) is None

    def test_a_configured_cache_makes_the_panel_available(self, bare):
        from sillo.cache import config
        from sillo.cache.backends import MemoryCache

        config.configure_cache(MemoryCache())
        try:
            assert CacheWatcher().probe(bare)
        finally:
            config.reset_cache_config()

    def test_operations_are_recorded(self, bare):
        from sillo.cache import config
        from sillo.cache.backends import MemoryCache

        import anyio

        config.configure_cache(MemoryCache())
        recorder = Recorder()
        watcher = CacheWatcher()
        try:
            watcher.attach(bare, recorder)

            async def exercise():
                cache = config.get_default_backend()
                await cache.set("k", "v")
                await cache.get("k")
                await cache.get("missing")

            anyio.run(exercise)
        finally:
            watcher.detach()
            config.reset_cache_config()

        results = [event.result for event in recorder.store.all(EventKind.CACHE)]
        assert results == ["stored", "hit", "miss"]

    def test_detaching_restores_the_backend(self, bare):
        from sillo.cache import config
        from sillo.cache.backends import MemoryCache

        cache = MemoryCache()
        config.configure_cache(cache)
        original = cache.get
        watcher = CacheWatcher()
        try:
            watcher.attach(bare, Recorder())
            watcher.detach()
            assert cache.get == original
        finally:
            config.reset_cache_config()


class TestLogs:
    def test_a_log_line_is_recorded(self, bare):
        recorder = Recorder()
        watcher = LogWatcher()
        watcher.attach(bare, recorder)
        try:
            logging.getLogger("app.test").warning("something happened")
        finally:
            watcher.detach()

        assert recorder.store.recent(EventKind.LOG)[0].message == "something happened"

    def test_vise_does_not_record_itself(self, bare):
        """A dashboard that records its own log lines fills its own panel, and
        then records that it did."""
        recorder = Recorder()
        watcher = LogWatcher()
        watcher.attach(bare, recorder)
        try:
            logging.getLogger("sillo_vise.dashboard").warning("internal")
        finally:
            watcher.detach()

        assert recorder.store.retained(EventKind.LOG) == 0

    def test_detaching_removes_the_handler(self, bare):
        watcher = LogWatcher()
        watcher.attach(bare, Recorder())
        watcher.detach()
        assert watcher.handler not in logging.getLogger().handlers

    def test_structured_extras_are_kept(self, bare):
        recorder = Recorder()
        watcher = LogWatcher()
        logger = logging.getLogger("app.test")
        logger.setLevel(logging.INFO)
        watcher.attach(bare, recorder)
        try:
            logger.info("m", extra={"workspace": 3})
        finally:
            watcher.detach()
            logger.setLevel(logging.NOTSET)

        assert recorder.store.recent(EventKind.LOG)[0].fields["workspace"] == 3

    def test_the_panel_only_sees_what_the_logger_passes(self):
        """A level is applied by the logger before any handler is consulted,
        so the panel shows what the application decided to log — not
        everything it might have. `install_logging` is what sets that level
        from `[logs] level`, and a project that configured its own keeps it."""
        recorder = Recorder()
        watcher = LogWatcher()
        logger = logging.getLogger("app.quiet")
        logger.setLevel(logging.ERROR)
        watcher.attach(None, recorder)
        try:
            logger.info("not important enough")
        finally:
            watcher.detach()
            logger.setLevel(logging.NOTSET)

        assert recorder.store.retained(EventKind.LOG) == 0


class TestRegistry:
    def test_only_available_watchers_attach(self, bare):
        registry = WatcherRegistry(bare, Recorder())
        registry.probe()
        try:
            assert "queries" not in registry.live()
            assert "logs" in registry.live()
        finally:
            registry.detach_all()

    def test_a_disabled_panel_never_attaches(self, bare):
        registry = WatcherRegistry(bare, Recorder(), PanelConfig(disable=("logs",)))
        registry.probe()
        try:
            assert "logs" not in registry.live()
        finally:
            registry.detach_all()

    def test_a_disabled_panel_is_not_even_probed(self, bare):
        registry = WatcherRegistry(bare, Recorder(), PanelConfig(disable=("logs",)))
        registry.probe()
        assert registry.state_of("logs").availability.detail == "not probed"

    def test_missing_panels_carry_a_reason(self, bare):
        registry = WatcherRegistry(bare, Recorder())
        registry.probe()
        try:
            assert all(state.availability.detail for state in registry.missing())
        finally:
            registry.detach_all()

    def test_a_broken_watcher_costs_only_its_own_panel(self, bare):
        class Broken(LogWatcher):
            name = "broken"

            def probe(self, app):
                raise RuntimeError("nope")

        registry = WatcherRegistry(bare, Recorder(), watchers=[Broken(), LogWatcher()])
        registry.probe()
        try:
            assert registry.live() == ["logs"]
            assert "nope" in registry.state_of("broken").availability.detail
        finally:
            registry.detach_all()

    def test_a_watcher_that_cannot_attach_costs_only_its_own_panel(self, bare):
        class Unattachable(LogWatcher):
            name = "unattachable"

            def attach(self, app, recorder):
                raise RuntimeError("no")

        registry = WatcherRegistry(bare, Recorder(), watchers=[Unattachable(), LogWatcher()])
        registry.probe()
        try:
            assert registry.live() == ["logs"]
        finally:
            registry.detach_all()

    def test_probing_again_is_cheap_until_the_interval_passes(self, bare):
        registry = WatcherRegistry(bare, Recorder(), PanelConfig(probe_seconds=3600))
        registry.probe()
        first = registry.state_of("logs").probed_at
        try:
            registry.probe_if_due()
            assert registry.state_of("logs").probed_at == first
        finally:
            registry.detach_all()

    def test_the_report_says_what_each_panel_needs(self, bare):
        registry = WatcherRegistry(bare, Recorder())
        registry.probe()
        try:
            assert all(state.to_dict()["requires"] for state in registry)
        finally:
            registry.detach_all()
