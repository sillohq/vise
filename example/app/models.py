"""
The data Sillodraft keeps.

Three Record models over SQLite, which is what makes the Queries panel live
without asking anybody to run a database. Everything the panel is for shows up
here anyway: statement text, bindings, durations, row counts — and, on the
search route, a deliberate N+1 that the panel names.
"""

from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class Workspace(Model):
    """A place documents live."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=120)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:  # noqa: D106
        table = "workspaces"

    def __str__(self) -> str:
        """The workspace's name."""
        return self.name


class Document(Model):
    """One document, in one workspace."""

    id = fields.IntField(primary_key=True)
    title = fields.CharField(max_length=200)
    status = fields.CharField(max_length=20, default="draft")
    body = fields.TextField(default="")
    workspace = fields.ForeignKeyField("models.Workspace", related_name="documents")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:  # noqa: D106
        table = "documents"

    def __str__(self) -> str:
        """The document's title."""
        return self.title


class Revision(Model):
    """One saved version of a document."""

    id = fields.IntField(primary_key=True)
    document = fields.ForeignKeyField("models.Document", related_name="revisions")
    summary = fields.CharField(max_length=200)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:  # noqa: D106
        table = "revisions"

    def __str__(self) -> str:
        """The revision's summary."""
        return self.summary
