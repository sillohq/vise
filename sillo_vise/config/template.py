"""
sillo_vise.config.template — the ``.vise`` that ``vise init`` writes.

Every key is present and every key is commented out, set to the default it
already has. A generated file full of live settings is a file that silently
pins today's defaults forever; a generated file full of commented ones is
documentation that happens to be editable.

The exception is ``[app] target``, which is written live when ``vise init``
managed to find the application, because that is the one value the project
cannot look up for itself.
"""

from __future__ import annotations

__all__ = ["TEMPLATE", "render_template"]

TEMPLATE = '''\
# .vise — the Sillo development server.
#
#   vise serve     run the application, with Foreman alongside it
#   vise doctor    report what is configured and what can be observed
#   vise panels    list the panels, and why any are missing
#
# Every setting below is shown at its default. Uncomment to change one.

[app]
{target}
# name = "My application"          # what the dashboard calls it

[server]
# host = "127.0.0.1"
# port = 8000
# reload = true
# watch = ["app", "config", "src"]
# workers = 1                      # more than one turns the recorder off
# root_path = ""                   # when something else proxies to this

[dashboard]
# enabled = true
# path = "/__sillo/foreman"
# access = "local"                 # local | token | open
# token = ""                       # required by access = "token"

[recorder]
# enabled = true
# buffer = 2000                    # events kept per kind
# window_minutes = 60
# slow_request_ms = 500
# slow_query_ms = 100
# redact = []                      # extra headers, on top of the built-in list
# redact_params = ["token", "secret", "password", "api_key"]
# redact_bindings = false          # replace SQL parameters with their types
# capture_bodies = false           # off: a body is where the other credential is
# max_body_bytes = 16384

[logs]
# style = "vise"                   # vise | plain | json
# level = "info"
# access = true
# banner = true
# show_query = true
# static = false                   # log the dashboard's own asset requests

[panels]
# disable = []                     # panel ids to leave out
# refresh_ms = 2000
# probe_seconds = 15
'''


def render_template(target: str | None = None) -> str:
    """Render the starter ``.vise``.

    Args:
        target: The application's import string, when one was found. None
            leaves the key commented, and vise falls back to the framework's
            own discovery at boot.

    Returns:
        The file's contents.
    """
    line = (
        f'target = "{target}"'
        if target
        else '# target = "app.main:app"        # default: sillo finds it'
    )
    return TEMPLATE.format(target=line)
