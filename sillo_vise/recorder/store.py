"""
sillo_vise.recorder.store — one bounded store, shared by every watcher.

Fourteen panels with fourteen collection paths would be fourteen storage
decisions and fourteen ways to leak. There is one store. Every watcher writes
into it and every panel reads out of it, which means the retention policy, the
memory ceiling and the correlation key are each decided once.

Memory is bounded by configuration, not by traffic: a ring per event kind,
capped at ``recorder.buffer``, plus the time series, capped at
``recorder.window_minutes``. A store under sustained load and a store on an
idle laptop cost the same.

Nothing here is redacted, because nothing reaches here un-redacted — the
recorder applies :class:`~sillo_vise.recorder.redact.Redactor` on the way in.
The store having no redaction of its own is the design, not an omission: two
places that could redact are two places that could forget.
"""

from __future__ import annotations

import threading
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator

from .events import Event, EventKind, RequestEvent
from .series import SeriesSet

__all__ = ["Store", "StoreSubscription"]

#: How many correlated events one request page will assemble. A request that
#: somehow produced ten thousand queries should not be able to make the
#: dashboard hang while rendering them.
MAX_CORRELATED = 500


class StoreSubscription:
    """A live feed of events, for the dashboard's SSE stream.

    Each connected browser holds one. It is bounded, and drops from the front
    when the reader is behind, because a slow consumer must cost its own feed
    and never the application's throughput.

    Attributes:
        kinds: Kinds this subscriber wants, or None for all of them.
        dropped: Events discarded because the subscriber was behind.
    """

    __slots__ = ("kinds", "dropped", "_queue", "_wake")

    def __init__(
        self, kinds: Iterable[EventKind] | None = None, maxlen: int = 512
    ) -> None:
        """Open a subscription.

        Args:
            kinds: Kinds to receive. None means everything.
            maxlen: Events buffered before the oldest are dropped.
        """
        self.kinds = frozenset(kinds) if kinds else None
        self.dropped = 0
        self._queue: deque[Event] = deque(maxlen=maxlen)
        self._wake = threading.Event()

    def offer(self, event: Event) -> None:
        """Hand an event to this subscriber.

        Args:
            event: The event just recorded.
        """
        if self.kinds is not None and event.kind not in self.kinds:
            return

        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1

        self._queue.append(event)
        self._wake.set()

    def drain(self) -> list[Event]:
        """Take everything buffered.

        Returns:
            The events, oldest first.
        """
        taken = list(self._queue)
        self._queue.clear()
        self._wake.clear()
        return taken

    def wait(self, timeout: float) -> bool:
        """Block until something arrives, or *timeout* passes.

        Args:
            timeout: Seconds to wait.

        Returns:
            True when there is something to drain.
        """
        return self._wake.wait(timeout)


