"""Zadania i kalendarz (takze zlecenia serwisowe).

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

INDEXED = ("company_id", "parent_id", "contractor_id", "equipment_id", "start_at", "due_at", "source_ref")


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("priority", sa.String(length=10), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("contractor_id", sa.Integer(), nullable=True),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("equipment_id", sa.Integer(), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.Column("done_at", sa.DateTime(), nullable=True),
        sa.Column("assignee", sa.String(length=100), nullable=False),
        sa.Column("minutes_spent", sa.Integer(), nullable=False),
        sa.Column("timer_started_at", sa.DateTime(), nullable=True),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contractor_id"], ["contractors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in INDEXED:
        op.create_index(f"ix_tasks_{col}", "tasks", [col])


def downgrade() -> None:
    for col in INDEXED:
        op.drop_index(f"ix_tasks_{col}", table_name="tasks")
    op.drop_table("tasks")
