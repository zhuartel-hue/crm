"""Polaczenie z bankiem przez PSD2 (Enable Banking)

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bank_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("aspsp_name", sa.String(120), nullable=False),
        sa.Column("aspsp_country", sa.String(2), nullable=False),
        sa.Column("psu_type", sa.String(10), nullable=False),
        sa.Column("auth_state", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("accounts_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("last_sync", sa.DateTime(), nullable=True),
        sa.Column("synced_until", sa.Date(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bank_connections_company", "bank_connections", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_bank_connections_company", table_name="bank_connections")
    op.drop_table("bank_connections")
