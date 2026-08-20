"""
sillo_vise.dashboard.security — who may look at this.

The dashboard shows every request the application has served, the SQL it ran and
the exceptions it raised. That is a great deal of information about a system and
its users, and the default has to be narrow: ``access = "local"`` admits only
loopback clients, which is the correct answer for a development server and a
wrong one for nothing.

Three modes:

* ``local`` — the peer address must be loopback. Deliberately *not* satisfied by
  an ``X-Forwarded-For`` header, because a header is a claim made by whoever is
  talking to you.
* ``token`` — a shared secret, in ``Authorization: Bearer`` or a ``token`` query
  parameter, compared in constant time.
* ``open`` — no gate. There is a real use for this, behind somebody else's
  authentication, and it is spelled out rather than reachable by accident.

A refusal says which mode refused and nothing else. "Wrong token" and "not
loopback" are different facts, and telling an unauthenticated caller which one
applies tells them how the gate is configured.
"""

from __future__ import annotations

import hmac
import ipaddress
from typing import Any

__all__ = ["ACCESS_MODES", "AccessGate"]

#: The modes ``[dashboard] access`` accepts.
ACCESS_MODES = ("local", "token", "open")

#: What a refused caller is told. One message for every mode, on purpose.
REFUSAL = "The vise dashboard refused this request."


class AccessGate:
    """Decides whether a caller may reach the dashboard.

    Attributes:
        mode: One of :data:`ACCESS_MODES`.
        token: The shared secret, for ``token`` mode.
    """

    __slots__ = ("mode", "token")

    def __init__(self, mode: str = "local", token: str | None = None) -> None:
        """Build a gate.

        Args:
            mode: One of :data:`ACCESS_MODES`.
            token: The shared secret.

        Raises:
            ValueError: If the mode is unknown, or ``token`` mode was asked for
                without a token. Failing at startup is the point: a dashboard
                that silently fell back to open because the token was missing
                would be the worst possible outcome of a typo.
        """
        if mode not in ACCESS_MODES:
            raise ValueError(
                f"[dashboard] access must be one of {', '.join(ACCESS_MODES)}, not {mode!r}"
            )

        if mode == "token" and not token:
            raise ValueError('[dashboard] access = "token" needs a token')

        self.mode = mode
        self.token = token

    def allows(self, scope: Any) -> bool:
        """Whether this caller may proceed.

        Args:
            scope: The ASGI scope.

        Returns:
            True when the caller is admitted.
        """
        if self.mode == "open":
            return True

        if self.mode == "local":
            return _is_loopback(scope)

        return self._token_matches(scope)

    def _token_matches(self, scope: Any) -> bool:
        """Whether the caller presented the shared secret.

        Args:
            scope: The ASGI scope.

        Returns:
            True when the token matches.
        """
        presented = _presented_token(scope)
        if not presented or not self.token:
            return False

        # Constant time, because a timing side channel on a comparison is one
        # of the few attacks a local dashboard is genuinely exposed to.
        return hmac.compare_digest(presented, self.token)

    def describe(self) -> str:
        """What this gate admits, in prose, for the banner.

        Returns:
            One short phrase.
        """
        return {
            "local": "loopback only",
            "token": "token required",
            "open": "open — anyone who can reach it",
        }[self.mode]


def _is_loopback(scope: Any) -> bool:
    """Whether the peer is on this machine.

    Read from the transport's peer address, never from a forwarded header. A
    header is a claim made by whoever is talking to you, and this is the check
    standing between a stranger and every request the application has served.

    Args:
        scope: The ASGI scope.

    Returns:
        True when the peer address is a loopback address.
    """
    client = scope.get("client")
    if not client:
        # No peer address at all. A test client, or a transport that does not
        # report one. Refusing is the safe answer and costs a test one line of
        # configuration.
        return False

    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


def _presented_token(scope: Any) -> str:
    """The token a caller presented, if any.

    Accepts a bearer header or a query parameter. The query parameter exists so
    a dashboard can be opened by clicking a link, and it is the reason
    ``recorder.redact_params`` covers ``token`` — the dashboard's own URL must
    not end up in the dashboard's own request log.

    Args:
        scope: The ASGI scope.

    Returns:
        The token, or an empty string.
    """
    for name, value in scope.get("headers", ()):
        if name == b"authorization":
            text = value.decode("latin-1")
            if text.lower().startswith("bearer "):
                return text[7:].strip()
            return text.strip()

    query = scope.get("query_string", b"").decode("latin-1")
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == "token":
            return value

    return ""
