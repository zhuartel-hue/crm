"""Sprzet i serwis: urzadzenia u klientow i historia serwisu.

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equipment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("inventory_no", sa.String(length=100), nullable=False),
        sa.Column("serial_no", sa.String(length=150), nullable=False),
        sa.Column("barcode", sa.String(length=100), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("contractor_id", sa.Integer(), nullable=True),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("supplier", sa.String(length=300), nullable=False),
        sa.Column("location", sa.String(length=300), nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("install_date", sa.Date(), nullable=True),
        sa.Column("price", sa.Numeric(14, 2), nullable=True),
        sa.Column("warranty_until", sa.Date(), nullable=True),
        sa.Column("warranty_remind_days", sa.Integer(), nullable=False),
        sa.Column("service_interval_months", sa.Integer(), nullable=False),
        sa.Column("next_service", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contractor_id"], ["contractors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("company_id", "serial_no", "contractor_id", "source_ref"):
        op.create_index(f"ix_equipment_{col}", "equipment", [col])
    op.create_table(
        "service_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("equipment_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=15), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=100), nullable=False),
        sa.Column("delivery_id", sa.Integer(), nullable=True),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["delivery_id"], ["deliveries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_entries_equipment_id", "service_entries", ["equipment_id"])


def downgrade() -> None:
    op.drop_index("ix_service_entries_equipment_id", table_name="service_entries")
    op.drop_table("service_entries")
    for col in ("company_id", "serial_no", "contractor_id", "source_ref"):
        op.drop_index(f"ix_equipment_{col}", table_name="equipment")
    op.drop_table("equipment")
