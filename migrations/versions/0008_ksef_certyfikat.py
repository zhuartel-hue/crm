"""KSeF: logowanie certyfikatem KSeF (XAdES) obok tokena

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("ksef_auth", sa.String(10), nullable=False, server_default="token"))
        b.add_column(sa.Column("ksef_cert_pem", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("ksef_key_enc", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("companies") as b:
        b.drop_column("ksef_key_enc")
        b.drop_column("ksef_cert_pem")
        b.drop_column("ksef_auth")
