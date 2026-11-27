"""Koszty: zalaczniki do weryfikacji (skany, PDF) i kto/kiedy zweryfikowal fakture kosztowa.

Revision ID: 0102
Revises: 0101
"""
import sqlalchemy as sa
from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_invoices", sa.Column("verified_at", sa.DateTime(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("verified_by", sa.String(length=60), nullable=False, server_default=""))
    op.create_table(
        "purchase_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("purchase_id", sa.Integer(), sa.ForeignKey("purchase_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("added_by", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_purchase_files_company_id", "purchase_files", ["company_id"])
    op.create_index("ix_purchase_files_purchase_id", "purchase_files", ["purchase_id"])


def downgrade() -> None:
    op.drop_table("purchase_files")
    op.drop_column("purchase_invoices", "verified_by")
    op.drop_column("purchase_invoices", "verified_at")
