"""
sillo_vise.server — running the application, with vise around it.

:func:`serve` is what ``vise serve`` calls. :func:`install` is separable, and is
what a project would call to mount the dashboard inside its own server.
"""

from __future__ import annotations

from .factory import FACTORY, create, import_application
from .install import Installation, install
from .runner import Server, serve

__all__ = [
    "FACTORY",
    "Installation",
    "Server",
    "create",
    "import_application",
    "install",
    "serve",
]
