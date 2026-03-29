"""Add Banhammer state and event tables.

Revision ID: 0054
Revises: 0053
Create Date: 2026-03-30
"""
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS banhammer_user_states (
            user_uuid UUID PRIMARY KEY,
            warnings_count INTEGER NOT NULL DEFAULT 0,
            block_stage INTEGER NOT NULL DEFAULT 0,
            blocked_until TIMESTAMPTZ,
            pre_block_status VARCHAR(50),
            last_warning_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS banhammer_events (
            id BIGSERIAL PRIMARY KEY,
            user_uuid UUID NOT NULL REFERENCES users(uuid) ON DELETE CASCADE,
            event_type VARCHAR(32) NOT NULL,
            warning_number INTEGER,
            block_stage INTEGER,
            block_minutes INTEGER,
            blocked_until TIMESTAMPTZ,
            message TEXT,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banhammer_user_states_blocked_until
        ON banhammer_user_states (blocked_until)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banhammer_user_states_updated_at
        ON banhammer_user_states (updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banhammer_events_user_created
        ON banhammer_events (user_uuid, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banhammer_events_created
        ON banhammer_events (created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banhammer_events_type
        ON banhammer_events (event_type)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_banhammer_events_type")
    op.execute("DROP INDEX IF EXISTS idx_banhammer_events_created")
    op.execute("DROP INDEX IF EXISTS idx_banhammer_events_user_created")
    op.execute("DROP INDEX IF EXISTS idx_banhammer_user_states_updated_at")
    op.execute("DROP INDEX IF EXISTS idx_banhammer_user_states_blocked_until")
    op.execute("DROP TABLE IF EXISTS banhammer_events")
    op.execute("DROP TABLE IF EXISTS banhammer_user_states")
