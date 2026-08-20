"""
sillo_vise.logs.repeat — print one failure once.

sillo reports an unhandled exception from two layers. ``exception_handler``
logs it, and then ``core.error.handler`` logs it again as the response is being
built — two loggers, one failure, two full tracebacks on the screen a second
apart. Neither is wrong on its own, and together they are the noisiest thing in
the output.

This filter drops the second. The rule is narrow on purpose: a record is
suppressed only when it matches one seen within the last :data:`WINDOW` seconds.
Same thing, same moment, therefore the same event reported twice.

For an ordinary line "matches" means byte-identical. For a traceback it cannot,
because the two layers catch the exception at different depths and so print
different frames — the outer one has the middleware stack the inner one was
called from. What is the same is the exception itself and the line that raised
it, so a traceback is keyed on its deepest frame and its final line. Two
reports of one ``ValueError`` collapse; two genuinely different failures do not.

What this deliberately does not do is collapse repeats generally. A loop logging
"retrying" forty times is forty things happening, and a log that hid thirty-nine
of them would be lying about a retry storm. The window is short enough that only
genuine double-reporting falls inside it.
"""

from __future__ import annotations

import logging
import time

__all__ = ["WINDOW", "RepeatFilter"]

#: How Python opens a formatted traceback.
_TRACEBACK = "Traceback (most recent call last):"

#: How long an identical message is suppressed for. Long enough to cover two
#: layers reporting the same failure, far too short to hide a retry loop.
WINDOW = 1.0


class RepeatFilter(logging.Filter):
    """Drops a message identical to the one just before it.

    Attributes:
        window: Seconds an identical message is suppressed for.
    """

    def __init__(self, window: float = WINDOW) -> None:
        """Build a filter.

        Args:
            window: Seconds an identical message is suppressed for.
        """
        super().__init__()
        self.window = window
        self._last: tuple[str, float] = ("", 0.0)

    def filter(self, record: logging.LogRecord) -> bool:
        """Whether this record should be printed.

        Args:
            record: The record.

        Returns:
            False when it is the same message, again, immediately.
        """
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a record that cannot format is not
            # this filter's problem; let the formatter deal with it.
            return True

        key = _key(message)
        previous, at = self._last
        now = time.monotonic()

        if key == previous and now - at < self.window:
            # Refreshed rather than left alone, so three layers reporting the
            # same failure collapse to one rather than to two.
            self._last = (key, now)
            return False

        self._last = (key, now)
        return True


def _key(message: str) -> str:
    """What makes this message that message.

    Args:
        message: The formatted log message.

    Returns:
        The message itself, or — for a traceback — the deepest frame and the
        exception line, which is what two layers reporting one failure share.
    """
    if _TRACEBACK not in message:
        return message

    lines = [line for line in message.splitlines() if line.strip()]
    if not lines:
        return message

    frames = [line for line in lines if line.strip().startswith('File "')]
    deepest = frames[-1].strip() if frames else ""

    return f"{deepest}\n{lines[-1].strip()}"
