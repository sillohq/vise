"""
sillo_vise.watchers.cache — hit ratio, hot keys, and what each request read.

sillo's cache has a ``CacheStats`` on every backend, which gives the hit ratio
for free and nothing else the panel needs: a ratio is a single number with no
history, no keys and no attribution to the request that caused a miss.

So the backend's own methods are wrapped. That is a smaller intrusion than it
sounds — the wrapping is per *instance*, not per class, because the framework's
cache is one configured object rather than a class every caller instantiates.
Detaching puts the bound methods back.

The probe deliberately reads ``sillo.cache.config._DEFAULT`` rather than calling
``get_default_backend()``. That function creates a ``MemoryCache`` on first
access, so probing through it would bring a cache into existence and then
report that the application has one — a panel that appears *because* something
looked to see whether it should.

Hit and miss are read from the backend's own ``CacheStats``, as a delta across
the call, rather than inferred from what ``get`` returned. Inferring is wrong
in both directions: a miss returns a ``_MISSING`` sentinel rather than ``None``,
so every miss would be counted as a hit, and a key whose cached value genuinely
*is* ``None`` would be counted as a miss. The hit ratio is the number this panel
exists for, and it should come from the thing that already knows it.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ..recorder import Recorder
from .base import Availability, Watcher, available, unavailable

__all__ = ["CacheWatcher"]

#: Backend methods worth recording, and the result each reports on success.
_OPERATIONS: dict[str, str] = {
    "get": "",  # decided by what came back
    "set": "stored",
    "delete": "evicted",
    "touch": "touched",
    "clear": "cleared",
    "invalidate_tags": "evicted",
}


class CacheWatcher(Watcher):
    """Records every cache operation the application performs.

    Attributes:
        backend: The instance being watched.
        patched: Method names replaced on it, with the originals.
    """

    name = "cache"
    requires = "a cache backend configured with sillo.cache.configure_cache"

    __slots__ = ("backend", "patched")

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.backend: Any = None
        self.patched: list[tuple[str, Any]] = []

    def probe(self, app: Any) -> Availability:
        """Report whether a cache backend has been configured.

        Args:
            app: The application. Unused — sillo's cache is process-wide
                rather than attached to an application, and pretending
                otherwise would make the probe answer a different question
                from the one the panel asks.

        Returns:
            Whether there is a cache to watch.
        """
        backend = _configured_backend(app)
        if backend is None:
            return unavailable("no cache backend configured")

        return available(_describe(backend))

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Wrap the configured backend's methods.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)

        self.backend = _configured_backend(app)
        if self.backend is None:  # pragma: no cover - probe said yes
            return

        for name, result in _OPERATIONS.items():
            original = getattr(self.backend, name, None)
            if original is None or getattr(original, "__vise_wrapped__", False):
                continue

            setattr(self.backend, name, self._wrap(name, result, original))
            self.patched.append((name, original))

    def _wrap(self, operation: str, outcome: str, original: Callable) -> Callable:
        """Build the replacement for one backend method.

        Args:
            operation: The method's name.
            outcome: What a successful call means, or an empty string when the
                answer depends on what came back.
            original: What the method was.

        Returns:
            A coroutine function that times the call and records it.
        """
        backend = _describe(self.backend)

        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            """Perform the operation, then record it."""
            started = time.perf_counter()
            before = _counters(self.backend)

            try:
                value = await original(*args, **kwargs)
            except Exception:
                self._record(operation, args, started, "error", None)
                raise

            result = outcome or _hit_or_miss(before, _counters(self.backend))
            self._record(operation, args, started, result, value)
            return value

        wrapped.__name__ = operation
        wrapped.__qualname__ = f"vise:{backend}.{operation}"
        wrapped.__vise_wrapped__ = True  # type: ignore[attr-defined]
        return wrapped

    def _record(
        self,
        operation: str,
        args: tuple[Any, ...],
        started: float,
        result: str,
        value: Any,
    ) -> None:
        """Store one operation.

        Args:
            operation: The method called.
            args: Its positional arguments, the first of which is the key for
                every operation that has one.
            started: ``perf_counter`` reading from before the call.
            result: hit, miss, stored, evicted, touched, cleared or error.
            value: What came back, used only to notice a TTL.
        """
        if self.recorder is None:  # pragma: no cover - detached
            return

        self.recorder.cache(
            key=str(args[0]) if args else "*",
            operation=operation,
            result=result,
            ttl=_ttl(value),
            duration_ms=(time.perf_counter() - started) * 1000,
            backend=_describe(self.backend),
        )

    def detach(self) -> None:
        """Put every wrapped method back."""
        for name, original in reversed(self.patched):
            setattr(self.backend, name, original)
        self.patched.clear()
        self.backend = None
        super().detach()


def _configured_backend(app: Any) -> Any:
    """The cache backend the project configured, if it configured one.

    Checked in two places, because a project can hold a cache either way: on
    the application's state, which is how a project that wants more than one
    does it, or as the process-wide default that ``@cache()`` reaches for.

    Args:
        app: The application.

    Returns:
        The backend, or None when nothing is configured.
    """
    state = getattr(app, "state", None) or {}
    from_state = state.get("cache")
    if from_state is not None:
        return from_state

    try:
        from sillo.cache import config
    except ImportError:  # pragma: no cover - cache is first-party
        return None

    # Read the module global rather than calling get_default_backend(), which
    # would create a MemoryCache and then report that one exists.
    return getattr(config, "_DEFAULT", None)


def _describe(backend: Any) -> str:
    """Name a backend for the interface.

    Args:
        backend: The cache backend.

    Returns:
        A short lowercase name — ``redis``, ``memory`` — taken from the class.
    """
    if backend is None:
        return ""
    return type(backend).__name__.removesuffix("Cache").lower() or "cache"


def _counters(backend: Any) -> tuple[int, int]:
    """A backend's hit and miss counters, right now.

    Args:
        backend: The cache backend.

    Returns:
        Hits and misses, or zeroes when the backend keeps no statistics.
    """
    stats = getattr(backend, "stats", None)
    # A property on the base class, a method on some backends. Asking rather
    # than assuming costs one `callable` check and avoids reading the counters
    # off a bound method, which silently returns zeroes forever.
    if callable(stats):
        try:
            stats = stats()
        except Exception:  # noqa: BLE001 - a backend that will not report is not a crash
            return (0, 0)

    if stats is None:
        return (0, 0)

    return (int(getattr(stats, "hits", 0) or 0), int(getattr(stats, "misses", 0) or 0))


def _hit_or_miss(before: tuple[int, int], after: tuple[int, int]) -> str:
    """Whether a ``get`` found the key, according to the backend.

    Args:
        before: Hits and misses from before the call.
        after: Hits and misses from after it.

    Returns:
        ``hit``, ``miss``, or ``unknown`` when the backend counts neither —
        which is honest, and is what a third-party backend that does not
        implement ``CacheStats`` deserves.
    """
    if after[1] > before[1]:
        return "miss"
    if after[0] > before[0]:
        return "hit"
    return "unknown"


def _ttl(value: Any) -> int | None:
    """A time-to-live, when the returned value carries one.

    Args:
        value: Whatever the operation returned.

    Returns:
        The TTL in seconds, or None.
    """
    ttl = getattr(value, "ttl", None)
    return ttl if isinstance(ttl, int) else None
