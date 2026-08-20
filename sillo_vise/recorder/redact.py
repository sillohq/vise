"""
sillo_vise.recorder.redact — remove credentials before anything is stored.

This runs on capture, never on read. The distinction is the whole point: a
watcher that stores a ``Cookie`` header and hides it in the interface is a
credential store with a filter on top, and every filter is one bug away from
being no filter. Sillo 0.1.0 had to fix a debug page that returned one
request's headers to another client, and a dashboard holding every request the
application has served is that surface multiplied by the buffer size.

So the store has no un-redacted path at all. :class:`Redactor` is applied
inside :meth:`~sillo_vise.recorder.recorder.Recorder.emit`, and the event that
reaches the ring already has ``***`` where the token was.

Three things are covered:

* **Headers**, by name, against the configured set.
* **Query-string and form values**, by key, so ``?api_key=live_abc`` does not
  survive in a path.
* **URL userinfo**, because ``https://user:pass@host/`` is a credential in a
  field nobody thinks of as one.

SQL bind parameters are covered too, behind ``recorder.redact_bindings``. That
one is off by default because bindings are usually the most useful thing on the
Queries panel and are usually not secret — but a project that puts a token in a
``WHERE`` clause can turn it on.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

__all__ = ["PLACEHOLDER", "Redactor"]

#: What replaces a redacted value. Deliberately not the empty string: a reader
#: needs to know a header was sent, and only that its value is not shown.
PLACEHOLDER = "***"

#: ``scheme://user:password@host`` — the credential in a URL.
_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][\w+.-]*://)(?P<userinfo>[^/@\s]+)@")

#: A bearer token, a basic credential or an API key sitting loose in free text,
#: which is where they end up when someone logs a whole request.
_LOOSE_SECRET = re.compile(
    r"\b(?:bearer|basic|token|secret|api[-_]?key|password)\b[=:\s]+\S+",
    re.IGNORECASE,
)


class Redactor:
    """Removes credentials from events on their way into the store.

    Built once, from the recorder's configuration, and held by the recorder.
    It is deliberately not a free function: the header and parameter sets are
    resolved once at boot rather than recomputed per request, and the hot path
    for an event with nothing to redact is a handful of set lookups.

    Attributes:
        headers: Header names whose values are replaced, lowercased.
        params: Query-string and form keys whose values are replaced.
        bindings: Whether SQL bind parameters are replaced by their types.
    """

    __slots__ = ("headers", "params", "bindings")

    def __init__(
        self,
        headers: Iterable[str] = (),
        params: Iterable[str] = (),
        *,
        bindings: bool = False,
    ) -> None:
        """Build a redactor.

        Args:
            headers: Header names to redact.
            params: Query-string and form keys to redact.
            bindings: Replace SQL bind parameters with their types.
        """
        self.headers = frozenset(name.lower() for name in headers)
        self.params = frozenset(name.lower() for name in params)
        self.bindings = bindings

    def header_pairs(
        self, pairs: Iterable[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """Redact a header list, keeping the order it was sent in.

        Order matters on the Requests panel — the headers a client sent, in the
        order it sent them, is evidence about the client — so this returns a
        list of pairs rather than a dictionary, and duplicated names survive.

        Args:
            pairs: Header name and value pairs.

        Returns:
            The same pairs, with configured values replaced.
        """
        return [
            (name, PLACEHOLDER if name.lower() in self.headers else value)
            for name, value in pairs
        ]

    def headers_of(self, headers: Mapping[str, str]) -> dict[str, str]:
        """Redact a header mapping.

        Args:
            headers: Header names to values.

        Returns:
            A new mapping with configured values replaced.
        """
        return {
            name: PLACEHOLDER if name.lower() in self.headers else value
            for name, value in headers.items()
        }

    def query(self, query: str) -> str:
        """Redact the values of configured keys in a query string.

        Parsing and re-encoding would normalise a query string, and the exact
        bytes a client sent are part of what someone comes to this panel to
        see. So this rewrites in place, leaving separators, ordering and
        encoding as they were.

        Args:
            query: A query string, without the leading ``?``.

        Returns:
            The query string, with configured values replaced.
        """
        if not query or not self.params:
            return query

        parts = []
        for part in query.split("&"):
            key, sep, _ = part.partition("=")
            if sep and key.lower() in self.params:
                parts.append(f"{key}={PLACEHOLDER}")
            else:
                parts.append(part)
        return "&".join(parts)

    def url(self, url: str) -> str:
        """Redact userinfo and query values in a URL.

        Args:
            url: An absolute or relative URL.

        Returns:
            The URL with credentials replaced.
        """
        if not url:
            return url

        url = _USERINFO.sub(rf"\g<scheme>{PLACEHOLDER}@", url)

        head, sep, query = url.partition("?")
        return f"{head}{sep}{self.query(query)}" if sep else head

    def params_of(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Redact a mapping of form or query parameters.

        Args:
            params: Parameter names to values.

        Returns:
            A new mapping with configured values replaced.
        """
        return {
            key: PLACEHOLDER if key.lower() in self.params else value
            for key, value in params.items()
        }

    def sql_params(self, params: Any) -> Any:
        """Redact SQL bind parameters, when that is turned on.

        A binding is replaced by its type rather than by ``***``, because the
        shape of what was bound is what makes a query log readable and the
        value is what makes it dangerous.

        Args:
            params: Whatever the driver was handed — a sequence, a mapping or
                a single value.

        Returns:
            The parameters, or a description of them.
        """
        if not self.bindings or params is None:
            return params

        if isinstance(params, Mapping):
            return {key: type(value).__name__ for key, value in params.items()}

        if isinstance(params, (list, tuple)):
            return [type(value).__name__ for value in params]

        return type(params).__name__

    def text(self, text: str) -> str:
        """Redact credentials appearing loose in free text.

        For log lines and exception messages, where a whole request or a whole
        header block sometimes ends up interpolated into a string. This cannot
        be complete — free text is free — and it is not the mechanism the
        header and parameter paths rely on. It is the net under them.

        Args:
            text: The message.

        Returns:
            The message, with recognisable credentials replaced.
        """
        if not text:
            return text
        return _LOOSE_SECRET.sub(
            lambda match: f"{match.group(0).split('=')[0].split(':')[0].strip()} {PLACEHOLDER}",
            _USERINFO.sub(rf"\g<scheme>{PLACEHOLDER}@", text),
        )

    def body(self, body: bytes | str, limit: int) -> str:
        """Truncate and redact a captured body.

        Args:
            body: The bytes or text captured.
            limit: Maximum characters to keep.

        Returns:
            The body as text, truncated and with recognisable credentials
            replaced.
        """
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")

        if len(body) > limit:
            body = f"{body[:limit]}… ({len(body)} bytes)"

        return self.text(body)

    @classmethod
    def from_config(cls, recorder: Any) -> Redactor:
        """Build a redactor from a :class:`~sillo_vise.config.RecorderConfig`.

        Args:
            recorder: The recorder section of the configuration.

        Returns:
            The redactor those settings describe.
        """
        return cls(
            recorder.redacted_headers,
            recorder.redact_params,
            bindings=recorder.redact_bindings,
        )

    def __repr__(self) -> str:
        """A description that does not list the names being protected.

        Returns:
            The counts, not the contents.
        """
        return (
            f"Redactor(headers={len(self.headers)}, params={len(self.params)}, "
            f"bindings={self.bindings})"
        )

