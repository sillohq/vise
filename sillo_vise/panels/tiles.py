"""
sillo_vise.panels.tiles — the stat tiles, and the one rule about their colour.

A tile is a label, a value, a delta, a tone and a sparkline. Four of them sit
across the top of most panels, and between them they are the first thing anyone
reads, so the rule they follow matters more than it looks:

**A tone says whether the number is good, not which direction it moved.**

Latency falling is green and latency rising is amber; queue size rising is amber
and queue size falling is green; throughput is the other way round. Colouring by
direction would make a queue draining look like a problem, which is the one
thing an operations dashboard must never do. So a tile is built through
:func:`tile` with an explicit sense — ``higher_is_better`` — rather than by
letting each panel guess.

The palette is the one on sillo.build: emerald for good, amber for worrying,
the brand red for bad, and muted for a number that is neither.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..logs.format import duration, number, size
from ..recorder import Series

__all__ = [
    "SPARK_POINTS",
    "TONE_BAD",
    "TONE_GOOD",
    "TONE_INFO",
    "TONE_MUTED",
    "TONE_WARN",
    "bytes_tile",
    "duration_tile",
    "rate_tile",
    "spark_of",
    "threshold_tone",
    "tile",
    "trend_tile",
]

#: The number is healthy.
TONE_GOOD = "good"

#: The number is worth watching.
TONE_WARN = "warn"

#: The number is a problem.
TONE_BAD = "bad"

#: The number is a fact, not a judgement — a version, a backend name.
TONE_MUTED = "muted"

#: The number is notable without being good or bad — a count in flight.
TONE_INFO = "info"

#: Points a sparkline draws.
SPARK_POINTS = 12


def tile(
    label: str,
    value: Any,
    *,
    delta: Any = "",
    tone: str = TONE_MUTED,
    spark: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Build one stat tile.

    Args:
        label: What the number measures.
        value: The number, already formatted.
        delta: The change beside it.
        tone: One of the tone constants.
        spark: Points for the sparkline.

    Returns:
        The tile.
    """
    return {
        "label": label,
        "value": str(value),
        "delta": str(delta),
        "tone": tone,
        "spark": list(spark or []),
    }


def trend_tile(
    label: str,
    value: Any,
    series: Series,
    *,
    higher_is_better: bool,
    unit: str = "",
    minutes: int = 5,
) -> dict[str, Any]:
    """Build a tile whose delta and tone come from a series' trend.

    The sense is a required argument rather than a default, because getting it
    wrong is the failure this module exists to prevent: a queue draining must
    not be amber, and throughput collapsing must not be green.

    Args:
        label: What the number measures.
        value: The number, already formatted.
        series: The series behind it.
        higher_is_better: Whether a rise is good news.
        unit: Suffix for the delta.
        minutes: Width of each half of the comparison.

    Returns:
        The tile.
    """
    change = series.trend(minutes)
    return tile(
        label,
        value,
        delta=_delta(change, unit),
        tone=_tone(change, higher_is_better),
        spark=spark_of(series),
    )


def _delta(change: float, unit: str) -> str:
    """Render a change with its sign.

    Args:
        change: The difference.
        unit: Suffix.

    Returns:
        The delta as text, or an em dash when nothing moved.
    """
    if not change:
        return "—"
    return f"{'+' if change > 0 else ''}{number(change)}{unit}"


def _tone(change: float, higher_is_better: bool) -> str:
    """Judge a change.

    Args:
        change: The difference.
        higher_is_better: Whether a rise is good news.

    Returns:
        A tone constant.
    """
    if not change:
        return TONE_MUTED
    good = change > 0 if higher_is_better else change < 0
    return TONE_GOOD if good else TONE_WARN


def spark_of(series: Series, points: int = SPARK_POINTS) -> list[float]:
    """The sparkline for a series.

    Args:
        series: The series.
        points: How many points to draw.

    Returns:
        The points, oldest first.
    """
    return series.spark(points)


def threshold_tone(value: float, warn: float, bad: float) -> str:
    """Judge a number against two thresholds.

    For the tiles where "good" is not a direction but a range — an error rate,
    a pool saturation, an oldest-pending age.

    Args:
        value: The number.
        warn: At or above this, it is worth watching.
        bad: At or above this, it is a problem.

    Returns:
        A tone constant.
    """
    if value >= bad:
        return TONE_BAD
    if value >= warn:
        return TONE_WARN
    return TONE_GOOD


def rate_tile(label: str, series: Series, *, unit: str = "/ min") -> dict[str, Any]:
    """Build a tile showing a per-minute rate.

    Args:
        label: What the rate measures.
        series: The series behind it.
        unit: Suffix on the label.

    Returns:
        The tile.
    """
    return trend_tile(
        f"{label} {unit}".strip(),
        number(series.rate(5)),
        series,
        higher_is_better=True,
    )


def duration_tile(
    label: str, series: Series, fraction: float = 0.95, *, minutes: int = 5
) -> dict[str, Any]:
    """Build a tile showing an estimated percentile duration.

    The baseline is the same percentile over the *preceding* window of the same
    width, so the delta answers "is this worse than it just was". A wider window
    containing both halves would be dominated by whichever is worse, and a
    latency that had just tripled would read as no change at all.

    Lower is better, always — stated by passing ``higher_is_better=False``
    rather than by negating the change and hoping the reader follows, which is
    how this was first written and how it came out amber for a latency that had
    fallen by 390ms.

    Args:
        label: What the duration measures.
        series: The series behind it.
        fraction: Which percentile. 0.95 is the p95.
        minutes: How far back the recent window runs.

    Returns:
        The tile.
    """
    recent = series.percentile(fraction, minutes)
    baseline = series.percentile(fraction, minutes, offset=minutes)
    change = recent - baseline if baseline else 0.0

    return tile(
        label,
        duration(recent),
        delta=_delta(change, "ms") if round(change) else "—",
        tone=_tone(change, higher_is_better=False) if round(change) else TONE_MUTED,
        spark=spark_of(series),
    )


def bytes_tile(label: str, series: Series) -> dict[str, Any]:
    """Build a tile showing a byte total.

    Args:
        label: What the total measures.
        series: The series behind it.

    Returns:
        The tile.
    """
    return tile(
        label,
        size(int(series.sum())),
        delta="1h",
        tone=TONE_MUTED,
        spark=spark_of(series),
    )
