"""Poczta: skrzynki IMAP/SMTP firm, wiadomosci i zalaczniki

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("imap_host", sa.String(200), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False),
        sa.Column("smtp_host", sa.String(200), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("smtp_security", sa.String(10), nullable=False),
        sa.Column("username", sa.String(200), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sync_state", sa.Text(), nullable=False),
        sa.Column("last_sync", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "mail_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("folder", sa.String(200), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.String(300), nullable=False),
        sa.Column("in_reply_to", sa.String(300), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("from_addr", sa.String(200), nullable=False),
        sa.Column("from_name", sa.String(200), nullable=False),
        sa.Column("to_addrs", sa.Text(), nullable=False),
        sa.Column("cc_addrs", sa.Text(), nullable=False),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("seen", sa.Boolean(), nullable=False),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mail_account_date", "mail_messages", ["account_id", "date"])
    op.create_index("ix_mail_contractor", "mail_messages", ["contractor_id"])
    op.create_index("ix_mail_invoice", "mail_messages", ["invoice_id"])
    op.create_index("ix_mail_msgid", "mail_messages", ["account_id", "message_id"])
    op.create_table(
        "mail_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("mail_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=True),
    )
    op.create_index("ix_mail_attachments_message_id", "mail_attachments", ["message_id"])


def downgrade() -> None:
    op.drop_table("mail_attachments")
    op.drop_table("mail_messages")
    op.drop_table("mail_accounts")
