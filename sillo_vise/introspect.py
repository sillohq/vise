"""
sillo_vise.introspect — read the application without changing it.

Two panels and one watcher need to know what routes exist. The Routes panel
lists them, the Config panel counts them, and the request watcher wants the
name of the route that handled each request.

That last one is not free. sillo's router does not put the matched route on the
ASGI scope — it sets ``route_params`` and dispatches — so there is nothing to
read afterwards. The alternatives were to patch the router, which a watcher has
no business doing, or to resolve the name after the fact. This resolves, and
caches: the first request to a path walks the route table, and every request
after it is a dictionary lookup.

The cache is bounded. A path space is small in a development server and
unbounded on the open internet, and a dashboard that turns a 404 flood into
unbounded memory growth would be a poor sort of operations tool.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

__all__ = ["RouteInfo", "RouteResolver", "walk_routes"]

#: Resolved names kept before the oldest are dropped.
CACHE_LIMIT = 2048


@dataclasses.dataclass(frozen=True, slots=True)
class RouteInfo:
    """One row of the route table.

    Attributes:
        methods: HTTP methods, sorted. ``("WEBSOCKET",)`` for a socket route
            and ``("MOUNT",)`` for a mounted application that declares none.
        path: The pattern as it was declared, including the mount prefix.
        name: The route's name, or the handler's when it was not named.
        handler: The handler's qualified name.
        auth: ``"required"`` when the route declares an ``auth=``, else
            ``"open"``.
        middleware: How many middlewares the route declares of its own.
    """

    methods: tuple[str, ...]
    path: str
    name: str
    handler: str
    auth: str
    middleware: int

    @property
    def method(self) -> str:
        """The primary method, for a single-method column.

        HEAD is dropped when GET is present: the framework registers the pair
        together, and a table showing ``GET,HEAD`` on every row is a table with
        a wasted column.

        Returns:
            The method to show.
        """
        methods = [name for name in self.methods if name != "HEAD"] or list(
            self.methods
        )
        return methods[0] if len(methods) == 1 else ",".join(methods)


def _path_of(route: Any) -> str:
    """The pattern a route matches.

    sillo's ``Route`` keeps it on ``raw_path``. ``path`` exists on some route
    types and not others, so both are tried.

    Args:
        route: The route.

    Returns:
        The pattern, or an empty string.
    """
    return getattr(route, "raw_path", None) or getattr(route, "path", "") or ""


def _handler_of(route: Any) -> Any:
    """The callable a route dispatches to.

    Args:
        route: The route.

    Returns:
        The handler, or None.
    """
    return getattr(route, "endpoint", None) or getattr(route, "handler", None)


def walk_routes(app: Any, prefix: str = "") -> list[RouteInfo]:
    """Flatten an application's route table, descending into mounted routers.

    A mounted router is one entry holding routes of its own, and its children
    carry paths relative to the mount. Listing only the top level shows
    ``/api`` and hides every route under it, which is the opposite of what
    someone opens this panel to find out.

    Args:
        app: The application, or a router.
        prefix: Path the enclosing mount is under.

    Returns:
        Every route, sorted by path.
    """
    routes = getattr(getattr(app, "router", app), "routes", None) or []
    return sorted(_walk(routes, prefix), key=lambda route: (route.path, route.methods))


def _walk(routes: Any, prefix: str) -> Iterator[RouteInfo]:
    """Yield a route table, recursing into mounts.

    Args:
        routes: Routes to walk.
        prefix: Path the enclosing mount is under.

    Yields:
        One :class:`RouteInfo` per leaf route.
    """
    for route in routes:
        path = prefix + _path_of(route)
        children = getattr(route, "routes", None)

        if children:
            yield from _walk(children, path)
            continue

        methods = getattr(route, "methods", None)
        if methods:
            label = tuple(sorted(methods))
        elif "websocket" in type(route).__name__.lower():
            label = ("WEBSOCKET",)
        else:
            # A mount with nothing under it — a static directory, say. Calling
            # it WEBSOCKET because it declares no methods would be a guess.
            label = ("MOUNT",)

        handler = _handler_of(route)
        yield RouteInfo(
            methods=label,
            path=path or "/",
            name=getattr(route, "name", "") or getattr(handler, "__name__", ""),
            handler=getattr(handler, "__qualname__", "")
            or getattr(handler, "__name__", ""),
            auth="required" if getattr(route, "auth", None) else "open",
            middleware=len(getattr(route, "middleware", ()) or ()),
        )


class RouteResolver:
    """Names the route that handled a request, with a bounded cache.

    Attributes:
        app: The application whose routes are searched.
        limit: Cached answers kept.
    """

    __slots__ = ("app", "limit", "_cache", "_routes")

    def __init__(self, app: Any, limit: int = CACHE_LIMIT) -> None:
        """Build a resolver.

        Args:
            app: The application.
            limit: Cached answers to keep.
        """
        self.app = app
        self.limit = limit
        self._cache: dict[tuple[str, str], str] = {}
        self._routes: list[Any] | None = None

    def _all(self) -> list[Any]:
        """Every leaf route, resolved once.

        Deferred rather than read in ``__init__`` because routes are still
        being registered when vise wraps the application: the admin panel
        registers its own during startup, and a table captured before that
        would name none of them.

        Returns:
            The routes.
        """
        if self._routes is None:
            routes = (
                getattr(getattr(self.app, "router", self.app), "routes", None) or []
            )
            self._routes = list(_leaves(routes))
        return self._routes

    def resolve(self, scope: Any) -> str:
        """Name the route matching *scope*.

        Args:
            scope: The ASGI scope of a finished request.

        Returns:
            The route's name, or an empty string when nothing matched — which
            is the ordinary answer for a 404 and for a mounted application
            that does its own routing.
        """
        key = (scope.get("method", ""), scope.get("path", ""))

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        name = self._search(key[0], key[1])

        if len(self._cache) >= self.limit:
            # Dictionaries iterate in insertion order, so this drops the
            # least recently *added* entry. Not a true LRU, and it does not
            # need to be: the job is to keep a 404 flood from growing memory
            # without bound, not to maximise a hit rate.
            self._cache.pop(next(iter(self._cache)), None)

        self._cache[key] = name
        return name

    def _search(self, method: str, path: str) -> str:
        """Walk the route table for a full match.

        A fresh dictionary is built for the probe rather than the live scope
        being passed through, because ``Route.match`` writes to what it is
        given and a watcher must not mutate the request it is observing.

        Args:
            method: The request's method.
            path: The request's path.

        Returns:
            The matched route's name, or an empty string.
        """
        probe = {"type": "http", "method": method or "GET", "path": path or "/"}

        for route in self._all():
            try:
                match, _ = route.match(probe)
            except Exception:  # noqa: BLE001 - a route that cannot match is not a crash
                continue

            if getattr(match, "name", "") == "FULL":
                handler = _handler_of(route)
                return getattr(route, "name", "") or getattr(handler, "__name__", "")

        return ""

    def invalidate(self) -> None:
        """Forget the route table and every resolved name.

        Called when routes may have changed, which in a development server
        means after a reload.
        """
        self._cache.clear()
        self._routes = None


def _leaves(routes: Any) -> Iterator[Any]:
    """Yield every route that dispatches, descending into mounts.

    Args:
        routes: Routes to walk.

    Yields:
        Routes with no children of their own.
    """
    for route in routes:
        children = getattr(route, "routes", None)
        if children:
            yield from _leaves(children)
        elif hasattr(route, "match"):
            yield route
