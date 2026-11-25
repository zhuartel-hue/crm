"""Magazyn: stany produktow, ruchy, PZ, ceny i VAT na WZ

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("pattern_pz", sa.String(60), nullable=False, server_default="PZ/{nr}/{MM}/{RRRR}"))
    with op.batch_alter_table("products") as b:
        b.add_column(sa.Column("is_service", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("stock", sa.Numeric(12, 3), nullable=False, server_default="0"))
    with op.batch_alter_table("deliveries") as b:
        b.add_column(sa.Column("kind", sa.String(3), nullable=False, server_default="wz"))
        b.add_column(sa.Column("prices_gross", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("total_net", sa.Numeric(14, 2), nullable=False, server_default="0"))
        b.add_column(sa.Column("total_vat", sa.Numeric(14, 2), nullable=False, server_default="0"))
        b.add_column(sa.Column("total_gross", sa.Numeric(14, 2), nullable=False, server_default="0"))
    with op.batch_alter_table("delivery_items") as b:
        b.add_column(sa.Column("unit_price", sa.Numeric(14, 2), nullable=True))
        b.add_column(sa.Column("vat_rate", sa.String(3), nullable=False, server_default="23"))
        b.add_column(sa.Column("net", sa.Numeric(14, 2), nullable=False, server_default="0"))
        b.add_column(sa.Column("vat", sa.Numeric(14, 2), nullable=False, server_default="0"))
        b.add_column(sa.Column("gross", sa.Numeric(14, 2), nullable=False, server_default="0"))
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("deliveries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_stock_movements_product_id", "stock_movements", ["product_id"])


def downgrade() -> None:
    op.drop_table("stock_movements")
    for t, cols in (("delivery_items", ["gross", "vat", "net", "vat_rate", "unit_price"]),
                    ("deliveries", ["total_gross", "total_vat", "total_net", "prices_gross", "kind"]),
                    ("products", ["stock", "is_service"]), ("companies", ["pattern_pz"])):
        with op.batch_alter_table(t) as b:
            for c in cols:
                b.drop_column(c)
