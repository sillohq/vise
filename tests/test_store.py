"""The store: bounds, correlation and the live feed."""

from __future__ import annotations

from sillo_vise.recorder import EventKind, LogEvent, QueryEvent, RequestEvent, Store


def request(**fields) -> RequestEvent:
    return RequestEvent(**fields)


class TestBounds:
    def test_the_ring_never_exceeds_the_buffer(self):
        store = Store(buffer=10)
        for index in range(100):
            store.add(request(path=f"/{index}"))
        assert store.retained(EventKind.REQUEST) == 10

    def test_the_newest_survive(self):
        store = Store(buffer=3)
        for index in range(10):
            store.add(request(path=f"/{index}"))
        assert [event.path for event in store.recent(EventKind.REQUEST)] == [
            "/9",
            "/8",
            "/7",
        ]

    def test_kinds_are_bounded_separately(self):
        store = Store(buffer=2)
        for index in range(5):
            store.add(request(path=f"/{index}"))
            store.add(QueryEvent(sql=f"select {index}"))
        assert store.retained(EventKind.REQUEST) == 2
        assert store.retained(EventKind.QUERY) == 2

    def test_the_correlation_index_is_bounded_too(self):
        store = Store(buffer=5)
        for index in range(50):
            store.add(QueryEvent(sql="select 1", request_id=f"req{index}"))
        assert len(store._by_request) <= 5

    def test_lifetime_counts_outlive_the_ring(self):
        store = Store(buffer=2)
        for index in range(20):
            store.add(request(path=f"/{index}"))
        assert store.count(EventKind.REQUEST) == 20
        assert store.retained(EventKind.REQUEST) == 2


class TestCorrelation:
    def test_events_emitted_before_the_request_still_correlate(self):
        store = Store()
        store.add(QueryEvent(sql="select 1", request_id="r1"))
        store.add(LogEvent(message="hello", request_id="r1"))
        store.add(request(id="r1"))
        assert len(store.correlated("r1")) == 2

    def test_the_request_itself_is_not_in_its_own_correlation(self):
        store = Store()
        store.add(request(id="r1"))
        assert store.correlated("r1") == []

    def test_events_for_other_requests_are_kept_apart(self):
        store = Store()
        store.add(QueryEvent(sql="a", request_id="r1"))
        store.add(QueryEvent(sql="b", request_id="r2"))
        assert [event.sql for event in store.correlated("r1")] == ["a"]

    def test_an_unknown_request_correlates_to_nothing(self):
        assert Store().correlated("nope") == []

    def test_one_request_cannot_flood_the_index(self):
        store = Store()
        for index in range(2000):
            store.add(QueryEvent(sql=f"select {index}", request_id="r1"))
        assert len(store.correlated("r1")) <= 500


class TestReading:
    def test_recent_is_newest_first(self):
        store = Store()
        for index in range(5):
            store.add(request(path=f"/{index}"))
        assert store.recent(EventKind.REQUEST)[0].path == "/4"

    def test_recent_honours_the_limit(self):
        store = Store()
        for index in range(20):
            store.add(request(path=f"/{index}"))
        assert len(store.recent(EventKind.REQUEST, limit=3)) == 3

    def test_recent_honours_a_predicate(self):
        store = Store()
        store.add(request(status=200))
        store.add(request(status=503))
        found = store.recent(EventKind.REQUEST, where=lambda event: event.status >= 500)
        assert [event.status for event in found] == [503]

    def test_find_locates_by_id(self):
        store = Store()
        event = store.add(request(path="/found"))
        assert store.find(EventKind.REQUEST, event.id) is event

    def test_find_returns_none_once_evicted(self):
        store = Store(buffer=1)
        event = store.add(request(path="/gone"))
        store.add(request(path="/here"))
        assert store.find(EventKind.REQUEST, event.id) is None

    def test_request_only_returns_requests(self):
        store = Store()
        query = store.add(QueryEvent(sql="select 1"))
        assert store.request(query.id) is None


class TestLiveFeed:
    def test_a_subscriber_receives_events(self):
        store = Store()
        feed = store.subscribe()
        store.add(request(path="/x"))
        assert [event.path for event in feed.drain()] == ["/x"]

    def test_a_subscriber_can_filter_by_kind(self):
        store = Store()
        feed = store.subscribe([EventKind.QUERY])
        store.add(request(path="/x"))
        store.add(QueryEvent(sql="select 1"))
        assert [event.kind for event in feed.drain()] == [EventKind.QUERY]

    def test_draining_empties_the_buffer(self):
        store = Store()
        feed = store.subscribe()
        store.add(request())
        feed.drain()
        assert feed.drain() == []

    def test_a_slow_consumer_drops_its_own_events(self):
        store = Store()
        feed = store.subscribe()
        for index in range(2000):
            store.add(request(path=f"/{index}"))
        assert feed.dropped > 0
        assert len(feed.drain()) <= 512

    def test_a_slow_consumer_does_not_cost_the_store(self):
        store = Store()
        store.subscribe()
        for index in range(2000):
            store.add(request(path=f"/{index}"))
        assert store.count(EventKind.REQUEST) == 2000

    def test_unsubscribing_stops_delivery(self):
        store = Store()
        feed = store.subscribe()
        store.unsubscribe(feed)
        store.add(request())
        assert feed.drain() == []

    def test_subscriber_count_is_reported(self):
        store = Store()
        store.subscribe()
        store.subscribe()
        assert store.subscribers == 2


class TestHousekeeping:
    def test_clear_empties_everything(self):
        store = Store()
        store.add(request())
        store.add(QueryEvent(sql="select 1", request_id="r1"))
        store.clear()
        assert store.totals() == {kind.value: 0 for kind in EventKind}
        assert store.correlated("r1") == []

    def test_iteration_covers_every_ring(self):
        store = Store()
        store.add(request())
        store.add(QueryEvent(sql="select 1"))
        assert len(list(store)) == 2
