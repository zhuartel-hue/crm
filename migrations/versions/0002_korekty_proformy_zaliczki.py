"""korekty, proformy, zaliczki

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("pattern_proforma", sa.String(60), nullable=False, server_default="PRO/{nr}/{MM}/{RRRR}"))
        b.add_column(sa.Column("pattern_correction", sa.String(60), nullable=False, server_default="FK/{nr}/{MM}/{RRRR}"))
        b.add_column(sa.Column("pattern_advance", sa.String(60), nullable=False, server_default="FZ/{nr}/{MM}/{RRRR}"))
    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("related_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("correction_reason", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("advance_gross", sa.Numeric(14, 2), nullable=True))
        b.add_column(sa.Column("order_total", sa.Numeric(14, 2), nullable=True))
        b.add_column(sa.Column("settled_by_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_invoices_related", "invoices", ["related_id"], ["id"])
        b.create_foreign_key("fk_invoices_settled_by", "invoices", ["settled_by_id"], ["id"])
    with op.batch_alter_table("invoice_items") as b:
        b.add_column(sa.Column("is_before", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("invoice_items") as b:
        b.drop_column("is_before")
    with op.batch_alter_table("invoices") as b:
        b.drop_constraint("fk_invoices_settled_by", type_="foreignkey")
        b.drop_constraint("fk_invoices_related", type_="foreignkey")
        for c in ("settled_by_id", "order_total", "advance_gross", "correction_reason", "related_id"):
            b.drop_column(c)
    with op.batch_alter_table("companies") as b:
        for c in ("pattern_advance", "pattern_correction", "pattern_proforma"):
            b.drop_column(c)
