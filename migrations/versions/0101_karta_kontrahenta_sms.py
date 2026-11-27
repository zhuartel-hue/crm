"""Karta kontrahenta jak w Firmao: typ, REGON, KRS, konto, adres korespondencyjny, nastepny kontakt; SMS-y; pliki kontrahenta.

Tylko dodawanie kolumn i tabeli (migracja odwracalna).

Revision ID: 0101
Revises: 0100
"""
import sqlalchemy as sa
from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None

COLS = [
    ("customer_type", sa.String(20)),
    ("regon", sa.String(20)),
    ("krs", sa.String(20)),
    ("bank_account", sa.String(40)),
    ("corr_address", sa.String(300)),
]


def upgrade() -> None:
    with op.batch_alter_table("contractors") as b:
        for name, typ in COLS:
            b.add_column(sa.Column(name, typ, nullable=False, server_default=""))
        b.add_column(sa.Column("next_contact", sa.Date(), nullable=True))
    op.create_table(
        "sms_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False, index=True),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True,
                  index=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("direction", sa.String(3), nullable=False, server_default="out"),
        sa.Column("phone", sa.String(40), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="waiting"),
        sa.Column("provider_ref", sa.String(100), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )


    op.create_table(
        "contractor_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False, index=True),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("filename", sa.String(200), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(100), nullable=False, server_default="application/octet-stream"),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("added_by", sa.String(60), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("contractor_files")
    op.drop_table("sms_messages")
    with op.batch_alter_table("contractors") as b:
        b.drop_column("next_contact")
        for name, _ in reversed(COLS):
            b.drop_column(name)
