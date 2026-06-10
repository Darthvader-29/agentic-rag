"""make sessions.user_id NOT NULL (tenant-isolation hardening)

Revision ID: d7e8f9a0b1c2
Revises: c2d3e4f5a6b7
Create Date: 2026-06-07

Phase 3 added ``sessions.user_id`` as nullable "for online-migration safety". That left a
tenant-isolation gap: app code treated a NULL owner as accessible/claimable by any authenticated
user. The code now refuses NULL-owner sessions; this migration enforces the invariant at the schema
level so the state can never recur.

Backfill is non-destructive: any legacy NULL-owner rows are adopted by a reserved *system* user
(login is impossible — the password hash is a placeholder), turning orphaned pre-auth sessions inert
rather than deleting them, before the column is set NOT NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e8f9a0b1c2"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed, well-known id for the reserved owner of orphaned pre-auth sessions.
SYSTEM_OWNER_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    """Backfill NULL owners to a reserved system user, then enforce NOT NULL."""
    # 1. Reserved system owner. ON CONFLICT DO NOTHING makes re-runs idempotent and tolerates a
    #    pre-existing row (by id, email, or username). Login is impossible (placeholder hash).
    op.execute(
        sa.text(
            """
            INSERT INTO users (id, email, username, hashed_password, is_guest, created_at, updated_at)
            VALUES (CAST(:id AS uuid), :email, :username, :pw, true, now(), now())
            ON CONFLICT DO NOTHING
            """
        ).bindparams(
            id=SYSTEM_OWNER_ID,
            email="system+orphan@internal.invalid",
            username="system_orphan_owner",
            pw="!disabled-orphan-owner-no-login",
        )
    )
    # 2. Adopt any pre-auth (NULL-owner) sessions so the column can be made NOT NULL. No-op when
    #    there are none.
    op.execute(
        sa.text("UPDATE sessions SET user_id = CAST(:id AS uuid) WHERE user_id IS NULL").bindparams(
            id=SYSTEM_OWNER_ID
        )
    )
    # 3. Enforce ownership at the schema level.
    op.alter_column("sessions", "user_id", existing_type=sa.Uuid(), nullable=False)


def downgrade() -> None:
    """Relax the constraint back to nullable. Adopted rows + the system user are left in place
    (non-destructive) — re-running upgrade is a no-op for them."""
    op.alter_column("sessions", "user_id", existing_type=sa.Uuid(), nullable=True)
