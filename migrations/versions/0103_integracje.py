"""Integracje: sklep (Shopify), Allegro, kurierzy (Apaczka); powiazania produktow, przesylki, pola zamowien.

Revision ID: 0103
Revises: 0102
"""
import sqlalchemy as sa
from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("state", sa.String(length=10), nullable=False, server_default="new"),
        sa.Column("settings_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("secret", sa.Text(), nullable=False, server_default=""),
        sa.Column("auth_state", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("cursor", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("last_sync", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_integrations_company_id", "integrations", ["company_id"])
    op.create_table(
        "product_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("integration_id", sa.Integer(), sa.ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_id", sa.String(length=80), nullable=False),
        sa.Column("external_sku", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("external_name", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("external_stock", sa.Numeric(12, 3), nullable=True),
        sa.Column("external_price", MONEY, nullable=True),
        sa.Column("external_status", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("integration_id", "external_id", name="uq_product_link"),
    )
    for col in ("company_id", "integration_id", "product_id"):
        op.create_index(f"ix_product_links_{col}", "product_links", [col])
    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("integration_id", sa.Integer(), sa.ForeignKey("integrations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="apaczka"),
        sa.Column("service_id", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("service_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("external_id", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("waybill", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("tracking_url", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("state", sa.String(length=12), nullable=False, server_default="draft"),
        sa.Column("status_text", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("receiver_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("parcel_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("cod_amount", MONEY, nullable=True),
        sa.Column("price_gross", MONEY, nullable=True),
        sa.Column("label", sa.LargeBinary(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    for col in ("company_id", "order_id", "waybill"):
        op.create_index(f"ix_shipments_{col}", "shipments", [col])
    with op.batch_alter_table("orders") as b:
        b.add_column(sa.Column("channel", sa.String(length=20), nullable=False, server_default=""))
        b.add_column(sa.Column("integration_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("external_status", sa.String(length=60), nullable=False, server_default=""))
        b.add_column(sa.Column("payment_status", sa.String(length=20), nullable=False, server_default=""))
        b.add_column(sa.Column("shipping_method", sa.String(length=200), nullable=False, server_default=""))
        b.add_column(sa.Column("pickup_point", sa.String(length=300), nullable=False, server_default=""))
        b.add_column(sa.Column("delivery_address", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("buyer_tax_id", sa.String(length=30), nullable=False, server_default=""))
        b.add_column(sa.Column("invoice_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.create_foreign_key("fk_orders_integration", "integrations", ["integration_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("orders") as b:
        b.drop_constraint("fk_orders_integration", type_="foreignkey")
        for col in ("invoice_requested", "buyer_tax_id", "delivery_address", "pickup_point", "shipping_method",
                    "payment_status", "external_status", "integration_id", "channel"):
            b.drop_column(col)
    op.drop_table("shipments")
    op.drop_table("product_links")
    op.drop_table("integrations")
