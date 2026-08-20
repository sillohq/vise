"""
sillo_vise.recorder.series — the time series behind every chart and sparkline.

The framework already has the fields the panels show. What it does not have is
anything remembering what those fields were a minute ago, and a dashboard whose
charts are the point cannot be built out of instantaneous readings.

So: one :class:`Series` per measured thing, bucketed by minute, over a fixed
window. A bucket holds a count, a sum and a reservoir of samples rather than
every observation, which keeps memory flat under load — the expensive way to
compute a p95 is to keep every value, and the expensive way is the one that
turns a busy afternoon into an out-of-memory kill.

Percentiles come from the reservoir and are therefore estimates. That is said
plainly here and in the interface, because a p95 presented as exact when it is
sampled is worse than one presented as sampled.
"""

from __future__ import annotations

import bisect
import time
from collections import deque
from collections.abc import Iterable, Iterator

__all__ = ["Bucket", "Series", "SeriesSet"]

#: Observations kept per minute for percentile estimation. 256 puts the p95
#: within a bucket or so of the true value at any traffic level worth charting,
#: and costs two kilobytes per minute per series.
RESERVOIR = 256


class Bucket:
    """One minute of one series.

    Attributes:
        minute: Unix timestamp floored to the minute.
        count: Observations seen.
        total: Their sum, which gives the mean without keeping them.
        maximum: The largest observation.
        errors: Observations flagged as failures by the caller.
    """

    __slots__ = ("minute", "count", "total", "maximum", "errors", "_samples")

    def __init__(self, minute: int) -> None:
        """Open a bucket.

        Args:
            minute: Unix timestamp floored to the minute.
        """
        self.minute = minute
        self.count = 0
        self.total = 0.0
        self.maximum = 0.0
        self.errors = 0
        self._samples: list[float] = []

    def add(self, value: float, *, error: bool = False) -> None:
        """Record one observation.

        Args:
            value: What was measured. Zero is fine — a counter series
                observes zeroes to say the minute happened.
            error: Whether this observation was a failure.
        """
        self.count += 1
        self.total += value
        if value > self.maximum:
            self.maximum = value
        if error:
            self.errors += 1

        # A sorted insert keeps `percentile` free, and the list is capped, so
        # the insert is into at most 256 elements.
        if len(self._samples) < RESERVOIR:
            bisect.insort(self._samples, value)
        elif value > self._samples[0]:
            # Full: drop the smallest. Percentiles asked for here are always
            # upper ones — p50 and above — so the tail is what must survive.
            self._samples.pop(0)
            bisect.insort(self._samples, value)

    @property
    def mean(self) -> float:
        """Average observation in this minute.

        Returns:
            The mean, or zero when nothing was observed.
        """
        return self.total / self.count if self.count else 0.0

    def percentile(self, fraction: float) -> float:
        """Estimate a percentile from this minute's reservoir.

        Args:
            fraction: Between 0 and 1. 0.95 is the p95.

        Returns:
            The estimate, or zero when nothing was observed.
        """
        if not self._samples:
            return 0.0
        index = min(
            len(self._samples) - 1,
            max(0, int(round(fraction * (len(self._samples) - 1)))),
        )
        return self._samples[index]

    @property
    def samples(self) -> list[float]:
        """The retained observations, ascending.

        Returns:
            A copy, so a caller cannot disturb the reservoir's ordering.
        """
        return list(self._samples)

    def __repr__(self) -> str:
        """A short description for debugging.

        Returns:
            The minute, the count and the mean.
        """
        return f"Bucket(minute={self.minute}, count={self.count}, mean={self.mean:.1f})"


