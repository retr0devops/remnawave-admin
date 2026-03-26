"""Add email column to admin_accounts for password reset.

Revision ID: 0050
Revises: 0049
Create Date: 2026-03-26
"""
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE admin_accounts
        ADD COLUMN IF NOT EXISTS email VARCHAR(255)
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_admin_accounts_email
        ON admin_accounts (LOWER(email))
        WHERE email IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_admin_accounts_email")
    op.execute("ALTER TABLE admin_accounts DROP COLUMN IF EXISTS email")
