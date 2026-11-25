"""Szablony maili i faktury cykliczne

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(15), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_templates_company_id", "email_templates", ["company_id"])
    op.create_table(
        "recurring_invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("buyer_name", sa.String(300), nullable=False),
        sa.Column("buyer_nip", sa.String(20), nullable=False),
        sa.Column("buyer_address", sa.Text(), nullable=False),
        sa.Column("items_json", sa.Text(), nullable=False),
        sa.Column("prices_gross", sa.Boolean(), nullable=False),
        sa.Column("payment_method", sa.String(15), nullable=False),
        sa.Column("due_days", sa.Integer(), nullable=False),
        sa.Column("interval_months", sa.Integer(), nullable=False),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("next_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("email_to", sa.String(300), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_recurring_invoices_company_id", "recurring_invoices", ["company_id"])
    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("recurring_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_invoices_recurring_id", "recurring_invoices", ["recurring_id"], ["id"], ondelete="SET NULL")
        b.create_index("ix_invoices_recurring_id", ["recurring_id"])


def downgrade() -> None:
    with op.batch_alter_table("invoices") as b:
        b.drop_index("ix_invoices_recurring_id")
        b.drop_constraint("fk_invoices_recurring_id", type_="foreignkey")
        b.drop_column("recurring_id")
    op.drop_index("ix_recurring_invoices_company_id", "recurring_invoices")
    op.drop_table("recurring_invoices")
    op.drop_index("ix_email_templates_company_id", "email_templates")
    op.drop_table("email_templates")
