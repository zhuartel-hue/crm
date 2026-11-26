"""Kontrahenci, osoby kontaktowe i produkty przypisane do firmy (bez mieszania danych firm).

Kazdy rekord dostaje firme, ktora go uzywa. Dane z Firmao (source_ref firmao:) ida do firmy,
do ktorej importowano faktury z Firmao. Kontrahent albo produkt uzywany w dwoch firmach dostaje
kopie w drugiej firmie, a jej dokumenty wskazuja juz na kopie.

Revision ID: 0022
Revises: 0021
"""
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

TABLES = ("contractors", "contacts", "products")
# (tabela dokumentu, kolumna) -> dokument ma company_id
CONTRACTOR_REFS = ("invoices", "offers", "deliveries", "purchase_invoices", "recurring_invoices", "equipment", "tasks")
CONTACT_REFS = ("equipment", "tasks")
PRODUCT_REFS = (("invoice_items", "invoice_id", "invoices"), ("offer_items", "offer_id", "offers"),
                ("delivery_items", "delivery_id", "deliveries"))


def _cols(conn, table):
    return [c["name"] for c in sa.inspect(conn).get_columns(table)]


def _default_company(conn) -> int | None:
    cid = conn.execute(sa.text("SELECT company_id FROM invoices WHERE source_ref LIKE 'firmao:%' "
                               "GROUP BY company_id ORDER BY count(*) DESC LIMIT 1")).scalar()
    return cid or conn.execute(sa.text("SELECT min(id) FROM companies")).scalar()


def _clone(conn, table, row_id, company_id, skip=("id",)):
    cols = [c for c in _cols(conn, table) if c not in skip and c != "company_id"]
    names = ", ".join(cols)
    new_id = conn.execute(sa.text(f"INSERT INTO {table} ({names}, company_id) SELECT {names}, :cid FROM {table} "
                                  f"WHERE id = :id RETURNING id"), {"cid": company_id, "id": row_id}).scalar()
    return new_id


def _assign(conn, table, uses, default):
    """uses: id -> {company_id: liczba uzyc}. Zwraca {(id, firma): nowe_id} dla kopii."""
    firmao = {r[0] for r in conn.execute(sa.text(f"SELECT id FROM {table} WHERE source_ref LIKE 'firmao:%'"))}
    clones = {}
    for (rid,) in conn.execute(sa.text(f"SELECT id FROM {table}")).fetchall():
        used = uses.get(rid, {})
        if rid in firmao or not used:
            main = default if (rid in firmao or not used or default in used) else max(used, key=used.get)
        else:
            main = default if default in used else max(used, key=used.get)
        conn.execute(sa.text(f"UPDATE {table} SET company_id = :c WHERE id = :id"), {"c": main, "id": rid})
        for cid in used:
            if cid != main:
                clones[(rid, cid)] = _clone(conn, table, rid, cid)
    return clones


