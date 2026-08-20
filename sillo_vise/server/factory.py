"""
sillo_vise.server.factory — how vise survives a reload.

``--reload`` runs the application in a child process, and that child imports the
application itself. An object instrumented in the parent never reaches it, so a
naive ``vise serve --reload`` would record the first run and nothing after it —
the worst kind of bug, because everything looks fine until you save a file.

So with reload on, uvicorn is not handed the project's application at all. It is
handed :func:`create`, as a factory: the child calls it, and it imports the
project's application, instruments it and returns it. Vise is therefore
reattached on every reload, by construction rather than by remembering to.

The child is a fresh interpreter, so nothing can be passed to it in memory. The
target and the configuration overrides travel in the environment, which is the
one channel a re-executed process definitely inherits.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

from ..config import ViseConfig, load_config, parse_config
from ..logs.theme import ACCENT

__all__ = ["FACTORY", "create", "prepare_environment"]

#: The import string handed to uvicorn when reload is on.
FACTORY = "sillo_vise.server.factory:create"

#: Where the target is passed to the child.
TARGET_VARIABLE = "VISE_INTERNAL_TARGET"

#: Where the resolved configuration is passed to the child, as JSON. The child
#: could re-read ``.vise`` itself, and deliberately does not: command-line flags
#: are not in the file, and a reload that quietly dropped ``--port`` would be
#: baffling.
CONFIG_VARIABLE = "VISE_INTERNAL_CONFIG"

#: Set once the first worker has started, so later reloads print one line
#: instead of the whole banner again.
STARTED_VARIABLE = "VISE_INTERNAL_STARTED"


def prepare_environment(target: str, config: ViseConfig) -> None:
    """Put what the child needs into the environment.

    Args:
        target: The application's import string.
        config: The resolved configuration.
    """
    os.environ[TARGET_VARIABLE] = target
    os.environ[CONFIG_VARIABLE] = json.dumps(_serialise(config))


def create() -> Any:
    """Import the project's application and instrument it.

    Called by uvicorn in the reload worker.

    Returns:
        The instrumented application.

    Raises:
        RuntimeError: If the environment does not name an application, which
            means this was called by something other than ``vise serve``.
    """
    target = os.environ.get(TARGET_VARIABLE)
    if not target:
        raise RuntimeError(
            f"{FACTORY} is vise's reload entry point and is not meant to be "
            "imported directly. Run `vise serve`."
        )

    config = _configuration()
    app = import_application(target)

    # Imported here rather than at module scope: this module is imported by the
    # CLI to reach `prepare_environment`, and pulling in the whole server there
    # would cost a uvicorn import on every `vise --help`.
    from .runner import Server

    server = Server(config, target)
    installation = server.prepare(app)

    if os.environ.get(STARTED_VARIABLE):
        # A reload gets one line rather than the whole banner again. The panel
        # count is on it because a reload is exactly when a panel appears or
        # disappears — a database that just came up, a route that just went.
        server.banner.stream.write(
            f"  {server.banner.palette.render('▲', ACCENT)} reloaded"
            f" · {len(installation.live_panels)} panels live\n"
        )
        server.banner.stream.flush()
    else:
        os.environ[STARTED_VARIABLE] = "1"
        server.announce()

    return app


def import_application(target: str) -> Any:
    """Import an application from a ``module:attribute`` string.

    Args:
        target: The import string.

    Returns:
        The application.

    Raises:
        ValueError: If the string is malformed, or nothing matches.
    """
    if ":" not in target:
        raise ValueError(
            f"{target!r} should be written as 'module:attribute', e.g. 'app.main:app'"
        )

    module_name, _, attribute = target.partition(":")

    # A console script's sys.path starts at its own bin directory, so the
    # project's own packages are not importable without this.
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))

    try:
        module = import_module(module_name)
    except ImportError as error:
        raise ValueError(f"Could not import {module_name!r}: {error}") from error

    try:
        return getattr(module, attribute)
    except AttributeError:
        raise ValueError(f"{module_name!r} has no attribute {attribute!r}") from None


def _configuration() -> ViseConfig:
    """The configuration the parent resolved.

    Returns:
        The configuration, rebuilt from the environment, or freshly loaded when
        the parent did not pass one.
    """
    raw = os.environ.get(CONFIG_VARIABLE)
    if not raw:
        return load_config()

    try:
        return parse_config(_to_toml(json.loads(raw)))
    except Exception:  # noqa: BLE001 - a mangled handoff should not stop the server
        return load_config()


def _serialise(config: ViseConfig) -> dict[str, Any]:
    """Reduce a configuration to something JSON can carry.

    Args:
        config: The resolved configuration.

    Returns:
        Nested plain data, with the token intact — this goes to a child process
        of the same user, not over a network.
    """
    data = dataclasses.asdict(config)
    data.pop("source", None)
    return data


def _to_toml(data: dict[str, Any]) -> str:
    """Render plain data back as TOML, so one parser reads every path.

    Rebuilding the dataclasses directly would be shorter and would introduce a
    second way of turning data into a :class:`ViseConfig` — one that does not
    run the loader's validation. Going back through TOML keeps a single path.

    Args:
        data: The serialised configuration.

    Returns:
        A TOML document.
    """
    lines = []
    for section, values in data.items():
        if not isinstance(values, dict):
            continue

        lines.append(f"[{section}]")
        for key, value in values.items():
            if value is None or value == "":
                continue
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")

    return "\n".join(lines)


def _toml_value(value: Any) -> str:
    """Render one value as TOML.

    Args:
        value: The value.

    Returns:
        Its TOML form.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value))
