"""Umowy: status (jak w Firmao, edytowalny w ustawieniach). Fakturowanie wlacza sie osobno.

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recurring_invoices") as b:
        b.add_column(sa.Column("contract_status", sa.String(length=12), server_default="active", nullable=False))
    op.execute("UPDATE recurring_invoices SET contract_status = 'expired' WHERE active = false")


def downgrade() -> None:
    with op.batch_alter_table("recurring_invoices") as b:
        b.drop_column("contract_status")
