"""ensure workflow_runs.call_disposition / is_simulation exist on fresh installs

The development database received these two columns from the manually applied
``.checkpoints/migration_stage_c.sql``; no alembic revision ever created them, so a database built
by ``alembic upgrade head`` alone cannot insert a workflow run. This revision is idempotent
(``IF NOT EXISTS``) and therefore a no-op on databases that already have the columns.

Revision ID: c02a1d3e5f70
Revises: f1a2b3c4d5e6
Create Date: 2026-11-17
"""

from alembic import op

revision = "c02a1d3e5f70"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS call_disposition VARCHAR(64) DEFAULT 'UNKNOWN'")
    op.execute("ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS is_simulation BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    # Never drop columns that may hold production data (they pre-exist on installs that applied the
    # manual Stage C SQL).
    pass
