"""
sillo_vise.recorder.context — which request is in flight, right here.

A query watcher sitting inside the ORM has no argument telling it which HTTP
request caused the statement it is about to log. A :class:`~contextvars.ContextVar`
does: the request middleware sets it, everything downstream in the same task
reads it, and asyncio copies the context into child tasks, so a query issued
from a ``gather`` inside a handler is still attributed correctly.

The alternative is threading a request id through every framework hook, which
would mean changing the framework. Watchers are supposed to be watchers.

The same mechanism carries the job identifier, so an event emitted inside a
queued task is attributed to the job rather than to whichever request happened
to enqueue it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextvars import ContextVar, Token

__all__ = [
    "current_job",
    "current_request",
    "current_route",
    "job_scope",
    "request_scope",
    "set_route",
]

#: The request being served in this context, as an event id.
_REQUEST: ContextVar[str | None] = ContextVar("vise_request", default=None)

#: The route handling it, once routing has resolved one.
_ROUTE: ContextVar[str] = ContextVar("vise_route", default="")

#: The queued job being executed in this context, as a task id.
_JOB: ContextVar[str | None] = ContextVar("vise_job", default=None)


def current_request() -> str | None:
    """The request in flight here.

    Returns:
        The request's event id, or None outside a request.
    """
    return _REQUEST.get()


def current_route() -> str:
    """The route handling the request in flight here.

    Returns:
        The route's name, or an empty string when routing has not resolved
        one — which is also what a 404 leaves behind.
    """
    return _ROUTE.get()


def current_job() -> str | None:
    """The queued job being executed here.

    Returns:
        The job's task id, or None outside a job.
    """
    return _JOB.get()


def set_route(name: str) -> None:
    """Name the route handling the request in flight.

    Called once routing has resolved, which is after the recorder has already
    minted the request id — the id has to exist before the route is known, or
    a query issued during routing could not be attributed.

    Args:
        name: The route's name.
    """
    _ROUTE.set(name)


class request_scope:
    """Mark a block as serving one request.

    Written as a class rather than a ``@contextmanager`` generator because
    this wraps every single request: a generator-based context manager costs
    a generator object, a frame and two ``throw``/``send`` round trips per
    use, and the request path is the one place in vise where that is worth
    avoiding.

    Attributes:
        request_id: The request being served.
    """

    __slots__ = ("request_id", "_request_token", "_route_token")

    def __init__(self, request_id: str) -> None:
        """Open a scope.

        Args:
            request_id: The request's event id.
        """
        self.request_id = request_id
        self._request_token: Token[str | None] | None = None
        self._route_token: Token[str] | None = None

    def __enter__(self) -> str:
        """Enter the scope.

        Returns:
            The request id, so ``with request_scope(id) as request:`` reads.
        """
        self._request_token = _REQUEST.set(self.request_id)
        self._route_token = _ROUTE.set("")
        return self.request_id

    def __exit__(self, *exception: object) -> None:
        """Leave the scope, restoring whatever was set before it."""
        if self._request_token is not None:
            _REQUEST.reset(self._request_token)
        if self._route_token is not None:
            _ROUTE.reset(self._route_token)


class job_scope:
    """Mark a block as executing one queued job.

    Attributes:
        task_id: The job being executed.
    """

    __slots__ = ("task_id", "_token")

    def __init__(self, task_id: str) -> None:
        """Open a scope.

        Args:
            task_id: The job's task id.
        """
        self.task_id = task_id
        self._token: Token[str | None] | None = None

    def __enter__(self) -> str:
        """Enter the scope.

        Returns:
            The task id.
        """
        self._token = _JOB.set(self.task_id)
        return self.task_id

    def __exit__(self, *exception: object) -> None:
        """Leave the scope, restoring whatever was set before it."""
        if self._token is not None:
            _JOB.reset(self._token)


def _scopes() -> Iterator[ContextVar]:  # pragma: no cover - introspection aid
    """The context variables this module owns.

    Returns:
        An iterator over them, for tests that need to assert nothing leaked.
    """
    yield _REQUEST
    yield _ROUTE
    yield _JOB
