"""
sillo_vise.watchers.mail — everything sent or suppressed, and what the recipient got.

The panel's most useful case is the one that looks least like monitoring:
development mail that was *suppressed*. A framework that quietly discards
messages outside production leaves a developer guessing whether the password
reset was ever built; listing them, with the rendered body, answers it.

sillo's mail client is the hook. ``send`` is wrapped at class level so every
mailer an application constructs is covered, and the recorded event carries the
recipients, the subject, the template and the outcome — not the body. The body
is where a password reset link lives, and a dashboard that holds one is a
dashboard that hands out account access.
"""

from __future__ import annotations

from typing import Any, Callable

from ..recorder import Recorder
from .base import Availability, Watcher, available, unavailable

__all__ = ["MailWatcher"]

#: The client method that delivers a message.
_SEND = "send"


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

        state = getattr(app, "state", None) or {}
        if "mail" not in state and not _configured():
            return unavailable("no mailer configured")

        return available(_transport(state.get("mail")))

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
                self._record(client, message, "failed", f"{type(error).__name__}: {error}")
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
            status: completed, queued, suppressed or failed.
            error: The message, when delivery failed.
        """
        if self.recorder is None:  # pragma: no cover - detached
            return

        self.recorder.mail(
            _recipients(message),
            subject=str(getattr(message, "subject", "") or ""),
            template=str(getattr(message, "template", "") or ""),
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
        from sillo.mail.client import Mailer
    except ImportError:
        try:
            from sillo.mail import Mailer  # type: ignore[attr-defined]
        except ImportError:
            return None
    return Mailer


def _configured() -> bool:
    """Whether mail settings have been provided.

    Returns:
        True when sillo's mail configuration names a transport.
    """
    try:
        from sillo.mail import config
    except ImportError:
        return False

    settings = getattr(config, "_SETTINGS", None) or getattr(config, "_DEFAULT", None)
    return settings is not None


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

    Args:
        client: The mailer.
        result: Whatever ``send`` returned.

    Returns:
        ``suppressed`` when the mailer is not really sending, ``queued`` when
        it handed the message to the queue, else ``completed``.
    """
    if getattr(client, "suppress", False) or "suppress" in _transport(client):
        return "suppressed"
    if getattr(result, "queued", False):
        return "queued"
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

    transport = getattr(client, "transport", None) or getattr(client, "driver", None)
    if transport is not None:
        return str(getattr(transport, "name", None) or type(transport).__name__).lower()

    return type(client).__name__.lower()