class Series:
    """A measured quantity, bucketed by minute over a fixed window.

    Attributes:
        name: What this measures.
        window: Minutes retained.
    """

    __slots__ = ("name", "window", "_buckets")

    def __init__(self, name: str, window: int = 60) -> None:
        """Open a series.

        Args:
            name: What this measures.
            window: Minutes to retain.
        """
        self.name = name
        self.window = max(1, window)
        self._buckets: deque[Bucket] = deque(maxlen=self.window)

    @staticmethod
    def _minute(at: float | None = None) -> int:
        """Floor a timestamp to its minute.

        Args:
            at: The timestamp. Defaults to now.

        Returns:
            The minute this falls in.
        """
        return int((time.time() if at is None else at) // 60 * 60)

    def _bucket(self, at: float | None = None) -> Bucket:
        """The bucket for *at*, opening it and any gap before it.

        Gaps are opened as empty buckets rather than skipped, so a chart shows
        a quiet minute as a quiet minute instead of closing the gap and
        implying traffic that was never there.

        Args:
            at: The timestamp. Defaults to now.

        Returns:
            The bucket to record into.
        """
        minute = self._minute(at)

        if self._buckets and self._buckets[-1].minute == minute:
            return self._buckets[-1]

        if self._buckets and minute < self._buckets[-1].minute:
            # An observation older than the newest bucket. Find it, or drop it
            # if it has already fallen out of the window.
            for bucket in reversed(self._buckets):
                if bucket.minute == minute:
                    return bucket
            return Bucket(minute)

        last = self._buckets[-1].minute if self._buckets else minute
        for gap in range(last + 60, minute, 60):
            self._buckets.append(Bucket(gap))

        bucket = Bucket(minute)
        self._buckets.append(bucket)
        return bucket

    def add(
        self, value: float = 1.0, *, at: float | None = None, error: bool = False
    ) -> None:
        """Record one observation.

        Args:
            value: What was measured.
            at: When. Defaults to now.
            error: Whether this observation was a failure.
        """
        self._bucket(at).add(value, error=error)

    def buckets(self, minutes: int | None = None, offset: int = 0) -> list[Bucket]:
        """A window of minutes, oldest first, ending *offset* minutes ago.

        Anchored to now rather than to the last observation. A series that
        stopped receiving traffic five minutes ago has to render as five empty
        minutes on the right of the chart — anchoring to the newest bucket
        instead would draw the old traffic at the live edge and make a stalled
        queue look busy.

        Minutes with no bucket are filled with empty ones, at either end, so
        the length is always what was asked for. A chart with a fixed bar count
        should not narrow because the process started two minutes ago.

        An *offset* shifts the window back in time, which is what lets a tile
        compare the last five minutes against the five before them. Comparing
        against a wider window containing both would be dominated by whichever
        half is worse, so a latency that had just tripled would read as no
        change at all.

        Args:
            minutes: How many minutes to return. None means the whole window.
            offset: How many minutes back the window ends.

        Returns:
            The buckets, oldest first.
        """
        wanted = self.window if minutes is None else max(1, min(minutes, self.window))
        held = {bucket.minute: bucket for bucket in self._buckets}
        latest = self._minute() - 60 * max(0, offset)

        return [
            held.get(minute) or Bucket(minute)
            for minute in range(latest - 60 * (wanted - 1), latest + 60, 60)
        ]

    def counts(self, minutes: int | None = None) -> list[int]:
        """Observations per minute, oldest first.

        Args:
            minutes: How many minutes. None means the whole window.

        Returns:
            One count per minute.
        """
        return [bucket.count for bucket in self.buckets(minutes)]

    def means(self, minutes: int | None = None) -> list[float]:
        """Mean observation per minute, oldest first.

        Args:
            minutes: How many minutes. None means the whole window.

        Returns:
            One mean per minute.
        """
        return [bucket.mean for bucket in self.buckets(minutes)]

    def rate(self, minutes: int = 5) -> float:
        """Observations per minute over the last *minutes*.

        Divided by *elapsed* time rather than by whole buckets. The obvious
        implementations are both wrong for a development server. Dividing by
        the bucket count makes every reading dip at the top of a minute and
        recover by the end of it, because the newest bucket is a fraction of a
        minute being counted as a whole one. Dropping the current bucket
        instead makes a server that has just served its first request report
        zero for up to sixty seconds — which is precisely the moment somebody
        is watching to see whether it worked.

        Args:
            minutes: How far back to average.

        Returns:
            Observations per minute.
        """
        wanted = max(1, minutes)
        counted = sum(bucket.count for bucket in self.buckets(wanted))

        # Whole minutes behind the current one, plus however much of the
        # current one has actually happened. Floored at a few seconds so the
        # first observation of a minute does not divide by nearly zero and
        # report a rate of several thousand.
        into_minute = max(5.0, time.time() % 60)
        span = (wanted - 1) + into_minute / 60

        return counted / span

    def total(self, minutes: int | None = None) -> int:
        """Observations over a window.

        Args:
            minutes: How far back. None means the whole window.

        Returns:
            The count.
        """
        return sum(bucket.count for bucket in self.buckets(minutes))

    def errors(self, minutes: int | None = None) -> int:
        """Failed observations over a window.

        Args:
            minutes: How far back. None means the whole window.

        Returns:
            The count of observations flagged as errors.
        """
        return sum(bucket.errors for bucket in self.buckets(minutes))

    def sum(self, minutes: int | None = None) -> float:
        """The sum of observations over a window.

        Args:
            minutes: How far back. None means the whole window.

        Returns:
            The total.
        """
        return sum(bucket.total for bucket in self.buckets(minutes))

    def mean(self, minutes: int | None = None) -> float:
        """The mean observation over a window.

        Args:
            minutes: How far back. None means the whole window.

        Returns:
            The mean, or zero when nothing was observed.
        """
        buckets = self.buckets(minutes)
        count = sum(bucket.count for bucket in buckets)
        return sum(bucket.total for bucket in buckets) / count if count else 0.0

    def percentile(
        self, fraction: float, minutes: int | None = None, offset: int = 0
    ) -> float:
        """Estimate a percentile across a window.

        The reservoirs of every bucket in the window are merged, so this is an
        estimate over sampled data and is labelled as such wherever it is
        shown.

        Args:
            fraction: Between 0 and 1. 0.95 is the p95.
            minutes: How far back. None means the whole window.
            offset: How many minutes back the window ends.

        Returns:
            The estimate, or zero when nothing was observed.
        """
        merged: list[float] = []
        for bucket in self.buckets(minutes, offset):
            merged.extend(bucket.samples)

        if not merged:
            return 0.0

        merged.sort()
        index = min(len(merged) - 1, max(0, int(round(fraction * (len(merged) - 1)))))
        return merged[index]

    def spark(self, points: int = 10) -> list[float]:
        """Counts reduced to a fixed number of points, for a sparkline.

        Args:
            points: How many points the sparkline draws.

        Returns:
            *points* values, oldest first, each the sum of one slice of the
            window.
        """
        counts = self.counts()
        if not counts:
            return [0.0] * points

        width = max(1, len(counts) // points)
        reduced = [
            float(sum(counts[index : index + width]))
            for index in range(0, len(counts), width)
        ]
        return (
            reduced[-points:]
            if len(reduced) >= points
            else ([0.0] * (points - len(reduced)) + reduced)
        )

    def trend(self, minutes: int = 5) -> float:
        """Change between the last *minutes* and the *minutes* before them.

        This is what the delta beside a stat tile reports.

        Args:
            minutes: The width of each half.

        Returns:
            Later total minus earlier total.
        """
        buckets = self.buckets(minutes * 2)
        earlier = sum(bucket.count for bucket in buckets[:minutes])
        later = sum(bucket.count for bucket in buckets[minutes:])
        return float(later - earlier)

    def clear(self) -> None:
        """Discard everything recorded."""
        self._buckets.clear()

    def __len__(self) -> int:
        """How many buckets are held.

        Returns:
            The bucket count, which is at most the window.
        """
        return len(self._buckets)

    def __repr__(self) -> str:
        """A short description for debugging.

        Returns:
            The name, the window and the total.
        """
        return f"Series({self.name!r}, window={self.window}, total={self.total()})"


class SeriesSet:
    """Named series, created on first use.

    Panels ask for series by name — ``requests``, ``requests.duration``,
    ``cache.hits`` — and a panel that has never seen its subsystem should get
    an empty series rather than a ``KeyError``, so that a chart renders flat
    instead of the panel failing.

    Attributes:
        window: Minutes each series retains.
    """

    __slots__ = ("window", "_series")

    def __init__(self, window: int = 60) -> None:
        """Open a set.

        Args:
            window: Minutes each series retains.
        """
        self.window = window
        self._series: dict[str, Series] = {}

    def __getitem__(self, name: str) -> Series:
        """The series called *name*, created if new.

        Args:
            name: The series' name.

        Returns:
            The series.
        """
        series = self._series.get(name)
        if series is None:
            series = Series(name, self.window)
            self._series[name] = series
        return series

    def __contains__(self, name: object) -> bool:
        """Whether a series exists yet.

        Args:
            name: The name to check.

        Returns:
            True when something has been recorded under it.
        """
        return name in self._series

    def __iter__(self) -> Iterator[Series]:
        """Iterate the series that exist.

        Returns:
            An iterator over them.
        """
        return iter(self._series.values())

    def names(self) -> list[str]:
        """Every series name in use.

        Returns:
            The names, sorted.
        """
        return sorted(self._series)

    def add(self, name: str, value: float = 1.0, **kwargs: object) -> None:
        """Record into a named series.

        Args:
            name: The series' name.
            value: What was measured.
            **kwargs: Passed to :meth:`Series.add`.
        """
        self[name].add(value, **kwargs)  # type: ignore[arg-type]

    def clear(self, names: Iterable[str] | None = None) -> None:
        """Discard recorded data.

        Args:
            names: Series to clear. None clears all of them.
        """
        for name in list(self._series) if names is None else names:
            if name in self._series:
                self._series[name].clear()

    def __repr__(self) -> str:
        """A short description for debugging.

        Returns:
            How many series are held, and over what window.
        """
        return f"SeriesSet({len(self._series)} series, window={self.window})"
