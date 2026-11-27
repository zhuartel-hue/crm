"""WZ: wydania zewnetrzne z pozycjami i podpisem odbiorcy

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("pattern_wz", sa.String(60), nullable=False, server_default="WZ/{nr}/{MM}/{RRRR}"))
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("number", sa.String(60), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recipient_name", sa.String(300), nullable=False),
        sa.Column("recipient_address", sa.Text(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("signed_by", sa.String(200), nullable=False),
        sa.Column("signature_png", sa.LargeBinary(), nullable=True),
        sa.Column("signed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "number", name="uq_delivery_number"),
    )
    op.create_table(
        "delivery_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit", sa.String(10), nullable=False),
    )
    op.create_index("ix_delivery_items_delivery_id", "delivery_items", ["delivery_id"])


def downgrade() -> None:
    op.drop_table("delivery_items")
    op.drop_table("deliveries")
    with op.batch_alter_table("companies") as b:
        b.drop_column("pattern_wz")
