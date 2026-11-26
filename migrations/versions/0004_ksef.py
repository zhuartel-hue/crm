"""KSeF: ustawienia firmy, status i numer KSeF faktury, podstawa zwolnienia, MPP

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("ksef_env", sa.String(10), nullable=False, server_default="off"))
        b.add_column(sa.Column("ksef_token_enc", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("exemption_basis", sa.String(256), nullable=False, server_default=""))
        b.add_column(sa.Column("split_payment", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("ksef_status", sa.String(10), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_env", sa.String(10), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_number", sa.String(40), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_session_ref", sa.String(40), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_invoice_ref", sa.String(40), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_message", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_sent_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("ksef_xml", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_hash", sa.String(64), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_upo", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("invoices") as b:
        for col in ("ksef_upo", "ksef_hash", "ksef_xml", "ksef_sent_at", "ksef_message", "ksef_invoice_ref",
                    "ksef_session_ref", "ksef_number", "ksef_env", "ksef_status", "split_payment", "exemption_basis"):
            b.drop_column(col)
    with op.batch_alter_table("companies") as b:
        b.drop_column("ksef_token_enc")
        b.drop_column("ksef_env")
