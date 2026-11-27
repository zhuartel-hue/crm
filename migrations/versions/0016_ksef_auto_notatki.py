"""Kontrahent: automatyczna wysylka faktur do KSeF; notatki przy kontrahencie.

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contractors", sa.Column("ksef_auto", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        "contractor_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contractor_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("author", sa.String(length=100), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["contractor_id"], ["contractors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contractor_notes_contractor_id", "contractor_notes", ["contractor_id"])
    op.create_index("ix_contractor_notes_source_ref", "contractor_notes", ["source_ref"])


def downgrade() -> None:
    op.drop_index("ix_contractor_notes_source_ref", table_name="contractor_notes")
    op.drop_index("ix_contractor_notes_contractor_id", table_name="contractor_notes")
    op.drop_table("contractor_notes")
    op.drop_column("contractors", "ksef_auto")
