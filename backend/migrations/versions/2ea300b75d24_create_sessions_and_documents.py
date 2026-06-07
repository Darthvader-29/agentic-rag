"""create sessions and documents

Revision ID: 2ea300b75d24
Revises:
Create Date: 2026-05-31

NOTE: DocumentStatus is stored as VARCHAR(16) with a CHECK constraint (not a native
      PostgreSQL ENUM type) to avoid asyncpg OID caching issues.  Adding a new member
      therefore only requires a migration that widens the CHECK constraint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2ea300b75d24"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_VALUES = ("pending", "processing", "ready", "failed")


def upgrade() -> None:
    """Create sessions and documents tables."""
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("s3_key", sa.String(512), nullable=False, unique=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            f"status IN {_STATUS_VALUES!r}",
            name="ck_documents_status",
        ),
    )
    op.create_index("ix_documents_session_id", "documents", ["session_id"])
    op.create_index("ix_documents_status", "documents", ["status"])


def downgrade() -> None:
    """Drop documents and sessions tables."""
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_session_id", table_name="documents")
    op.drop_table("documents")
    op.drop_table("sessions")
