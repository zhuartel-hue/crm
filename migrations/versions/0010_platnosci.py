"""Platnosci: wplaty czesciowe, przypomnienia, faktury kosztowe z KSeF

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
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("ksef_purchase_cursor", sa.String(40), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_purchase_synced_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("ksef_purchase_error", sa.Text(), nullable=False, server_default=""))
    op.create_table(
        "purchase_invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("ksef_env", sa.String(10), nullable=False),
        sa.Column("ksef_number", sa.String(40), nullable=False),
        sa.Column("invoice_number", sa.String(256), nullable=False),
        sa.Column("invoice_type", sa.String(10), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("stored_at", sa.String(40), nullable=False),
        sa.Column("seller_nip", sa.String(20), nullable=False),
        sa.Column("seller_name", sa.String(512), nullable=False),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("net", sa.Numeric(14, 2), nullable=False),
        sa.Column("vat", sa.Numeric(14, 2), nullable=False),
        sa.Column("gross", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("bank_account", sa.String(40), nullable=False),
        sa.Column("xml", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("paid_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "ksef_number", name="uq_purchase_ksef"),
    )
    op.create_index("ix_purchase_company_date", "purchase_invoices", ["company_id", "issue_date"])


def downgrade() -> None:
    op.drop_index("ix_purchase_company_date", "purchase_invoices")
    op.drop_table("purchase_invoices")
    with op.batch_alter_table("companies") as b:
        b.drop_column("ksef_purchase_error")
        b.drop_column("ksef_purchase_synced_at")
        b.drop_column("ksef_purchase_cursor")
    op.drop_index("ix_invoice_payments_invoice_id", "invoice_payments")
    op.drop_table("invoice_payments")
    with op.batch_alter_table("invoices") as b:
        b.drop_column("last_reminder_at")
        b.drop_column("reminder_count")
