"""Wyglad faktury: logo firmy i wybrany wzor PDF.

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("logo", sa.LargeBinary(), nullable=True))
    op.add_column("companies", sa.Column("logo_type", sa.String(length=20), server_default="", nullable=False))
    op.add_column("companies", sa.Column("invoice_template", sa.String(length=20), server_default="klasyczny",
                                         nullable=False))


def downgrade() -> None:
    op.drop_column("companies", "invoice_template")
    op.drop_column("companies", "logo_type")
    op.drop_column("companies", "logo")
