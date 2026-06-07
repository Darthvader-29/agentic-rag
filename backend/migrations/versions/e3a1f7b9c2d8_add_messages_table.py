"""add messages table (Phase 6 conversation memory)

Revision ID: e3a1f7b9c2d8
Revises: fc66257371a4
Create Date: 2026-06-02

Stores verbatim conversation turns so the supervisor can resolve follow-ups against the last-N
history. ON DELETE CASCADE off sessions mirrors documents — deleting a session removes its turns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a1f7b9c2d8"
down_revision: str | Sequence[str] | None = "fc66257371a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the messages table."""
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])


def downgrade() -> None:
    """Drop the messages table."""
    op.drop_index("ix_messages_created_at", table_name="messages")
    op.drop_index("ix_messages_session_id", table_name="messages")
    op.drop_table("messages")
