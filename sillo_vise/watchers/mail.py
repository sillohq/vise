"""
sillo_vise.watchers.mail — everything sent or suppressed, and what the recipient got.

The panel's most useful case is the one that looks least like monitoring:
development mail that was *suppressed*. A framework that quietly discards
messages outside production leaves a developer guessing whether the password
reset was ever built; listing them, with the rendered body, answers it.

``MailClient.send_message`` is the hook, and it is the right one because it is
the funnel: ``send_email`` builds a message and calls it, ``send_template_email``
calls ``send_email``. Wrapping the funnel records every path exactly once, where
wrapping the three entry points would record a templated message three times.

The recorded event carries the recipients, the subject, the template and the
outcome — not the body. The body is where a password reset link lives, and a
dashboard that holds one is a dashboard that hands out account access.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..recorder import Recorder
from .base import Availability, Watcher, available, unavailable

__all__ = ["MailWatcher"]

#: The one method every send path funnels through.
_SEND = "send_message"


class MailWatcher(Watcher):
    """Records every message the application sends.

    Attributes:
        client: The class that was wrapped.
        original: The method taken off it.
    """

    name = "mail"
    requires = "sillo.mail"

    __slots__ = ("client", "original")

    def __init__(self) -> None:
        """Build a detached watcher."""
        super().__init__()
        self.client: type | None = None
        self.original: Callable | None = None

    def probe(self, app: Any) -> Availability:
        """Report whether mail is configured.

        Args:
            app: The application.

        Returns:
            Whether there is a mailer to watch.
        """
        client = _client_class()
        if client is None:
            return unavailable("sillo.mail is not available")

        client = _configured_client(app)
        if client is None:
            return unavailable("no mailer — sillo.mail.setup_mail has not run")

        return available(_transport(client))

    def attach(self, app: Any, recorder: Recorder) -> None:
        """Wrap the mail client's send method.

        Args:
            app: The application.
            recorder: Where events go.
        """
        super().attach(app, recorder)

        client = _client_class()
        if client is None:  # pragma: no cover - probe said yes
            return

        original = client.__dict__.get(_SEND)
        if original is None or getattr(original, "__vise_wrapped__", False):
            return

        self.client = client
        self.original = original
        setattr(client, _SEND, self._wrap(original))

    def _wrap(self, original: Callable) -> Callable:
        """Build the replacement for the send method.

        Args:
            original: What it was.

        Returns:
            A coroutine function that records the message and its outcome.
        """

        async def wrapped(client: Any, message: Any, *args: Any, **kwargs: Any) -> Any:
            """Send the message, then record it."""
            try:
                result = await original(client, message, *args, **kwargs)
            except Exception as error:
                self._record(
                    client, message, "failed", f"{type(error).__name__}: {error}"
                )
                raise

            self._record(client, message, _outcome(client, result), "")
            return result

        wrapped.__name__ = _SEND
        wrapped.__qualname__ = f"vise:Mailer.{_SEND}"
        wrapped.__vise_wrapped__ = True  # type: ignore[attr-defined]
        return wrapped

    def _record(self, client: Any, message: Any, status: str, error: str) -> None:
        """Store one message.

        The body is deliberately not recorded. Everything else about a message
        is metadata; the body of a password reset is an account.

        Args:
            client: The mailer.
            message: The message.
            status: completed, suppressed or failed.
            error: The message, when delivery failed.
        """
        if self.recorder is None:  # pragma: no cover - detached
            return

        self.recorder.mail(
            _recipients(message),
            subject=str(getattr(message, "subject", "") or ""),
            template=str(getattr(message, "template_name", "") or ""),
            status=status,
            mailer=_transport(client),
            error=error,
        )

    def detach(self) -> None:
        """Put the send method back."""
        if self.client is not None and self.original is not None:
            setattr(self.client, _SEND, self.original)
        self.client = None
        self.original = None
        super().detach()


def _client_class() -> type | None:
    """sillo's mail client class, if it can be imported.

    Returns:
        The class, or None.
    """
    try:
        from sillo.mail.client import MailClient
    except ImportError:
        return None
    return MailClient


def _configured_client(app: Any) -> Any:
    """The mailer an application set up, if it set one up.

    ``setup_mail`` puts it on ``app.state["mail_client"]``. A project that
    built a client without calling that keeps it wherever it likes, and the
    watcher will still record its sends — the class is wrapped, not the
    instance — but the panel does not appear, because nothing here can prove
    the application has mail rather than merely has the import available.

    Args:
        app: The application.

    Returns:
        The mailer, or None.
    """
    state = getattr(app, "state", None) or {}
    return state.get("mail_client") or state.get("mail")


def _recipients(message: Any) -> str:
    """The recipients of a message, joined.

    Args:
        message: The message.

    Returns:
        The addresses as one string.
    """
    to = getattr(message, "to", None) or getattr(message, "recipients", None) or ()
    if isinstance(to, str):
        return to
    return ", ".join(str(address) for address in to)


def _outcome(client: Any, result: Any) -> str:
    """What happened to a message.

    Suppressed mail is the case this panel is most useful for: a framework
    that discards messages outside production leaves a developer guessing
    whether the password reset was ever built. ``send_message`` reports it in
    the result's provider response, and that is read rather than inferred
    from configuration, so a per-message override is reflected correctly.

    Args:
        client: The mailer.
        result: The ``EmailResult``.

    Returns:
        ``suppressed``, ``failed`` or ``completed``.
    """
    response = getattr(result, "provider_response", None) or {}
    if isinstance(response, dict) and response.get("suppressed"):
        return "suppressed"

    if getattr(result, "success", True) is False:
        return "failed"

    return "completed"


def _transport(client: Any) -> str:
    """Name a mailer's transport.

    Args:
        client: The mailer, or None.

    Returns:
        A short lowercase name.
    """
    if client is None:
        return "mail"

    config = getattr(client, "config", None)
    if config is not None:
        host = getattr(config, "host", "") or ""
        if getattr(config, "suppress_send", False):
            return "suppressed"
        if host:
            return str(host)

    return type(client).__name__.removesuffix("Client").lower() or "mail"
