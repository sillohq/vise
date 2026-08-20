"""
sillo_vise.__main__ — the ``vise`` entry point.

Installed as a console script, so ``vise`` is on the path after
``pip install sillo-vise``. ``python -m sillo_vise`` runs the same thing.

Nothing is imported here beyond the console itself. ``vise --help`` should not
pay for uvicorn, and the commands that need the server import it when they run.
"""

from __future__ import annotations

from .cli import build_console

__all__ = ["main"]


def main(argv: list[str] | None = None) -> None:
    """Run the ``vise`` command.

    Args:
        argv: Tokens after the program name. Defaults to ``sys.argv[1:]``.

    Raises:
        SystemExit: Always, with the command's exit code.
    """
    raise SystemExit(build_console().run(argv))


if __name__ == "__main__":
    main()
