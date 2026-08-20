"""
sillo_vise.logs.banner — what ``vise serve`` prints before the first request.

uvicorn's startup is five INFO lines saying what it is doing. A person running
a development server wants two things from that moment: the URLs to click, and
confirmation that the thing they just changed is switched on. So the banner
answers those and stops::

      ▲ vise 0.1.0                          sillo 0.2.1 · python 3.12.13

      ➜  Local      http://127.0.0.1:8000
      ➜  Foreman    http://127.0.0.1:8000/__sillo/foreman
      ➜  App        app.main:app
      ➜  Panels     11 live · queries, cache and 1 more waiting

      reload on · recorder on · .vise

The last line is the one that saves a support conversation. "Why is the Queues
panel missing" is answered before it is asked, and "why is nothing being
recorded" is visible rather than mysterious.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import IO

from sillo.console.style import Palette

from .theme import ACCENT, ARROW, DIM, LABEL, TITLE

__all__ = ["Banner"]

#: Width the labels are padded to, so the URLs line up under each other.
_LABEL_WIDTH = 10


class Banner:
    """Prints the startup block.

    Attributes:
        stream: Where it goes.
        palette: Decides whether it carries colour.
    """

    __slots__ = ("stream", "palette")

    def __init__(
        self, stream: IO[str] | None = None, palette: Palette | None = None
    ) -> None:
        """Build a banner.

        Args:
            stream: Where it goes. Defaults to stdout.
            palette: Colour decision for that stream.
        """
        self.stream = stream or sys.stdout
        self.palette = palette or Palette(self.stream)

    def render(
        self,
        *,
        version: str,
        framework: str,
        python: str,
        links: Sequence[tuple[str, str]],
        notes: Sequence[str] = (),
    ) -> str:
        """Build the block.

        Args:
            version: The vise version.
            framework: The framework version.
            python: The Python version.
            links: Label and value pairs, one per line.
            notes: Short facts for the footer line.

        Returns:
            The block, without a trailing newline.
        """
        paint = self.palette.render

        head = f"  {paint('▲', ACCENT)} {paint(f'vise {version}', TITLE)}"
        right = paint(f"sillo {framework} · python {python}", DIM)
        # Padded to a fixed column rather than to the terminal's width: a
        # banner that reflows when the window is resized looks broken in a
        # scrollback that was captured at another size.
        head = f"{head}{' ' * max(2, 44 - len('  ▲ vise ') - len(version))}{right}"

        rows = [
            f"  {paint('➜', ARROW)}  {paint(label.ljust(_LABEL_WIDTH), LABEL)}{value}"
            for label, value in links
        ]

        block = ["", head, "", *rows]

        if notes:
            block += ["", "  " + paint(" · ".join(notes), DIM)]

        return "\n".join(block) + "\n"

    def write(self, **fields: object) -> None:
        """Print the block.

        Args:
            **fields: Passed to :meth:`render`.
        """
        self.stream.write(self.render(**fields))  # type: ignore[arg-type]
        self.stream.flush()

    def stopped(self, reason: str = "") -> None:
        """Print the line that closes a run.

        A server that exits silently leaves a reader wondering whether it
        crashed. One line, in the same voice as the banner.

        Args:
            reason: Why it stopped, when there is something to say.
        """
        paint = self.palette.render
        tail = f" — {reason}" if reason else ""
        self.stream.write(
            f"\n  {paint('▲', ACCENT)} {paint('vise stopped' + tail, DIM)}\n"
        )
        self.stream.flush()
