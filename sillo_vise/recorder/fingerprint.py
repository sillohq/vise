"""
sillo_vise.recorder.fingerprint — group things that are the same query twice.

Two panels need to say "this happened 47 times" about events that are not
byte-identical. The Queries panel groups statements that differ only in their
literals, which is how N+1 detection names a relation rather than listing forty
seven near-identical selects. The Exceptions panel groups by type and location,
which is how a group gets a count, a first-seen and a trend.

Both are the same operation — reduce an event to a stable key — so both live
here rather than one in each panel, where the two would drift.

Fingerprinting SQL by regular expression is approximate. A literal inside a
string that contains a comma will confuse it. That is acceptable for grouping a
development dashboard's query log, and would not be acceptable for anything
that made decisions from it, which this does not.
"""

from __future__ import annotations

import re
import traceback

__all__ = ["fingerprint_sql", "is_n_plus_one", "short_sql", "where_raised"]

#: A quoted string literal, single or double.
_STRINGS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")

#: A bare number, including decimals and negatives.
_NUMBERS = re.compile(r"\b-?\d+(?:\.\d+)?\b")

#: A run of placeholders — ``$1, $2, $3`` or ``?, ?, ?`` — which differ only in
#: how many rows an ``IN`` clause happened to want.
_PLACEHOLDER_RUN = re.compile(r"(?:[$:]\d+|\?)(?:\s*,\s*(?:[$:]\d+|\?))+")

#: Runs of whitespace, including the newlines an ORM leaves in.
_SPACE = re.compile(r"\s+")


def fingerprint_sql(sql: str) -> str:
    """Reduce a statement to what makes it that statement.

    Literals become ``?``, a run of placeholders collapses to one, and
    whitespace normalises — so forty seven selects that differ only in the id
    they look up share a fingerprint, which is exactly the shape of an N+1.

    Args:
        sql: The statement.

    Returns:
        A stable key for statements of the same shape.
    """
    if not sql:
        return ""

    # Order matters. Placeholder runs are collapsed before bare numbers,
    # because `$1, $2, $3` is digits too — reducing numbers first turns it
    # into `$?, $?, $?`, which no longer matches a run and so leaves a
    # three-element IN clause looking different from a four-element one.
    reduced = _STRINGS.sub("?", sql)
    reduced = _PLACEHOLDER_RUN.sub("?", reduced)
    reduced = _NUMBERS.sub("?", reduced)
    return _SPACE.sub(" ", reduced).strip().lower()


def short_sql(sql: str, limit: int = 120) -> str:
    """A statement on one line, for a table cell.

    Args:
        sql: The statement.
        limit: Characters to keep.

    Returns:
        The statement, collapsed and truncated.
    """
    single = _SPACE.sub(" ", sql).strip()
    return single if len(single) <= limit else f"{single[: limit - 1]}…"


def is_n_plus_one(count: int, threshold: int = 5) -> bool:
    """Whether a group of identical statements looks like an N+1.

    A handful of repeats inside one request is ordinary — a loop over five
    things. Past the threshold it is a relation that should have been fetched
    with the parent.

    Args:
        count: How many times the fingerprint appeared in one request.
        threshold: Repeats before it is called out.

    Returns:
        True when the group should be flagged.
    """
    return count > threshold


def where_raised(error: BaseException) -> str:
    """Where an exception actually came from.

    The deepest frame, not the shallowest: the top of a traceback is whichever
    middleware caught it, and grouping on that puts every exception in the
    application into one group called ``ExceptionMiddleware``.

    Args:
        error: The exception.

    Returns:
        ``file:line``, or an empty string when there is no traceback.
    """
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return ""

    frame = frames[-1]
    filename = frame.filename.rsplit("/", 2)
    where = "/".join(filename[-2:]) if len(filename) > 1 else frame.filename
    return f"{where}:{frame.lineno}"
