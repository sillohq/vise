"""
sillo_vise.config — the ``.vise`` file.

:func:`load_config` is what everything else calls. The rest is exported so that
``vise doctor`` can report where a value came from, and so tests can parse a
string without touching the filesystem.
"""

from __future__ import annotations

from .loader import (
    CONFIG_FILENAME,
    ENV_PREFIX,
    ConfigError,
    find_config_file,
    load_config,
    parse_config,
)
from .schema import (
    DEFAULT_REDACT,
    DEFAULT_WATCH,
    AppConfig,
    DashboardConfig,
    LogConfig,
    PanelConfig,
    RecorderConfig,
    ServerConfig,
    ViseConfig,
)
from .template import TEMPLATE, render_template

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_REDACT",
    "DEFAULT_WATCH",
    "ENV_PREFIX",
    "TEMPLATE",
    "AppConfig",
    "ConfigError",
    "DashboardConfig",
    "LogConfig",
    "PanelConfig",
    "RecorderConfig",
    "ServerConfig",
    "ViseConfig",
    "find_config_file",
    "load_config",
    "parse_config",
    "render_template",
]
