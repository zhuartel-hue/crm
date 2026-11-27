"""Oferta: osobny naglowek na PDF; tytul jest wewnetrzny (dla zespolu, jak nazwa oferty w Firmao).

Revision ID: 0023
Revises: 0022
"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("offers") as b:
        b.add_column(sa.Column("heading", sa.String(length=300), server_default="", nullable=False))
    # oferty zrobione w Sygnavo mialy tytul na PDF: zostaje jako naglowek
    op.execute("UPDATE offers SET heading = title WHERE source_ref NOT LIKE 'firmao:%'")


def downgrade() -> None:
    with op.batch_alter_table("offers") as b:
        b.drop_column("heading")
