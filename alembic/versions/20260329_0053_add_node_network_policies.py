"""Add node_network_policies table for node-specific anti-abuse rules.

Revision ID: 0053
Revises: 0052
Create Date: 2026-03-29
"""
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS node_network_policies (
            id BIGSERIAL PRIMARY KEY,
            node_uuid UUID NOT NULL UNIQUE REFERENCES nodes(uuid) ON DELETE CASCADE,
            is_enabled BOOLEAN NOT NULL DEFAULT true,
            expected_connection_types JSONB NOT NULL DEFAULT '[]'::jsonb,
            strict_mode BOOLEAN NOT NULL DEFAULT true,
            violation_score INTEGER NOT NULL DEFAULT 70,
            reason_template TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_node_network_policies_enabled
        ON node_network_policies (is_enabled)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_node_network_policies_updated_at
        ON node_network_policies (updated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS idx_node_network_policies_updated_at"
    )
    op.execute(
        "DROP INDEX IF EXISTS idx_node_network_policies_enabled"
    )
    op.execute("DROP TABLE IF EXISTS node_network_policies")
