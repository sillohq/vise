"""
sillo_vise.cli.discover — find the project's application.

The order is the framework's own, deliberately: ``[app] target`` in ``.vise``,
then ``SILLO_APP``, then ``[tool.sillo] app`` in ``pyproject.toml``, then the
usual filenames. A project that already told ``sillo`` where its application is
should not have to tell ``vise`` separately.

What is returned is the import *string*, not the object. Reload re-imports the
application in a fresh process, and a string is the only form that survives
that — so every command here deals in strings and imports at the last moment.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import ViseConfig

__all__ = ["DEFAULT_APPS", "discover_target"]

#: Where an application is looked for when nothing points at one. Matches
#: ``sillo.__main__.DEFAULT_APPS``.
DEFAULT_APPS = ("app.main:app", "main:app", "app:app")

#: The framework's own override.
APP_VARIABLE = "SILLO_APP"


def discover_target(config: ViseConfig, start: Path | None = None) -> str | None:
    """Find the import string naming the project's application.

    Args:
        config: The resolved configuration, whose ``[app] target`` wins.
        start: Directory to look in. Defaults to the working directory.

    Returns:
        The import string, or None when nothing was found.
    """
    if config.app.target:
        return config.app.target

    from_environment = os.environ.get(APP_VARIABLE)
    if from_environment:
        return from_environment

    directory = Path(start or Path.cwd())

    configured = _from_pyproject(directory)
    if configured:
        return configured

    for candidate in DEFAULT_APPS:
        module, _, _ = candidate.partition(":")
        if _module_exists(directory, module):
            return candidate

    return None


def _from_pyproject(directory: Path) -> str | None:
    """Read ``[tool.sillo] app`` from a project's ``pyproject.toml``.

    Parsed by hand rather than through the TOML reader, for one reason: this
    runs before anything is imported, on a file the project may have written
    for a different tool entirely, and a parse error in an unrelated section
    should not stop ``vise serve`` from finding an application it could have
    found by reading two lines.

    Args:
        directory: Where to look.

    Returns:
        The import string, or None.
    """
    path = directory / "pyproject.toml"
    if not path.is_file():
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    in_section = False
    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == "[tool.sillo]"
            continue

        if in_section and stripped.startswith("app ="):
            value = stripped.partition("=")[2].strip()
            if len(value) > 1 and value[0] in "\"'" and value[-1] == value[0]:
                return value[1:-1]

    return None


def _module_exists(directory: Path, module: str) -> bool:
    """Whether a module name resolves to a file in *directory*.

    Checked on the filesystem rather than by importing. Importing to find out
    whether something is importable runs the project's module as a side effect
    of a question, which for ``main:app`` means starting a server to discover
    that a server could be started.

    Args:
        directory: The project root.
        module: A dotted module name.

    Returns:
        True when a matching file or package exists.
    """
    parts = module.split(".")
    base = directory.joinpath(*parts)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()
