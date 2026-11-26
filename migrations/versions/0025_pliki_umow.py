"""Umowy: pliki (skany umowy, aneksy, wypowiedzenia) dodawane recznie, trzymane w bazie (sa w kopii).

Revision ID: 0025
Revises: 0024
"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contract_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("recurring_id", sa.Integer(), sa.ForeignKey("recurring_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("added_by", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_contract_files_company_id", "contract_files", ["company_id"])
    op.create_index("ix_contract_files_recurring_id", "contract_files", ["recurring_id"])


def downgrade() -> None:
    op.drop_table("contract_files")
