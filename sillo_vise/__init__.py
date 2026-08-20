"""
sillo-vise — the Sillo development server, with Foreman built in.

``vise serve`` runs the project's application, replaces uvicorn's logging with
something a person can read at a glance, and mounts the Foreman operations
dashboard alongside it. Everything it does is configured by a ``.vise`` file in
the project root, and everything it shows is observed from the application that
is actually running.

The shape of the thing is one recorder and many watchers. Fourteen panels with
fourteen collection paths would be fourteen storage decisions and fourteen ways
to leak a credential; instead there is a single :class:`~sillo_vise.recorder.Recorder`
with a single bounded store, and one watcher per concern writing into it.

A panel is registered only when its watcher can observe something real. An
application with no Redis has no Queues panel, rather than a Queues panel
showing zeroes or, worse, invented numbers.
"""

from __future__ import annotations

__all__ = ["__version__"]

#: Kept in step with ``version`` in pyproject.toml. The release workflow
#: refuses to publish when the two disagree, because a release that reports a
#: version it is not is worse than one that fails to build.
__version__: str = "0.1.0"
