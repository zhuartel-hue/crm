"""Osoby kontaktowe i strona www kontrahenta

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("contractors") as b:
        b.add_column(sa.Column("website", sa.String(200), nullable=False, server_default=""))
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("position", sa.String(150), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False),
        sa.Column("phone2", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_contacts_contractor_id", "contacts", ["contractor_id"])
    op.create_index("ix_contacts_source_ref", "contacts", ["source_ref"])


def downgrade() -> None:
    op.drop_table("contacts")
    with op.batch_alter_table("contractors") as b:
        b.drop_column("website")
