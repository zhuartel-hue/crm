"""Kazda firma ma swoich kontrahentow, osoby i produkty; migracja 0022 rozdziela wspolne dane."""
from decimal import Decimal

import sqlalchemy as sa


def _draft(db, company, contractor_id=None, product_id=None, source_ref=""):
    from app import services

    inv = services.create_invoice_draft(db, "test", {"company": company, "buyer": {"name": "Ktoś"},
                                                     "items": [{"name": "Rzecz", "quantity": 1, "unit_price": "10"}]})
    inv.contractor_id, inv.source_ref = contractor_id, source_ref
    if product_id:
        inv.items[0].product_id = product_id
    db.commit()
    return inv.id


def test_lists_show_only_current_company(client):
    client.post("/kontrahenci/nowy", data={"name": "Park Margot", "is_company": "1"})
    client.post("/produkty/nowy", data={"code": "PM-1", "name": "Produkt Margot", "unit": "szt.", "vat_rate": "23",
                                        "price_net": "1,00", "price_mode": "net", "active": "1"})
    assert "Park Margot" in client.get("/kontrahenci").text
    assert "Produkt Margot" in client.get("/produkty").text
    client.post("/firma/2")
    assert "Park Margot" not in client.get("/kontrahenci").text
    assert "Produkt Margot" not in client.get("/produkty").text
    assert "Park Margot" not in client.get("/faktury/nowa").text
    client.post("/kontrahenci/nowy", data={"name": "Klient Spółki", "is_company": "1"})
    assert "Klient Spółki" in client.get("/kontrahenci").text
    client.post("/firma/1")
    page = client.get("/kontrahenci").text
    assert "Park Margot" in page and "Klient Spółki" not in page
    # cudzy kontrahent pod adresem: 404
    from app.db import SessionLocal
    from app.models import Contractor
    with SessionLocal() as db:
        other = db.scalar(sa.select(Contractor).where(Contractor.name == "Klient Spółki"))
    assert client.get(f"/kontrahenci/{other.id}").status_code == 404


def test_migration_0022_splits_shared_records(client):
    from alembic import command
    from alembic.config import Config

    from app import migrate
    from app.db import SessionLocal, engine
    from app.models import Contact, Contractor, Product

    with SessionLocal() as db:
        shared = Contractor(company_id=1, name="Wspólny", source_ref="firmao:7")
        ari_only = Contractor(company_id=1, name="Tylko spółka")
        unused = Contractor(company_id=1, name="Nieużywany")
        prod = Product(company_id=1, name="Kabel", code="K", stock=Decimal("10"), source_ref="firmao:9")
        db.add_all([shared, ari_only, unused, prod])
        db.flush()
        db.add(Contact(company_id=1, contractor_id=shared.id, first_name="Ola"))
        db.commit()
        ids = shared.id, ari_only.id, unused.id, prod.id
    m1 = _draft(SessionLocal(), "Margot", ids[0], ids[3], source_ref="firmao:100")
    a1 = _draft(SessionLocal(), "ARI", ids[0], ids[3])
    a2 = _draft(SessionLocal(), "ARI", ids[1])

    cfg = Config(str(migrate.ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(migrate.ROOT / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0021")
        command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rows = {r.name: r for r in conn.execute(sa.text("SELECT id, name, company_id FROM contractors"))}
        inv = dict(conn.execute(sa.text("SELECT id, contractor_id FROM invoices")).fetchall())
        items = dict(conn.execute(sa.text("SELECT invoice_id, product_id FROM invoice_items")).fetchall())
        prods = conn.execute(sa.text("SELECT id, company_id FROM products ORDER BY id")).fetchall()
        contacts = conn.execute(sa.text("SELECT contractor_id, company_id FROM contacts ORDER BY id")).fetchall()
    assert rows["Nieużywany"].company_id == 1
    assert rows["Tylko spółka"].company_id == 2 and inv[a2] == rows["Tylko spółka"].id
    with engine.connect() as conn:
        copies = conn.execute(sa.text("SELECT id, company_id FROM contractors WHERE name = 'Wspólny' ORDER BY id")).fetchall()
    assert [c[1] for c in copies] == [1, 2]
    assert inv[m1] == copies[0][0] and inv[a1] == copies[1][0]
    assert [p[1] for p in prods] == [1, 2] and items[m1] == prods[0][0] and items[a1] == prods[1][0]
    assert sorted(contacts) == sorted([(copies[0][0], 1), (copies[1][0], 2)])
