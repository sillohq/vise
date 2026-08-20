"""
sillo_vise.config.loader — find ``.vise``, read it, and layer the rest on top.

Four sources, in increasing precedence: the defaults in
:mod:`sillo_vise.config.schema`, the ``.vise`` file, ``VISE_*`` environment
variables, and command-line flags. Each layer only overrides keys it actually
sets, so ``--port 9000`` does not silently reset the recorder buffer to its
default.

Discovery walks up from the working directory, the same way a tool looks for
``pyproject.toml``, so ``vise serve`` works from a subdirectory of the project.
It stops at a filesystem root or at a directory holding a ``.git``, because
walking past the repository into somebody's home directory and picking up a
``.vise`` there is a surprise nobody wants.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeVar

from .schema import (
    AppConfig,
    DashboardConfig,
    LogConfig,
    PanelConfig,
    RecorderConfig,
    ServerConfig,
    ViseConfig,
)
from .toml import TOMLDecodeError, loads

__all__ = [
    "ConfigError",
    "find_config_file",
    "load_config",
    "parse_config",
]

#: The filename looked for while walking up from the working directory.
CONFIG_FILENAME = ".vise"

#: Prefix for the environment overlay. ``VISE_SERVER_PORT=9000`` sets
#: ``[server] port``, and a section name is required so that a future
#: top-level key cannot collide with a section.
ENV_PREFIX = "VISE_"

_Section = TypeVar("_Section")

#: Section name in the file, the attribute on ViseConfig, and its class.
_SECTIONS: tuple[tuple[str, str, type], ...] = (
    ("app", "app", AppConfig),
    ("server", "server", ServerConfig),
    ("dashboard", "dashboard", DashboardConfig),
    ("recorder", "recorder", RecorderConfig),
    ("logs", "logs", LogConfig),
    ("panels", "panels", PanelConfig),
)


class ConfigError(Exception):
    """A ``.vise`` file exists and cannot be used.

    Distinct from finding no file at all, which is ordinary and produces the
    defaults. This is raised when the project said something and it did not
    make sense — a misspelled key, a string where a port belongs — because
    silently ignoring it means running with settings the project did not ask
    for and cannot see.
    """


def find_config_file(start: Path | str | None = None) -> Path | None:
    """Look for ``.vise``, walking upward from *start*.

    Args:
        start: Directory to begin at. Defaults to the working directory.

    Returns:
        The path to the first ``.vise`` found, or None.
    """
    directory = Path(start or Path.cwd()).resolve()

    for candidate in (directory, *directory.parents):
        found = candidate / CONFIG_FILENAME
        if found.is_file():
            return found
        # A repository boundary is as far up as a project's configuration can
        # sensibly live.
        if (candidate / ".git").exists():
            return None

    return None


def _coerce(value: Any, annotation: Any, where: str) -> Any:
    """Bring a parsed TOML value to the type a field declares.

    TOML already distinguishes integers, floats, booleans and strings, so this
    is mostly about tuples — TOML has arrays, dataclasses here hold tuples —
    and about accepting an integer where a string is declared, which is the
    one substitution a person makes without noticing.

    Args:
        value: The parsed value.
        annotation: The field's declared type, as a string.
        where: Dotted path to the key, for the error message.

    Returns:
        The coerced value.

    Raises:
        ConfigError: If the value cannot be the declared type.
    """
    text = str(annotation)

    if "tuple" in text:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        raise ConfigError(f"{where} should be a list, not {type(value).__name__}")

    if "bool" in text:
        if isinstance(value, bool):
            return value
        raise ConfigError(f"{where} should be true or false")

    if "int" in text and "str" not in text:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where} should be a whole number")
        return value

    if "str" in text:
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            return str(value)
        raise ConfigError(f"{where} should be text")

    return value


def _build(section: Any, data: Mapping[str, Any], name: str) -> Any:
    """Construct one configuration section from parsed data.

    Args:
        section: The dataclass to build.
        data: The table under *name* in the file.
        name: The section's name, for error messages.

    Returns:
        The constructed section.

    Raises:
        ConfigError: On an unknown key, or a value of the wrong type.

    Note:
        *section* is typed loosely because ``dataclasses.fields`` wants a
        dataclass and a type variable is not one as far as a checker is
        concerned. The looseness is confined here; every caller passes a
        section class from :data:`_SECTIONS`.
    """
    fields = {field.name: field for field in dataclasses.fields(section)}

    unknown = set(data) - set(fields)
    if unknown:
        known = ", ".join(sorted(fields))
        raise ConfigError(
            f"[{name}] has no {', '.join(sorted(unknown))}. It accepts: {known}"
        )

    values = {
        key: _coerce(value, fields[key].type, f"[{name}] {key}")
        for key, value in data.items()
    }
    return section(**values)


def parse_config(text: str, source: str | None = None) -> ViseConfig:
    """Turn the text of a ``.vise`` file into a :class:`ViseConfig`.

    Args:
        text: The file's contents.
        source: Path to report as the origin.

    Returns:
        The configuration, with defaults for everything the file left out.

    Raises:
        ConfigError: If the text is not valid TOML, or says something unknown.
    """
    try:
        parsed = loads(text)
    except TOMLDecodeError as error:
        where = f" in {source}" if source else ""
        raise ConfigError(
            f"{CONFIG_FILENAME} is not valid TOML{where}: {error}"
        ) from error

    unknown = set(parsed) - {name for name, _, _ in _SECTIONS}
    if unknown:
        sections = ", ".join(name for name, _, _ in _SECTIONS)
        raise ConfigError(
            f"Unknown section {', '.join(sorted(unknown))}. "
            f"{CONFIG_FILENAME} has: {sections}"
        )

    built: dict[str, Any] = {"source": source}
    for name, attribute, section in _SECTIONS:
        table = parsed.get(name, {})
        if not isinstance(table, dict):
            raise ConfigError(f"[{name}] should be a table")
        built[attribute] = _build(section, table, name)

    return ViseConfig(**built)


def _environment_overlay(environ: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Read ``VISE_SECTION_KEY`` variables into nested tables.

    Values arrive as strings, so booleans and numbers are recognised here
    rather than left for :func:`_coerce`, which is written against TOML's
    already-typed output. A list is comma-separated.

    Args:
        environ: The environment to read.

    Returns:
        Tables keyed by section name, holding only what was set.
    """
    sections = {name for name, _, _ in _SECTIONS}
    fields = {
        name: {field.name for field in dataclasses.fields(cls)}
        for name, _, cls in _SECTIONS
    }
    types = {
        name: {field.name: str(field.type) for field in dataclasses.fields(cls)}
        for name, _, cls in _SECTIONS
    }

    overlay: dict[str, dict[str, Any]] = {}
    for variable, raw in environ.items():
        if not variable.startswith(ENV_PREFIX):
            continue

        remainder = variable[len(ENV_PREFIX) :].lower()
        section, _, key = remainder.partition("_")
        if section not in sections or key not in fields[section]:
            continue

        annotation = types[section][key]
        overlay.setdefault(section, {})[key] = _parse_environment_value(raw, annotation)

    return overlay


