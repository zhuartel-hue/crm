"""Umowy: faktury cykliczne staja sie umowami (numer, daty, adnotacje, skrzynka do wysylki).

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recurring_invoices") as b:
        b.add_column(sa.Column("number", sa.String(length=60), server_default="", nullable=False))
        b.add_column(sa.Column("signed_date", sa.Date(), nullable=True))
        b.add_column(sa.Column("start_date", sa.Date(), nullable=True))
        b.add_column(sa.Column("invoicing", sa.Boolean(), server_default=sa.true(), nullable=False))
        b.add_column(sa.Column("mail_account_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("contract_notes", sa.Text(), server_default="", nullable=False))
        b.add_column(sa.Column("source_ref", sa.String(length=120), server_default="", nullable=False))
        b.create_foreign_key("fk_recurring_mail_account", "mail_accounts", ["mail_account_id"], ["id"],
                             ondelete="SET NULL")
        b.create_index("ix_recurring_invoices_source_ref", ["source_ref"])


def downgrade() -> None:
    with op.batch_alter_table("recurring_invoices") as b:
        b.drop_index("ix_recurring_invoices_source_ref")
        b.drop_constraint("fk_recurring_mail_account", type_="foreignkey")
        for c in ("source_ref", "contract_notes", "mail_account_id", "invoicing", "start_date", "signed_date", "number"):
            b.drop_column(c)
