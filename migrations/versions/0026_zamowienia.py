"""Zamowienia od klientow i do dostawcow (pozycje, termin, status, powiazanie z oferta, faktura i WZ).

Revision ID: 0026
Revises: 0025
"""
import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("number", sa.String(60), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recipient_name", sa.String(300), nullable=False),
        sa.Column("recipient_address", sa.Text(), nullable=False),
        sa.Column("contact_person", sa.String(200), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False),
        sa.Column("customer_ref", sa.String(100), nullable=False),
        sa.Column("prices_gross", sa.Boolean(), nullable=False),
        sa.Column("total_net", MONEY, nullable=False),
        sa.Column("total_vat", MONEY, nullable=False),
        sa.Column("total_gross", MONEY, nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("internal_notes", sa.Text(), nullable=False),
        sa.Column("offer_id", sa.Integer(), sa.ForeignKey("offers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("deliveries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("source_ref", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "number", name="uq_order_number"),
    )
    op.create_index("ix_orders_company_id", "orders", ["company_id"])
    op.create_index("ix_orders_source_ref", "orders", ["source_ref"])
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit", sa.String(10), nullable=False),
        sa.Column("unit_price", MONEY, nullable=True),
        sa.Column("vat_rate", sa.String(3), nullable=False),
        sa.Column("net", MONEY, nullable=False),
        sa.Column("vat", MONEY, nullable=False),
        sa.Column("gross", MONEY, nullable=False),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])


def downgrade() -> None:
    op.drop_table("order_items")
    op.drop_table("orders")
