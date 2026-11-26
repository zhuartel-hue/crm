"""Etap B (SaaS): organizacje nad firmami i uzytkownicy z rolami.

Dodaje tabele organisations i users oraz kolumne companies.organisation_id (na razie NULL dozwolone,
NOT NULL w osobnym, pozniejszym wdrozeniu - zasada dwuetapowych migracji). Istniejace firmy trafiaja
do jednej organizacji nazwanej od pierwszej firmy. Konto wlasciciela powstaje przy pierwszym
logowaniu starym loginem (app/users.py), wiec logowanie dziala jak dotad.

Numer 0100 zostawia miejsce na migracje modulow (0026-0099); przy laczeniu galezi down_revision
ustawiamy na ostatnia z nich.

Revision ID: 0100
Revises: 0025
"""
import sqlalchemy as sa
from alembic import op

revision = "0100"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organisations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("plan", sa.String(length=30), nullable=False, server_default="wewnetrzny"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organisation_id", sa.Integer(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("login", sa.String(length=80), nullable=False, unique=True),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("email", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="handlowiec"),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_organisation_id", "users", ["organisation_id"])
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("organisation_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_companies_organisation", "organisations", ["organisation_id"], ["id"])
        b.create_index("ix_companies_organisation_id", ["organisation_id"])
    for t in ("api_tokens", "oauth_codes"):
        with op.batch_alter_table(t) as b:
            b.add_column(sa.Column("organisation_id", sa.Integer(), nullable=True))

    conn = op.get_bind()
    first = conn.execute(sa.text("SELECT short_name FROM companies ORDER BY id LIMIT 1")).first()
    if first is not None:
        conn.execute(sa.text("INSERT INTO organisations (name, plan, status) VALUES (:n, 'wewnetrzny', 'active')"),
                     {"n": first[0] or "Moja organizacja"})
        org_id = conn.execute(sa.text("SELECT MIN(id) FROM organisations")).scalar()
        conn.execute(sa.text("UPDATE companies SET organisation_id = :o WHERE organisation_id IS NULL"), {"o": org_id})
        # Dotychczasowe polaczenia Claude i klucze API widza dane tej (jedynej) organizacji.
        conn.execute(sa.text("UPDATE api_tokens SET organisation_id = :o WHERE organisation_id IS NULL"), {"o": org_id})


def downgrade() -> None:
    for t in ("api_tokens", "oauth_codes"):
        with op.batch_alter_table(t) as b:
            b.drop_column("organisation_id")
    with op.batch_alter_table("companies") as b:
        b.drop_index("ix_companies_organisation_id")
        b.drop_constraint("fk_companies_organisation", type_="foreignkey")
        b.drop_column("organisation_id")
    op.drop_index("ix_users_organisation_id", table_name="users")
    op.drop_table("users")
    op.drop_table("organisations")
