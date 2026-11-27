"""produkty ze zdjeciami, haslo zmieniane w aplikacji

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, server_default=""),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False, server_default="szt."),
        sa.Column("price_net", sa.Numeric(14, 2), nullable=True),
        sa.Column("vat_rate", sa.String(5), nullable=False, server_default="23"),
        sa.Column("ean", sa.String(20), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_ref", sa.String(120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_products_code", "products", ["code"])
    op.create_index("ix_products_source_ref", "products", ["source_ref"])
    op.create_table(
        "product_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filename", sa.String(200), nullable=False, server_default=""),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("thumb", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_product_photos_product_id", "product_photos", ["product_id"])
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
    )
    with op.batch_alter_table("invoice_items") as b:
        b.add_column(sa.Column("product_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_invoice_items_product", "products", ["product_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("invoice_items") as b:
        b.drop_constraint("fk_invoice_items_product", type_="foreignkey")
        b.drop_column("product_id")
    op.drop_table("app_settings")
    op.drop_index("ix_product_photos_product_id", "product_photos")
    op.drop_table("product_photos")
    op.drop_index("ix_products_source_ref", "products")
    op.drop_index("ix_products_code", "products")
    op.drop_table("products")
