"""Zrodlo dokumentow (import z Firmao bez dubli)

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TABLES = ("invoices", "deliveries", "offers", "purchase_invoices")


def upgrade() -> None:
    for t in TABLES:
        with op.batch_alter_table(t) as b:
            b.add_column(sa.Column("source_ref", sa.String(120), nullable=False, server_default=""))
        op.create_index(f"ix_{t}_source_ref", t, ["source_ref"])


def downgrade() -> None:
    for t in TABLES:
        op.drop_index(f"ix_{t}_source_ref", t)
        with op.batch_alter_table(t) as b:
            b.drop_column("source_ref")
