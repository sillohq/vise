"""
Mail, suppressed.

The Mail panel's most useful case is development mail that was never sent:
a framework that quietly discards it leaves you guessing whether the digest was
ever built. Suppressed messages are listed here rather than discarded.
"""

from __future__ import annotations

import logging

from sillo.mail.client import MailClient
from sillo.mail.models import EmailMessage

logger = logging.getLogger("app.mail")

#: Set by ``app.main`` once ``setup_mail`` has run.
mailer: MailClient | None = None


def use(client: MailClient) -> None:
    """Remember the application's mailer.

    Args:
        client: The mailer ``setup_mail`` returned.
    """
    global mailer
    mailer = client


async def send_digest(workspace_id: int, documents: int) -> None:
    """Send one workspace its digest.

    Args:
        workspace_id: Which workspace.
        documents: How many documents it holds.
    """
    if mailer is None:  # pragma: no cover - set at startup
        return

    await mailer.send_message(
        EmailMessage(
            to=[f"team-{workspace_id}@sillodraft.test"],
            subject=f"{documents} documents in your workspace",
            body=f"Workspace {workspace_id} now holds {documents} documents.",
        )
    )


async def send_invite(address: str) -> None:
    """Invite somebody to a workspace.

    Args:
        address: Who to invite.
    """
    if mailer is None:  # pragma: no cover - set at startup
        return

    await mailer.send_message(
        EmailMessage(
            to=[address],
            subject="You were added to Sillodraft",
            body="Open the workspace to get started.",
        )
    )
