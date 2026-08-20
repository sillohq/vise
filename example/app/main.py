"""
Sillodraft — the example application, with everything switched on.

    cd example
    vise serve

Then open http://127.0.0.1:8000/__sillo/foreman/ and, in another terminal,
``python traffic.py`` to give it something to show.

This wires up every subsystem vise can watch, so all fourteen panels are live:

======================  ==============================================
Panel                   What makes it live here
======================  ==============================================
Overview, Requests      the routes below
Queries                 Record over SQLite, with a deliberate N+1
Cache                   a memory cache on the document render path
Outgoing                an HTTP client calling this application's own
                        health endpoint, so the demo needs no internet
Queues, Workers         four jobs on three queues, one that always
                        fails, and an in-process worker pool
Schedules               two scheduled jobs, one every 30s
Exceptions              one route that raises, one job that raises
Logs                    every route logs
Real-time               a websocket route and registered events
Mail                    a suppressed mailer
Routes, Config          the application itself
======================  ==============================================

Everything runs in one process against SQLite and an in-memory queue. There is
nothing to install and nothing to start: the point is to be able to see the
dashboard doing its job, not to stand up infrastructure first.
"""

from __future__ import annotations

import asyncio
import logging
import random

from sillo import SilloApp
from sillo.cache import config as cache_config
from sillo.cache.backends import MemoryCache
from sillo.events.emitter import EventEmitter
from sillo.http.client import HTTPClient
from sillo.mail.client import setup_mail
from sillo.mail.config import MailConfig
from sillo.record.config import DatabaseConfig
from sillo.record.manager import setup_record
from sillo.work import setup_work
from sillo.work.queue.connection import ConnectionManager
from sillo.work.queue.failed import MemoryFailedRepository
from sillo.work.queue.payloads import PayloadSerializer
from sillo.work.queue.workers import QueueWorker, WorkerOptions, WorkerPool

from app import mailer as mail
from app.jobs import BuildExport, ReindexDocument, RollUpAnalytics, SendDigest
from app.models import Document, Revision, Workspace

logger = logging.getLogger("app.documents")

app = SilloApp(title="Sillodraft")


# -- data ---------------------------------------------------------------

# SQLite in a file beside the application, so restarting keeps what was seeded
# and the Queries panel has history the first time it is opened.
database = setup_record(
    app,
    DatabaseConfig(url="sqlite://sillodraft.db", generate_schemas=True),
    model_modules=["app.models"],
)


# -- cache --------------------------------------------------------------

cache_config.configure_cache(MemoryCache())


# -- mail ---------------------------------------------------------------

mail.use(
    setup_mail(
        app,
        MailConfig(suppress_send=True, default_from="noreply@sillodraft.test"),
    )
)


# -- queues, workers and schedules --------------------------------------

work = setup_work(app)
connection = work["connection"]
scheduler = work["scheduler"]

# Jobs need to know where to put themselves. `setup_work` builds the connection
# but does not introduce the two, so the application does.
for job in (ReindexDocument, SendDigest, BuildExport, RollUpAnalytics):
    job._connection = connection

# A worker asks its manager for `connection(queue_name)`, so every queue name
# has to be registered — not just "default". Handing the worker a bare
# connection instead crashes its loop with an AttributeError on the first tick.
QUEUES = ("default", "search", "mail", "exports")

connections = ConnectionManager()
for name in QUEUES:
    connections.add(name, connection)

pool = WorkerPool()
for queues in (["default", "search"], ["mail"], ["exports"]):
    pool.add(
        QueueWorker(
            connections,
            PayloadSerializer(),
            MemoryFailedRepository(),
            options=WorkerOptions(concurrency=2, sleep=0.2, queues=queues),
        )
    )

# Put the pool where the Workers panel looks for it.
app.state["worker_pool"] = pool

app.on_startup(pool.start)
app.on_shutdown(pool.shutdown)


@scheduler.every(30, name="analytics.rollup")
async def rollup() -> None:
    """Count what is in the database, every half minute."""
    await RollUpAnalytics.dispatch()


@scheduler.cron("0 6 * * 1-5", name="notifications.digest")
async def digest() -> None:
    """Mail every workspace its digest, on weekday mornings."""
    for workspace in await Workspace.all():
        await SendDigest.dispatch(workspace.id)


# -- events -------------------------------------------------------------
#
# `setup_work` puts a queue EventDispatcher at state["events"], which is a
# different thing entirely, so the application's own emitter goes under its own
# key. Vise duck-types rather than reading a fixed key, and finds this one.

events = EventEmitter()
app.state["emitter"] = events
app.on_startup(events.start)
app.on_shutdown(events.stop)


@events.on("document.published")
async def on_published(document_id: int) -> None:
    """React to a document being published."""
    logger.info("document %s published", document_id)
    await ReindexDocument.dispatch(document_id)


@events.on("workspace.created")
async def on_workspace(workspace_id: int) -> None:
    """React to a workspace appearing."""
    logger.info("workspace %s created", workspace_id)


# -- seeding ------------------------------------------------------------


async def seed() -> None:
    """Put something in the database, once.

    Runs at startup so the panels have data the first time somebody looks,
    rather than showing four empty tables and a chart of nothing.
    """
    if await Workspace.all().count():
        return

    logger.info("seeding")

    for name in ("Platform", "Design", "Research"):
        workspace = await Workspace.create(name=name)

        for index in range(6):
            document = await Document.create(
                title=f"{name} note {index + 1}",
                status=random.choice(["draft", "in_review", "published"]),
                body="The quick brown fox." * 20,
                workspace=workspace,
            )
            await Revision.create(document=document, summary="first draft")


