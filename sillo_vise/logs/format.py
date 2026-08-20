"""
sillo_vise.logs.format — the units a person reads, not the units a machine stores.

``2.1044921875`` seconds and ``94208`` bytes are both true and neither belongs
in a column somebody scans forty of. These render ``2.10s`` and ``92.0 kB``,
with one significant decision behind each: the width is fixed, so the numbers
line up down the log and a slow request is visible as a longer bar rather than
as a number that has to be read.
"""

from __future__ import annotations

__all__ = ["duration", "elapsed", "number", "size", "truncate"]

#: Byte units, ascending. Decimal rather than binary, matching what every
#: browser network panel shows, so a number here and a number there agree.
_UNITS = ("B", "kB", "MB", "GB", "TB")


def duration(milliseconds: float) -> str:
    """Render a duration for a log line or a table cell.

    Milliseconds below a second, seconds above it, minutes above sixty. Three
    characters of number and a unit, so the column is stable.

    Args:
        milliseconds: The duration.

    Returns:
        The duration as text.
    """
    if milliseconds < 1:
        return f"{milliseconds * 1000:.0f}µs"
    if milliseconds < 1000:
        return f"{milliseconds:.0f}ms"
    if milliseconds < 60_000:
        return f"{milliseconds / 1000:.2f}s"

    minutes, seconds = divmod(milliseconds / 1000, 60)
    return f"{int(minutes)}m {int(seconds):02d}s"


def elapsed(seconds: float) -> str:
    """Render an uptime.

    Args:
        seconds: How long something has been running.

    Returns:
        The duration as text, in the largest two units that apply.
    """
    if seconds < 60:
        return f"{int(seconds)}s"

    minutes, second = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {second:02d}s"

    hours, minute = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minute:02d}m"

    days, hour = divmod(hours, 24)
    return f"{days}d {hour:02d}h"


def size(count: int) -> str:
    """Render a byte count.

    Args:
        count: Bytes.

    Returns:
        The count as text, with a unit.
    """
    value = float(count)
    for unit in _UNITS:
        if value < 1000 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"  # pragma: no cover - the loop always returns


def number(value: float) -> str:
    """Render a count with thousands separators.

    Args:
        value: The count.

    Returns:
        The number as text.
    """
    if value >= 1000:
        return f"{value:,.0f}"
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def truncate(text: str, width: int) -> str:
    """Shorten *text* to *width*, marking that it was shortened.

    The ellipsis is a single character, so the result is exactly *width* and a
    column of truncated paths still lines up.

    Args:
        text: The text.
        width: Characters to allow.

    Returns:
        The text, at most *width* characters.
    """
    return text if len(text) <= width else f"{text[: width - 1]}…"
