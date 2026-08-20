"""
A small application, for looking at vise.

    cd example
    vise serve

It does just enough to make several panels do something: it logs, it reads and
writes a cache, it raises on one route, and it sends a suppressed message. What
it does *not* have is a database, a queue or a scheduler — so the Queries,
Queues, Workers and Schedules panels are absent, and ``vise panels`` says why.

That absence is the point. A demo that switched everything on would show a full
dashboard and hide the behaviour worth demonstrating.
"""

from __future__ import annotations

import asyncio
import logging

from sillo import SilloApp
from sillo.cache import config as cache_config
from sillo.cache.backends import MemoryCache
from sillo.mail.client import setup_mail
from sillo.mail.config import MailConfig
from sillo.mail.models import EmailMessage

app = SilloApp(title="Sillodraft")

cache_config.configure_cache(MemoryCache())

# Suppressed rather than connected: the Mail panel's most useful case is
# development mail that was never sent, listed rather than discarded.
mailer = setup_mail(app, MailConfig(suppress_send=True, default_from="noreply@sillodraft.test"))

logger = logging.getLogger("app.documents")


async def home(request, response):
    """The index."""
    logger.info("rendering the index")
    return response.json({"documents": 3})


async def show(request, response, id):
    """One document, read through the cache."""
    cache = cache_config.get_default_backend()

    key = f"document:{id}:render"
    cached = await cache.get(key)
    await cache.set(key, {"id": id}, ttl=60)

    # A little work, so the duration column has something in it.
    await asyncio.sleep(0.01)

    return response.json({"id": id, "cached": cached is not None})


async def invite(request, response):
    """Send a message, which the mailer suppresses and the panel lists."""
    await mailer.send_message(
        EmailMessage(
            to=["ada@sillodraft.test"],
            subject="You were added to Atlas",
            body="Welcome.",
        )
    )
    return response.json({"sent": True})


async def insights(request, response):
    """A route that fails, for the Exceptions panel."""
    logger.warning("insights upstream is unhealthy")
    raise ValueError("the upstream said no")


app.get("/", handler=home, name="web.home")
app.get("/api/v1/documents/{id}", handler=show, name="api.documents.show")
app.post("/api/v1/invites", handler=invite, name="api.invites.store")
app.get("/api/v1/insights", handler=insights, name="api.insights.index")
