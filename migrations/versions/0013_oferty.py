"""Oferty: naglowek, pozycje, wzor numeru

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)


def upgrade() -> None:
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("pattern_offer", sa.String(60), nullable=False, server_default="OF/{nr}/{MM}/{RRRR}"))
    op.create_table(
        "offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("number", sa.String(60), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("probability", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recipient_name", sa.String(300), nullable=False),
        sa.Column("recipient_address", sa.Text(), nullable=False),
        sa.Column("contact_person", sa.String(200), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("prices_gross", sa.Boolean(), nullable=False),
        sa.Column("total_net", MONEY, nullable=False),
        sa.Column("total_vat", MONEY, nullable=False),
        sa.Column("total_gross", MONEY, nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("internal_notes", sa.Text(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("deliveries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "number", name="uq_offer_number"),
    )
    op.create_index("ix_offers_company_id", "offers", ["company_id"])
    op.create_table(
        "offer_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("offer_id", sa.Integer(), sa.ForeignKey("offers.id", ondelete="CASCADE"), nullable=False),
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
    op.create_index("ix_offer_items_offer_id", "offer_items", ["offer_id"])


def downgrade() -> None:
    op.drop_table("offer_items")
    op.drop_table("offers")
    with op.batch_alter_table("companies") as b:
        b.drop_column("pattern_offer")
