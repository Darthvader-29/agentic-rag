"""add session_memory table (Phase 7 per-session markdown memory)

Revision ID: b1c2d3e4f5a6
Revises: a7f3c1e9d4b6
Create Date: 2026-06-03

A bounded per-session running markdown summary (one row per session; session_id is the PK).
ON DELETE CASCADE off sessions mirrors messages/documents — deleting a session removes its memory.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a7f3c1e9d4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the session_memory table."""
    op.create_table(
        "session_memory",
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop the session_memory table."""
    op.drop_table("session_memory")