def _parse_environment_value(raw: str, annotation: str) -> Any:
    """Interpret one environment string as the type a field declares.

    Args:
        raw: The variable's value.
        annotation: The field's declared type, as a string.

    Returns:
        The parsed value.

    Raises:
        ConfigError: If a numeric field was given something that is not one.
    """
    if "tuple" in annotation:
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    if "bool" in annotation:
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    if "int" in annotation and "str" not in annotation:
        try:
            return int(raw)
        except ValueError as error:
            raise ConfigError(f"{raw!r} is not a whole number") from error

    return raw


def _apply(config: ViseConfig, overlay: Mapping[str, Mapping[str, Any]]) -> ViseConfig:
    """Layer *overlay* over *config*, section by section.

    Args:
        config: The configuration so far.
        overlay: Tables keyed by section name, holding only what to change.

    Returns:
        A new configuration. Nothing is mutated, so the caller's copy stays
        the one it was handed.
    """
    changes: dict[str, Any] = {}
    for name, attribute, _ in _SECTIONS:
        table = overlay.get(name)
        if table:
            changes[attribute] = dataclasses.replace(
                getattr(config, attribute), **table
            )

    return dataclasses.replace(config, **changes) if changes else config


def load_config(
    start: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> ViseConfig:
    """Resolve the configuration for a project.

    Args:
        start: Directory to search from. Defaults to the working directory.
        environ: Environment to overlay. Defaults to ``os.environ``.
        overrides: Command-line flags, as tables keyed by section name. These
            win over everything, and should hold only what was actually typed
            — a flag defaulted to its schema default is indistinguishable from
            one the user set, and would override the file.

    Returns:
        The resolved configuration.

    Raises:
        ConfigError: If a ``.vise`` file exists and cannot be used.
    """
    path = find_config_file(start)

    if path is None:
        config = ViseConfig()
    else:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigError(f"{path} could not be read: {error}") from error
        config = parse_config(text, source=str(path))

    config = _apply(
        config, _environment_overlay(os.environ if environ is None else environ)
    )

    if overrides:
        config = _apply(config, overrides)

    return config
