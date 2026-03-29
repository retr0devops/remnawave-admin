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
            is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            expected_connection_types JSONB NOT NULL DEFAULT '[]'::jsonb,
            strict_mode BOOLEAN NOT NULL DEFAULT TRUE,
            violation_score INTEGER NOT NULL DEFAULT 70,
            reason_template TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT node_network_policies_violation_score_check
                CHECK (violation_score >= 0 AND violation_score <= 100)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_node_network_policies_enabled
        ON node_network_policies (is_enabled)
        WHERE is_enabled = TRUE
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS node_network_policies")
