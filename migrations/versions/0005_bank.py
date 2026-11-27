"""Bank: operacje z wyciagow MT940 i dopasowanie do faktur

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bank_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("account", sa.String(40), nullable=False),
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("counterparty", sa.String(300), nullable=False),
        sa.Column("counterparty_account", sa.String(40), nullable=False),
        sa.Column("op_code", sa.String(5), nullable=False),
        sa.Column("bank_ref", sa.String(40), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("applied_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "fingerprint", name="uq_bank_tx_fingerprint"),
    )
    op.create_index("ix_bank_tx_company_date", "bank_transactions", ["company_id", "booking_date"])


def downgrade() -> None:
    op.drop_index("ix_bank_tx_company_date", "bank_transactions")
    op.drop_table("bank_transactions")
