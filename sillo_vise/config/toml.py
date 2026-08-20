"""
sillo_vise.config.toml — one name for the TOML parser, whichever one is here.

``tomllib`` entered the standard library in 3.11. Vise supports 3.10, and
``.vise`` is TOML, so below that a parser has to come from somewhere. ``tomli``
is declared as a dependency under an environment marker and is the same library
``tomllib`` was adopted from, so the two are interchangeable at this API.

The import is resolved once, at module import, rather than inside
:func:`loads` — a per-call ``try``/``except ImportError`` around an import is a
``sys.modules`` lookup on every configuration read and hides the dependency from
anyone reading the module header.
"""

from __future__ import annotations

import sys
from typing import Any

__all__ = ["TOMLDecodeError", "loads"]

if sys.version_info >= (3, 11):
    import tomllib as _toml
else:  # pragma: no cover - exercised on 3.10 only
    try:
        import tomli as _toml
    except ModuleNotFoundError as error:  # pragma: no cover
        raise ModuleNotFoundError(
            "Reading .vise on Python 3.10 needs tomli. It is declared as a "
            "dependency of sillo-vise for this version, so seeing this means "
            "the environment was assembled by hand: pip install tomli"
        ) from error

#: Raised when ``.vise`` is not valid TOML. Re-exported so callers do not have
#: to work out which parser they got.
TOMLDecodeError = _toml.TOMLDecodeError


def loads(text: str) -> dict[str, Any]:
    """Parse TOML *text*.

    Args:
        text: The contents of a ``.vise`` file.

    Returns:
        The parsed table.

    Raises:
        TOMLDecodeError: If the text is not valid TOML.
    """
    return _toml.loads(text)
