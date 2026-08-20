"""
The work Sillodraft does out of band.

Four jobs on three queues, which is what makes the Queues and Workers panels
live. They are deliberately unalike: one is quick, one is slow enough to be
caught mid-flight, one fails every time, and one fans out into the others.

``BuildExport`` failing on purpose is not padding. A queue dashboard that has
only ever seen jobs succeed has never shown anybody the thing they actually open
it for.

Each job sets ``_queue_name`` as well as ``queue``. Both exist on sillo 0.2.1
and only the first one routes: ``Dispatchable.dispatch`` reads ``_queue_name``,
while ``queue`` is the attribute ``Job`` documents. Setting only ``queue`` puts
every job on ``default`` however many queues you declared — which looks like a
working queue system right up until you notice one worker doing all the work.
"""

from __future__ import annotations

import asyncio
import logging
import random

from sillo.work.queue.job import Job

logger = logging.getLogger("app.jobs")


class ReindexDocument(Job):
    """Rebuild one document's search entry."""

    queue = "search"
    _queue_name = "search"

    def __init__(self, document_id: int = 0) -> None:
        """Build the job.

        Args:
            document_id: Which document to reindex.
        """
        self.document_id = document_id

    async def handle(self) -> None:
        """Do the work."""
        from app.models import Document

        document = await Document.filter(id=self.document_id).first()
        if document is None:
            logger.warning("reindex skipped: document %s is gone", self.document_id)
            return

        await asyncio.sleep(random.uniform(0.02, 0.12))
        logger.info("reindexed %s", document.title)


class SendDigest(Job):
    """Mail one workspace its digest."""

    queue = "mail"
    _queue_name = "mail"

    def __init__(self, workspace_id: int = 0) -> None:
        """Build the job.

        Args:
            workspace_id: Which workspace to summarise.
        """
        self.workspace_id = workspace_id

    async def handle(self) -> None:
        """Do the work."""
        from app.mailer import send_digest
        from app.models import Document

        count = await Document.filter(workspace_id=self.workspace_id).count()
        await send_digest(self.workspace_id, count)


class BuildExport(Job):
    """Assemble a workspace export.

    Fails, every time, on purpose. A queue panel that has only seen jobs
    succeed has never shown anybody a traceback, a failed count or a dead
    letter — which is most of what somebody opens it for.
    """

    queue = "exports"
    _queue_name = "exports"

    def __init__(self, workspace_id: int = 0) -> None:
        """Build the job.

        Args:
            workspace_id: Which workspace to export.
        """
        self.workspace_id = workspace_id

    async def handle(self) -> None:
        """Fail."""
        await asyncio.sleep(0.05)
        raise RuntimeError(
            f"export storage rejected workspace {self.workspace_id}: quota exceeded"
        )


class RollUpAnalytics(Job):
    """Count what is in the database, slowly."""

    queue = "default"
    _queue_name = "default"

    async def handle(self) -> None:
        """Do the work."""
        from app.models import Document, Revision

        documents = await Document.all().count()
        revisions = await Revision.all().count()

        # Slow on purpose, so something is visible in flight on the Queues
        # panel rather than every job being finished before it is drawn.
        await asyncio.sleep(random.uniform(0.4, 1.1))

        logger.info("rolled up %d documents and %d revisions", documents, revisions)
