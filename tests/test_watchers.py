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
        import anyio
        from sillo.cache import config
        from sillo.cache.backends import MemoryCache

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

        registry = WatcherRegistry(
            bare, Recorder(), watchers=[Unattachable(), LogWatcher()]
        )
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


class TestScheduleWatcher:
    """The scheduler's accessor is `list()`. An earlier version read
    `manager.jobs`, found nothing, and reported "0 scheduled" — a panel that
    appeared and was wrong, which nothing complained about."""

    @staticmethod
    def manager():
        from sillo.work.scheduler.manager import SchedulerManager

        scheduler = SchedulerManager()

        @scheduler.every(30, name="analytics.rollup")
        async def rollup():
            pass

        @scheduler.cron("0 3 * * *", name="workspaces.prune")
        async def prune():
            pass

        return scheduler

    def app_with(self, scheduler):
        application = SilloApp(title="scheduled")
        application.state["scheduler"] = scheduler
        return application

    def test_the_jobs_are_found(self):
        watcher = ScheduleWatcher()
        assert "2 scheduled" in watcher.probe(self.app_with(self.manager())).detail

    def test_each_job_is_listed(self):
        watcher = ScheduleWatcher()
        app = self.app_with(self.manager())
        watcher.attach(app, Recorder())
        try:
            assert {job["job"] for job in watcher.jobs()} == {
                "analytics.rollup",
                "workspaces.prune",
            }
        finally:
            watcher.detach()

    def test_a_cron_expression_is_shown_as_written(self):
        watcher = ScheduleWatcher()
        app = self.app_with(self.manager())
        watcher.attach(app, Recorder())
        try:
            jobs = {job["job"]: job["expression"] for job in watcher.jobs()}
            assert jobs["workspaces.prune"] == "0 3 * * *"
        finally:
            watcher.detach()

    def test_an_interval_reads_as_an_interval(self):
        """Rather than as the repr of a trigger object."""
        watcher = ScheduleWatcher()
        app = self.app_with(self.manager())
        watcher.attach(app, Recorder())
        try:
            jobs = {job["job"]: job["expression"] for job in watcher.jobs()}
            assert jobs["analytics.rollup"] == "every 30s"
        finally:
            watcher.detach()

    def test_the_next_fire_is_read(self):
        watcher = ScheduleWatcher()
        app = self.app_with(self.manager())
        watcher.attach(app, Recorder())
        try:
            assert all(job["next_fire"] for job in watcher.jobs())
        finally:
            watcher.detach()


class TestWorkerWatcher:
    """WorkerPool keeps its workers on `_workers` and exposes no accessor. An
    earlier version looked for `pool.workers` and reported a pool of three as
    zero workers with an empty table."""

    @staticmethod
    def pool():
        from sillo.work.queue.connection import ConnectionManager, SyncConnection
        from sillo.work.queue.failed import MemoryFailedRepository
        from sillo.work.queue.payloads import PayloadSerializer
        from sillo.work.queue.workers import QueueWorker, WorkerOptions, WorkerPool

        connections = ConnectionManager()
        connections.add("default", SyncConnection())

        built = WorkerPool()
        for queues in (["default"], ["mail"]):
            built.add(
                QueueWorker(
                    connections,
                    PayloadSerializer(),
                    MemoryFailedRepository(),
                    options=WorkerOptions(concurrency=2, queues=queues),
                )
            )
        return built

    def app_with(self, pool):
        application = SilloApp(title="pooled")
        application.state["worker_pool"] = pool
        return application

    def test_the_pool_is_found(self):
        assert WorkerWatcher().probe(self.app_with(self.pool()))

    def test_the_workers_are_counted(self):
        watcher = WorkerWatcher()
        watcher.attach(self.app_with(self.pool()), Recorder())
        try:
            assert watcher.stats()["workers"] == 2
        finally:
            watcher.detach()

    def test_each_worker_is_a_row(self):
        watcher = WorkerWatcher()
        watcher.attach(self.app_with(self.pool()), Recorder())
        try:
            assert len(watcher.processes()) == 2
        finally:
            watcher.detach()

    def test_a_row_names_the_queues_it_listens_on(self):
        watcher = WorkerWatcher()
        watcher.attach(self.app_with(self.pool()), Recorder())
        try:
            assert {row["queues"] for row in watcher.processes()} == {"default", "mail"}
        finally:
            watcher.detach()

    def test_a_stopped_worker_is_not_reported_as_closed(self):
        """ "closed" is a circuit-breaker state. A worker that is not running is
        stalled, and saying so is more use than a hardcoded reassurance."""
        watcher = WorkerWatcher()
        watcher.attach(self.app_with(self.pool()), Recorder())
        try:
            assert {row["circuit"] for row in watcher.processes()} == {"stalled"}
        finally:
            watcher.detach()


class TestRealtimeWatcher:
    """`emit()` runs listeners through `Event.trigger`. The emitter's
    `_dispatch` only runs on the receive side of a networked transport, so
    wrapping it recorded nothing on the memory backend every project starts
    with."""

    @staticmethod
    def emitter():
        from sillo.events.emitter import EventEmitter

        return EventEmitter()

    def app_with(self, emitter):
        application = SilloApp(title="eventful")
        application.state["emitter"] = emitter
        return application

    def test_a_queue_dispatcher_is_not_mistaken_for_an_emitter(self):
        """setup_work puts an EventDispatcher at state["events"], which has
        neither of the members this watcher uses."""
        from sillo.work.queue.events import EventDispatcher

        application = SilloApp(title="worky")
        application.state["events"] = EventDispatcher()

        assert not RealtimeWatcher().probe(application)

    def test_an_emitter_with_events_is_available(self):
        emitter = self.emitter()

        @emitter.on("document.published")
        async def listener(document_id):
            pass

        assert RealtimeWatcher().probe(self.app_with(emitter))

    def test_an_emitted_event_is_recorded(self):
        import anyio

        emitter = self.emitter()

        @emitter.on("document.published")
        async def listener(document_id):
            pass

        recorder = Recorder()
        watcher = RealtimeWatcher()
        app = self.app_with(emitter)
        watcher.attach(app, recorder)

        try:

            async def exercise():
                await emitter.start()
                emitter.emit("document.published", 7)
                await anyio.sleep(0.15)
                await emitter.stop()

            anyio.run(exercise)
        finally:
            watcher.detach()

        assert recorder.store.count(EventKind.SIGNAL) == 1

    def test_the_listener_count_is_read(self):
        import anyio

        emitter = self.emitter()

        for _ in range(2):

            @emitter.on("document.published")
            async def listener(document_id):
                pass

        recorder = Recorder()
        watcher = RealtimeWatcher()
        watcher.attach(self.app_with(emitter), recorder)

        try:

            async def exercise():
                await emitter.start()
                emitter.emit("document.published", 7)
                await anyio.sleep(0.15)
                await emitter.stop()

            anyio.run(exercise)
        finally:
            watcher.detach()

        assert recorder.store.recent(EventKind.SIGNAL)[0].listeners == 2

    def test_detaching_restores_the_trigger(self):
        from sillo.events.core import Event as SilloEvent

        watcher = RealtimeWatcher()
        watcher.attach(self.app_with(self.emitter()), Recorder())
        watcher.detach()

        assert not getattr(SilloEvent.trigger, "__vise_wrapped__", False)
