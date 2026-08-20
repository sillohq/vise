"""
sillo_vise.dashboard.assets — serve the built interface out of the package.

The dashboard is a Vite build committed into ``sillo_vise/dashboard/static``, so
a wheel carries it and ``pip install`` needs no node. This serves it, with three
decisions worth writing down.

**Paths are resolved and then checked.** A static server is the classic place to
put a traversal bug, and the check is that the resolved path is *inside* the
static directory — not that the request contained no ``..``, which is a filter
on the symptom.

**Hashed assets are immutable, the index is not.** Vite names its output with a
content hash, so ``assets/index-a91f.js`` can be cached for a year and the index
must never be, or a reload after a rebuild serves the old bundle forever.

**A missing build says so.** Someone working on vise from a git checkout has no
``static`` directory until they have run the build, and a blank page is a bad way
to find that out.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

__all__ = ["Assets", "MISSING_BUILD"]

#: Where the build output lives inside the package.
STATIC = Path(__file__).parent / "static"

#: The page served when the build is not there. Deliberately plain HTML with no
#: assets of its own — the whole problem being reported is that assets are
#: missing.
MISSING_BUILD = """<!doctype html>
<meta charset="utf-8">
<title>vise — interface not built</title>
<style>
  body { background:#050505; color:#f7f7f5; font:15px/1.6 ui-sans-serif, system-ui, sans-serif;
         margin:0; display:grid; place-items:center; min-height:100vh; }
  main { max-width:38rem; padding:2rem; }
  h1 { font-size:1.4rem; letter-spacing:-0.03em; margin:0 0 1rem; }
  p { color:#8b8b91; margin:0 0 1rem; }
  code { font:13px ui-monospace, SFMono-Regular, Menlo, monospace; color:#fc0345; }
</style>
<main>
  <h1>The dashboard interface has not been built.</h1>
  <p>The server is running and collecting. What is missing is the compiled
     front end, which is committed into the package for a release and has to be
     built once in a source checkout.</p>
  <p><code>cd ui &amp;&amp; bun install &amp;&amp; bun run build</code></p>
  <p>The JSON API is unaffected — <code>{path}/api/meta</code> answers now.</p>
</main>
"""

#: Cache headers. A hashed filename can be cached forever; the index cannot be
#: cached at all, or a rebuild is invisible until somebody clears their browser.
IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-store, must-revalidate"


class Assets:
    """Serves the built interface.

    Attributes:
        root: The directory holding the build.
    """

    __slots__ = ("root",)

    def __init__(self, root: Path | None = None) -> None:
        """Build an asset server.

        Args:
            root: The directory to serve. Defaults to the packaged one.
        """
        self.root = (root or STATIC).resolve()

    @property
    def built(self) -> bool:
        """Whether there is a build to serve.

        Returns:
            True when the index exists.
        """
        return (self.root / "index.html").is_file()

    def index(self, dashboard_path: str) -> tuple[bytes, str, str]:
        """The page a browser gets when it opens the dashboard.

        Args:
            dashboard_path: Where the dashboard is mounted, so the fallback
                page can point at an endpoint that works.

        Returns:
            The body, its content type and its cache header.
        """
        if not self.built:
            body = MISSING_BUILD.replace("{path}", dashboard_path).encode()
            return body, "text/html; charset=utf-8", NO_CACHE

        return (
            (self.root / "index.html").read_bytes(),
            "text/html; charset=utf-8",
            NO_CACHE,
        )

    def find(self, relative: str) -> tuple[bytes, str, str] | None:
        """One asset by its path under the mount.

        Args:
            relative: The path after the dashboard's prefix.

        Returns:
            The body, its content type and its cache header, or None when there
            is no such asset.
        """
        target = (self.root / relative.lstrip("/")).resolve()

        # Resolved-then-contained, rather than looking for `..` in the request.
        # A filter on the symptom misses encodings, symlinks and the next trick
        # somebody thinks of; containment is the property that actually has to
        # hold.
        if not target.is_relative_to(self.root) or not target.is_file():
            return None

        kind, _ = mimetypes.guess_type(target.name)
        cache = IMMUTABLE if _is_hashed(target.name) else NO_CACHE

        return target.read_bytes(), kind or "application/octet-stream", cache


def _is_hashed(name: str) -> bool:
    """Whether a filename carries a content hash.

    Vite writes ``index-a91f3c2d.js``. A name shaped like that cannot change
    contents without changing name, so it is safe to cache forever.

    Args:
        name: The filename.

    Returns:
        True when the name looks content-addressed.
    """
    stem = name.rsplit(".", 1)[0]
    _, separator, suffix = stem.rpartition("-")
    return bool(separator) and len(suffix) >= 8 and all(
        character in "0123456789abcdefABCDEF_" for character in suffix
    )