class Store:
    """The single home for everything vise has observed.

    Attributes:
        buffer: Events retained per kind.
        series: The per-minute series behind charts and sparklines.
    """

    __slots__ = (
        "buffer",
        "series",
        "_rings",
        "_by_request",
        "_counters",
        "_lock",
        "_subscribers",
    )

    def __init__(self, buffer: int = 2000, window_minutes: int = 60) -> None:
        """Open a store.

        Args:
            buffer: Events retained per kind.
            window_minutes: Minutes the time series retains.
        """
        self.buffer = max(1, buffer)
        self.series = SeriesSet(window_minutes)

        self._rings: dict[EventKind, deque[Event]] = {
            kind: deque(maxlen=self.buffer) for kind in EventKind
        }
        # Correlation index. Bounded the same way the request ring is, and
        # trimmed alongside it, so a request falling out of the ring takes its
        # queries and cache reads with it rather than leaking them forever.
        self._by_request: dict[str, list[Event]] = {}
        self._counters: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._subscribers: list[StoreSubscription] = []

    # -- writing --------------------------------------------------------

    def add(self, event: Event) -> Event:
        """Record an event.

        Args:
            event: The event, already redacted.

        Returns:
            The same event, so a caller can hold onto it.
        """
        with self._lock:
            if event.request_id and event.kind is not EventKind.REQUEST:
                self._correlate(event)

            self._rings[event.kind].append(event)
            self._counters[event.kind.value] += 1
            subscribers = list(self._subscribers)

        # Outside the lock: a subscriber's deque is itself thread-safe for
        # append, and holding the store lock while fanning out to every open
        # browser would put the dashboard on the request path.
        for subscriber in subscribers:
            subscriber.offer(event)

        return event

    def _correlate(self, event: Event) -> None:
        """File *event* under the request that caused it.

        The request itself is stored last — its duration and status are not
        known until it finishes — so the queries and cache reads it caused
        arrive *before* it does. Indexing only against requests already in the
        ring would therefore correlate nothing at all, which is how this was
        first written and what the smoke test caught.

        The index is instead keyed independently and capped at the same size
        as the request ring, evicting in insertion order. That bounds it
        whether or not the matching request ever lands — a client that
        disconnects mid-request leaves correlated events behind, and without a
        cap they would accumulate for the life of the process.

        Called with the lock held.

        Args:
            event: A non-request event carrying a request id.
        """
        assert event.request_id is not None

        correlated = self._by_request.get(event.request_id)
        if correlated is None:
            if len(self._by_request) >= self.buffer:
                # Python dictionaries iterate in insertion order, so the first
                # key is the oldest request still indexed.
                self._by_request.pop(next(iter(self._by_request)), None)
            correlated = self._by_request[event.request_id] = []

        if len(correlated) < MAX_CORRELATED:
            correlated.append(event)

    # -- reading --------------------------------------------------------

    def recent(
        self,
        kind: EventKind,
        limit: int = 50,
        *,
        where: Callable[[Event], bool] | None = None,
    ) -> list[Event]:
        """The most recent events of one kind, newest first.

        Args:
            kind: Which ring to read.
            limit: How many to return.
            where: Optional predicate an event must satisfy.

        Returns:
            The events, newest first.
        """
        with self._lock:
            ring = list(self._rings[kind])

        found: list[Event] = []
        for event in reversed(ring):
            if where is None or where(event):
                found.append(event)
                if len(found) >= limit:
                    break
        return found

    def all(self, kind: EventKind) -> list[Event]:
        """Every retained event of one kind, oldest first.

        Args:
            kind: Which ring to read.

        Returns:
            The events.
        """
        with self._lock:
            return list(self._rings[kind])

    def find(self, kind: EventKind, event_id: str) -> Event | None:
        """One event by id.

        Args:
            kind: Which ring to search.
            event_id: The event's id.

        Returns:
            The event, or None when it has fallen out of the ring.
        """
        with self._lock:
            for event in reversed(self._rings[kind]):
                if event.id == event_id:
                    return event
        return None

    def request(self, request_id: str) -> RequestEvent | None:
        """One request by id.

        Args:
            request_id: The request's id.

        Returns:
            The request, or None when it has fallen out of the ring.
        """
        found = self.find(EventKind.REQUEST, request_id)
        return found if isinstance(found, RequestEvent) else None

    def correlated(self, request_id: str) -> list[Event]:
        """Everything one request caused.

        This is what makes the Requests panel more than a list: the queries,
        cache reads, outgoing calls, jobs, log lines and mail emitted while
        that request was in flight.

        Args:
            request_id: The request's id.

        Returns:
            The events, oldest first.
        """
        with self._lock:
            return list(self._by_request.get(request_id, ()))

    def count(self, kind: EventKind) -> int:
        """How many events of a kind have ever been recorded.

        Distinct from how many are retained: the ring forgets, this does not,
        which is what lets a tile say "84,102 processed" after the ring has
        turned over forty times.

        Args:
            kind: The kind to count.

        Returns:
            The lifetime count.
        """
        return self._counters[kind.value]

    def retained(self, kind: EventKind) -> int:
        """How many events of a kind are currently held.

        Args:
            kind: The kind to count.

        Returns:
            The ring's current length.
        """
        with self._lock:
            return len(self._rings[kind])

    def totals(self) -> dict[str, int]:
        """Lifetime counts for every kind.

        Returns:
            Kind names to counts.
        """
        return {kind.value: self._counters[kind.value] for kind in EventKind}

    # -- live feed ------------------------------------------------------

    def subscribe(self, kinds: Iterable[EventKind] | None = None) -> StoreSubscription:
        """Open a live feed.

        Args:
            kinds: Kinds to receive. None means everything.

        Returns:
            The subscription, which the caller must close.
        """
        subscription = StoreSubscription(kinds)
        with self._lock:
            self._subscribers.append(subscription)
        return subscription

    def unsubscribe(self, subscription: StoreSubscription) -> None:
        """Close a live feed.

        Args:
            subscription: The subscription to drop.
        """
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)

    @property
    def subscribers(self) -> int:
        """How many live feeds are open.

        Returns:
            The count, which the Config panel reports.
        """
        with self._lock:
            return len(self._subscribers)

    # -- housekeeping ---------------------------------------------------

    def clear(self) -> None:
        """Discard everything, including lifetime counts and series."""
        with self._lock:
            for ring in self._rings.values():
                ring.clear()
            self._by_request.clear()
            self._counters.clear()
        self.series.clear()

    def __iter__(self) -> Iterator[Event]:
        """Iterate every retained event, in no particular order.

        Returns:
            An iterator over the rings.
        """
        with self._lock:
            events = [event for ring in self._rings.values() for event in ring]
        return iter(events)

    def __repr__(self) -> str:
        """A short description for debugging.

        Returns:
            How much is retained, and the ceiling.
        """
        held = sum(len(ring) for ring in self._rings.values())
        return f"Store(retained={held}, buffer={self.buffer}, subscribers={self.subscribers})"
