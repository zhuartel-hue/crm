"""Platnosci: wplaty czesciowe, przypomnienia

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("reminder_count", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("last_reminder_at", sa.DateTime(), nullable=True))
    op.create_table(
        "invoice_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("method", sa.String(15), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_invoice_payments_invoice_id", "invoice_payments", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_payments_invoice_id", "invoice_payments")
    op.drop_table("invoice_payments")
    with op.batch_alter_table("invoices") as b:
        b.drop_column("last_reminder_at")
        b.drop_column("reminder_count")