app.on_startup(seed)


# -- routes -------------------------------------------------------------


async def home(request, response):
    """The index."""
    logger.info("rendering the index")

    workspaces = await Workspace.all().count()
    documents = await Document.all().count()

    return response.json({"workspaces": workspaces, "documents": documents})


async def health(request, response):
    """What the outgoing client calls, so the demo needs no internet."""
    return response.json({"status": "ok"})


async def index_documents(request, response):
    """Every document, newest first."""
    documents = await Document.all().order_by("-updated_at").limit(20)

    return response.json(
        {
            "documents": [
                {"id": document.id, "title": document.title, "status": document.status}
                for document in documents
            ]
        }
    )


async def show_document(request, response, id):
    """One document, read through the cache."""
    cache = cache_config.get_default_backend()
    key = f"document:{id}:render"

    cached = await cache.get(key)
    document = await Document.filter(id=int(id)).first()

    if document is None:
        return response.json({"detail": "Not found"}, status_code=404)

    rendered = {"id": document.id, "title": document.title, "body": document.body[:80]}
    await cache.set(key, rendered, ttl=60)

    return response.json({**rendered, "cached": cached is not None})


async def create_document(request, response):
    """Create a document, and let the world know."""
    payload = await request.json
    workspace = await Workspace.all().first()

    document = await Document.create(
        title=str(payload.get("title", "Untitled")),
        body=str(payload.get("body", "")),
        status="draft",
        workspace=workspace,
    )

    events.emit("document.published", document.id)

    return response.json({"id": document.id}, status_code=201)


async def search(request, response):
    """Search, badly — and on purpose.

    Every document's workspace is fetched in its own query rather than joined,
    which is the shape of an N+1: one query for the list and one per row. The
    Queries panel names it, counts it and points at the statement.
    """
    documents = await Document.filter(status="published").limit(12)

    found = []
    for document in documents:
        workspace = await Workspace.filter(id=document.workspace_id).first()
        found.append({"title": document.title, "workspace": workspace.name})

    return response.json({"results": found})


async def export_workspace(request, response, id):
    """Queue an export, which always fails."""
    job_id = await BuildExport.dispatch(int(id))
    return response.json({"queued": job_id}, status_code=202)


async def reindex(request, response):
    """Queue a reindex for every document."""
    queued = []
    for document in await Document.all().limit(8):
        queued.append(await ReindexDocument.dispatch(document.id))

    return response.json({"queued": len(queued)}, status_code=202)


async def invite(request, response):
    """Send a message now, and queue a digest to be sent later.

    Two paths on purpose: the direct send shows on the Mail panel immediately,
    and the queued one goes through the mail queue so a second worker has
    something to do.
    """
    await mail.send_invite("ada@sillodraft.test")

    workspace = await Workspace.all().first()
    queued = await SendDigest.dispatch(workspace.id) if workspace else None

    return response.json({"sent": True, "queued": queued})


async def upstream(request, response):
    """Call this application's own health endpoint, through the HTTP client.

    Pointed at itself so the Outgoing panel has something real to show without
    the demo needing an internet connection, or a second service, or a call that
    fails on an aeroplane.

    The base URL is passed rather than left empty because sillo 0.2.1 renders an
    empty one as ``None`` on its way to httpx — ``base_url=self._config.base_url
    or None`` — and httpx refuses that. An ``HTTPClient()`` with no base URL
    cannot currently be constructed at all.
    """
    async with HTTPClient(_own_url(request)) as client:
        result = await client.get("/api/v1/health")

    return response.json({"upstream": result})


async def insights(request, response):
    """A route that fails, for the Exceptions panel."""
    logger.warning("insights upstream is unhealthy")
    raise ValueError("the upstream said no")


async def slow(request, response):
    """A route that takes long enough to be marked slow."""
    await asyncio.sleep(random.uniform(0.2, 0.6))
    return response.json({"slept": True})


def _own_url(request) -> str:
    """This server's own base URL, from the request that arrived.

    Args:
        request: The incoming request.

    Returns:
        A base URL the HTTP client can call back into.
    """
    host = request.headers.get("host", "127.0.0.1:8000")
    return f"http://{host}"


app.get("/", handler=home, name="web.home")
app.get("/api/v1/health", handler=health, name="api.health")
app.get("/api/v1/documents", handler=index_documents, name="api.documents.index")
app.post("/api/v1/documents", handler=create_document, name="api.documents.store")
app.get("/api/v1/documents/{id}", handler=show_document, name="api.documents.show")
app.get("/api/v1/search", handler=search, name="api.documents.search")
app.post("/api/v1/workspaces/{id}/export", handler=export_workspace, name="api.exports.store")
app.post("/api/v1/reindex", handler=reindex, name="api.reindex.store")
app.post("/api/v1/invites", handler=invite, name="api.invites.store")
app.get("/api/v1/upstream", handler=upstream, name="api.upstream.index")
app.get("/api/v1/insights", handler=insights, name="api.insights.index")
app.get("/api/v1/slow", handler=slow, name="api.slow.index")


# -- websockets ---------------------------------------------------------


async def presence(websocket, path=None):
    """Who is looking at a document.

    Kept trivial on purpose: the Real-time panel counts connections, channels
    and bytes, and none of that needs a real collaboration protocol underneath
    to be worth looking at.
    """
    await websocket.accept()

    try:
        await websocket.send_json({"event": "joined", "members": 1})

        while True:
            message = await websocket.receive_text()
            await websocket.send_json({"event": "echo", "message": message})
    except Exception:
        # The client went away, which is how every websocket ends.
        pass


app.ws_route("/ws/documents/{id}", handler=presence)
