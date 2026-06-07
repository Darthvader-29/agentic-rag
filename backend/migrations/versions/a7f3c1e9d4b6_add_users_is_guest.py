"""add users.is_guest (Phase 6 guest accounts)

Revision ID: a7f3c1e9d4b6
Revises: e3a1f7b9c2d8
Create Date: 2026-06-02

Anonymous guest users start with is_guest=True; POST /api/auth/upgrade flips it to False in place,
preserving the user_id, sessions, and BYOK keys. server_default false backfills existing rows as
registered accounts (NOT NULL is safe on a non-empty users table).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7f3c1e9d4b6"
down_revision: str | Sequence[str] | None = "e3a1f7b9c2d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the is_guest boolean column to users."""
    op.add_column(
        "users",
        sa.Column(
            "is_guest",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop the is_guest column."""
    op.drop_column("users", "is_guest")