def upgrade() -> None:
    for t in TABLES:
        with op.batch_alter_table(t) as b:
            b.add_column(sa.Column("company_id", sa.Integer(), nullable=True))
    conn = op.get_bind()
    default = _default_company(conn)
    if default is not None:
        # --- kontrahenci
        uses: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for t in CONTRACTOR_REFS:
            for rid, cid, n in conn.execute(sa.text(
                    f"SELECT contractor_id, company_id, count(*) FROM {t} WHERE contractor_id IS NOT NULL "
                    f"GROUP BY contractor_id, company_id")):
                uses[rid][cid] += n
        for rid, cid, n in conn.execute(sa.text(
                "SELECT m.contractor_id, a.company_id, count(*) FROM mail_messages m JOIN mail_accounts a "
                "ON a.id = m.account_id WHERE m.contractor_id IS NOT NULL GROUP BY m.contractor_id, a.company_id")):
            uses[rid][cid] += n
        clones = _assign(conn, "contractors", uses, default)
        for (old, cid), new in clones.items():
            for t in CONTRACTOR_REFS:
                conn.execute(sa.text(f"UPDATE {t} SET contractor_id = :n WHERE contractor_id = :o AND company_id = :c"),
                             {"n": new, "o": old, "c": cid})
            conn.execute(sa.text("UPDATE mail_messages SET contractor_id = :n WHERE contractor_id = :o AND account_id IN "
                                 "(SELECT id FROM mail_accounts WHERE company_id = :c)"), {"n": new, "o": old, "c": cid})
        # --- osoby kontaktowe: firma kontrahenta; kopia przy kopii kontrahenta
        conn.execute(sa.text("UPDATE contacts SET company_id = (SELECT company_id FROM contractors c "
                             "WHERE c.id = contacts.contractor_id) WHERE contractor_id IS NOT NULL"))
        for (old, cid), new in clones.items():
            for (kid,) in conn.execute(sa.text("SELECT id FROM contacts WHERE contractor_id = :o"), {"o": old}).fetchall():
                nk = _clone(conn, "contacts", kid, cid)
                conn.execute(sa.text("UPDATE contacts SET contractor_id = :n WHERE id = :k"), {"n": new, "k": nk})
                for t in CONTACT_REFS:
                    conn.execute(sa.text(f"UPDATE {t} SET contact_id = :n WHERE contact_id = :o AND company_id = :c"),
                                 {"n": nk, "o": kid, "c": cid})
        kuses: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for t in CONTACT_REFS:
            for rid, cid, n in conn.execute(sa.text(
                    f"SELECT contact_id, company_id, count(*) FROM {t} WHERE contact_id IS NOT NULL "
                    f"GROUP BY contact_id, company_id")):
                kuses[rid][cid] += n
        for (kid,) in conn.execute(sa.text("SELECT id FROM contacts WHERE company_id IS NULL")).fetchall():
            used = kuses.get(kid, {})
            main = default if not used or default in used else max(used, key=used.get)
            conn.execute(sa.text("UPDATE contacts SET company_id = :c WHERE id = :id"), {"c": main, "id": kid})
        # --- produkty
        puses: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for items, fk, doc in PRODUCT_REFS:
            for rid, cid, n in conn.execute(sa.text(
                    f"SELECT i.product_id, d.company_id, count(*) FROM {items} i JOIN {doc} d ON d.id = i.{fk} "
                    f"WHERE i.product_id IS NOT NULL GROUP BY i.product_id, d.company_id")):
                puses[rid][cid] += n
        for rid, cid, n in conn.execute(sa.text(
                "SELECT product_id, company_id, count(*) FROM equipment WHERE product_id IS NOT NULL "
                "GROUP BY product_id, company_id")):
            puses[rid][cid] += n
        pclones = _assign(conn, "products", puses, default)
        for (old, cid), new in pclones.items():
            for items, fk, doc in PRODUCT_REFS:
                conn.execute(sa.text(f"UPDATE {items} SET product_id = :n WHERE product_id = :o AND {fk} IN "
                                     f"(SELECT id FROM {doc} WHERE company_id = :c)"), {"n": new, "o": old, "c": cid})
            conn.execute(sa.text("UPDATE equipment SET product_id = :n WHERE product_id = :o AND company_id = :c"),
                         {"n": new, "o": old, "c": cid})
            # ruchy magazynowe z dokumentow drugiej firmy przechodza na kopie; stany przeliczone z ruchow tej firmy
            conn.execute(sa.text("UPDATE stock_movements SET product_id = :n WHERE product_id = :o AND delivery_id IN "
                                 "(SELECT id FROM deliveries WHERE company_id = :c)"), {"n": new, "o": old, "c": cid})
            moved = conn.execute(sa.text("SELECT coalesce(sum(quantity), 0) FROM stock_movements WHERE product_id = :n"),
                                 {"n": new}).scalar()
            conn.execute(sa.text("UPDATE products SET stock = :q WHERE id = :n"), {"q": moved, "n": new})
            conn.execute(sa.text("UPDATE products SET stock = stock - :q WHERE id = :o"), {"q": moved, "o": old})
    for t in TABLES:
        with op.batch_alter_table(t) as b:
            b.alter_column("company_id", existing_type=sa.Integer(), nullable=False)
            b.create_foreign_key(f"fk_{t}_company", "companies", ["company_id"], ["id"])
            b.create_index(f"ix_{t}_company_id", ["company_id"])


def downgrade() -> None:
    for t in TABLES:
        with op.batch_alter_table(t) as b:
            b.drop_index(f"ix_{t}_company_id")
            b.drop_constraint(f"fk_{t}_company", type_="foreignkey")
            b.drop_column("company_id")
