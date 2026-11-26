"""Polaczenia telefoniczne: historia z Firmao i notatki z rozmow, przy kliencie.

Revision ID: 0027
Revises: 0026
"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phone_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False),
        sa.Column("user_label", sa.String(100), nullable=False),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("source_ref", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    for col in ("company_id", "started_at", "phone", "contractor_id", "source_ref"):
        op.create_index(f"ix_phone_calls_{col}", "phone_calls", [col])


def downgrade() -> None:
    op.drop_table("phone_calls")
