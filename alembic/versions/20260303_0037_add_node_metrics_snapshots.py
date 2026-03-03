"""Add node_metrics_snapshots table for historical metrics tracking.

Revision ID: 0037
Revises: 0036
Create Date: 2026-03-03

Stores periodic snapshots of node system metrics (CPU, RAM, disk) for
analytics aggregation (AVG, MAX over configurable periods).
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0037'
down_revision: Union[str, None] = '0036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS node_metrics_snapshots (
            id BIGSERIAL PRIMARY KEY,
            node_uuid UUID NOT NULL REFERENCES nodes(uuid) ON DELETE CASCADE,
            cpu_usage FLOAT,
            cpu_cores INTEGER,
            memory_usage FLOAT,
            memory_total_bytes BIGINT,
            memory_used_bytes BIGINT,
            disk_usage FLOAT,
            disk_total_bytes BIGINT,
            disk_used_bytes BIGINT,
            disk_read_speed_bps BIGINT DEFAULT 0,
            disk_write_speed_bps BIGINT DEFAULT 0,
            uptime_seconds INTEGER,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_nms_node_created
        ON node_metrics_snapshots(node_uuid, created_at)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS node_metrics_snapshots")
