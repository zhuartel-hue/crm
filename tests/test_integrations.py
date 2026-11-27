"""Integracje: sklep Shopify, Allegro, kurier Apaczka (zapytania HTTP podmienione)."""
import base64
import json
from decimal import Decimal

import httpx
import pytest


class FakeHttp:
    def __init__(self):
        self.calls = []
        self.handlers = []

    def on(self, match, fn):
        self.handlers.append((match, fn))

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for match, fn in reversed(self.handlers):
            if match in url:
                res = fn(method, url, kw)
                status, body = res if isinstance(res, tuple) else (200, res)
                return httpx.Response(status, json=body, request=httpx.Request(method, url))
        raise AssertionError(f"Nieoczekiwane zapytanie {method} {url}")


@pytest.fixture()
def fake(monkeypatch):
    from app.integrations import http
    f = FakeHttp()
    monkeypatch.setattr(http, "request", f)
    return f


def _new(client, kind):
    r = client.post("/ustawienia/integracje/nowa", data={"kind": kind}, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


SHOP_ORDER = {
    "id": "gid://shopify/Order/1001", "name": "#1001", "createdAt": "2026-11-20T10:00:00Z",
    "updatedAt": "2026-11-20T10:05:00Z", "cancelledAt": None, "displayFinancialStatus": "PAID",
    "displayFulfillmentStatus": "UNFULFILLED", "email": "jan@example.com", "phone": None, "note": "Proszę szybko",
    "taxesIncluded": True, "customer": {"displayName": "Jan Nowak", "email": "jan@example.com", "phone": None},
    "billingAddress": {"name": "Jan Nowak", "company": "Nowak Sp. z o.o.", "address1": "ul. Długa 5", "address2": None,
                       "zip": "00-001", "city": "Warszawa", "countryCodeV2": "PL", "phone": "+48500600700"},
    "shippingAddress": {"name": "Jan Nowak", "company": None, "address1": "ul. Krótka 2", "address2": None,
                        "zip": "89-600", "city": "Chojnice", "countryCodeV2": "PL", "phone": "+48500600700"},
    "shippingLine": {"title": "InPost Paczkomat", "code": "inpost", "discountedPriceSet": {"shopMoney": {"amount": "12.99"}}},
    "lineItems": {"nodes": [
        {"name": "Switch PoE 8 portów", "sku": "SW-8", "quantity": 2,
         "discountedUnitPriceAfterAllDiscountsSet": {"shopMoney": {"amount": "246.00"}}, "taxLines": [{"rate": 0.23}],
         "variant": {"id": "gid://shopify/ProductVariant/55"}},
        {"name": "Kabel", "sku": "", "quantity": 1,
         "discountedUnitPriceAfterAllDiscountsSet": {"shopMoney": {"amount": "10.00"}}, "taxLines": [],
         "variant": None}]},
}


def test_shopify_orders_import(client, fake):
    from app.db import SessionLocal
    from app.models import Integration, Order, Product, ProductLink

    with SessionLocal() as db:
        db.add(Product(company_id=1, code="SW-8", name="Switch PoE 8p (magazyn)", price_net=Decimal("200")))
        db.commit()
    fake.on("/admin/oauth/access_token", lambda m, u, kw: {"access_token": "tok-123", "expires_in": 86399})
    orders = {"nodes": [SHOP_ORDER]}

    def gql(m, u, kw):
        assert kw["headers"]["X-Shopify-Access-Token"] == "tok-123"
        q = kw["json"]["query"]
        if "shop {" in q:
            return {"data": {"shop": {"name": "MARGOT"}}}
        return {"data": {"orders": {**orders, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
    fake.on("/graphql.json", gql)
    iid = _new(client, "shopify")
    assert "Jak zdobyć klucze" in client.get(f"/ustawienia/integracje/{iid}").text
    client.post(f"/ustawienia/integracje/{iid}/klucze",
                data={"shop": "https://bb97e0-8e.myshopify.com/", "client_id": "cid-abcdefghijk", "client_secret": "sek-1234567890"})
    with SessionLocal() as db:
        integ = db.get(Integration, iid)
        assert integ.state == "active" and integ.name == "MARGOT" and integ.settings["shop"] == "bb97e0-8e.myshopify.com"
        assert "sek-1234567890" not in integ.secret and "tok-123" not in integ.secret  # zaszyfrowane
    client.post(f"/ustawienia/integracje/{iid}/pobierz")
    client.post(f"/ustawienia/integracje/{iid}/pobierz")  # drugi raz bez dubli
    with SessionLocal() as db:
        rows = db.query(Order).filter(Order.integration_id == iid).all()
        assert len(rows) == 1
        z = rows[0]
        assert z.channel == "shopify" and z.customer_ref == "#1001" and z.payment_status == "paid"
        assert z.recipient_name == "Nowak Sp. z o.o." and z.contact_person == "Jan Nowak"
        assert "Chojnice" in z.delivery_address and z.shipping_method == "InPost Paczkomat"
        assert [i.name for i in z.items] == ["Switch PoE 8 portów", "Kabel", "Wysyłka: InPost Paczkomat"]
        assert z.items[0].product_id is not None and z.items[0].code == "SW-8" and z.prices_gross
        assert str(z.total_gross) == "514.99" and z.number.endswith("/Z/S")
        assert db.query(ProductLink).filter_by(integration_id=iid).count() == 1  # powiazanie z SKU
        zid = z.id
    page = client.get(f"/zamowienia/{zid}").text
    assert "Nadaj paczkę" in page and "opłacone" in page and "InPost Paczkomat" in page
    assert "sklep" in client.get("/zamowienia").text
    # anulowanie w sklepie -> status anulowane
    orders["nodes"] = [{**SHOP_ORDER, "cancelledAt": "2026-11-21T08:00:00Z", "updatedAt": "2026-11-21T08:00:00Z"}]
    client.post(f"/ustawienia/integracje/{iid}/pobierz")
    with SessionLocal() as db:
        assert db.get(Order, zid).status == "cancelled"
    # inna firma nie widzi integracji
    client.post("/firma/2")
    assert client.get(f"/ustawienia/integracje/{iid}").status_code == 404
    assert "MARGOT" not in client.get("/ustawienia/integracje").text
    client.post("/firma/1")


def test_shopify_bad_shop_and_error_shown(client, fake):
    from app.db import SessionLocal
    from app.models import Integration
    iid = _new(client, "shopify")
    client.post(f"/ustawienia/integracje/{iid}/klucze", data={"shop": "margotsystem.com", "client_id": "a", "client_secret": "b"})
    with SessionLocal() as db:
        assert "myshopify.com" in db.get(Integration, iid).last_error and db.get(Integration, iid).state == "new"
    fake.on("/admin/oauth/access_token", lambda m, u, kw: (400, {"error": "shop_not_permitted"}))
    client.post(f"/ustawienia/integracje/{iid}/klucze", data={"shop": "abc.myshopify.com", "client_id": "a", "client_secret": "b"})
    with SessionLocal() as db:
        assert "tej samej organizacji" in db.get(Integration, iid).last_error


ALLEGRO_FORM = {
    "id": "29b6c4d0-aaaa-bbbb-cccc-000000000001", "status": "READY_FOR_PROCESSING", "updatedAt": "2026-11-25T12:00:00.000Z",
    "buyer": {"login": "kupujacy1", "email": "k1@allegromail.pl", "firstName": "Anna", "lastName": "Kowal",
              "phoneNumber": "600100200", "address": {"street": "Polna 1", "city": "Gdańsk", "postCode": "80-001", "countryCode": "PL"}},
    "delivery": {"address": {"firstName": "Anna", "lastName": "Kowal", "street": "Polna 1", "city": "Gdańsk",
                             "zipCode": "80-001", "countryCode": "PL", "phoneNumber": "600100200"},
                 "method": {"id": "x", "name": "Allegro Paczkomaty InPost"},
                 "pickupPoint": {"id": "GDA01M", "name": "Paczkomat GDA01M", "address": {"street": "Morska 2", "zipCode": "80-002", "city": "Gdańsk"}},
                 "cost": {"amount": "9.99", "currency": "PLN"}},
    "invoice": {"required": True, "address": {"street": "Firmowa 3", "city": "Gdańsk", "zipCode": "80-003", "countryCode": "PL",
                                               "company": {"name": "Kowal IT", "taxId": "5551859956"}}},
    "payment": {"type": "ONLINE", "paidAmount": {"amount": "109.99", "currency": "PLN"}},
    "lineItems": [{"offer": {"id": "123456", "name": "Szafa rack 6U", "external": {"id": "RACK-6"}}, "quantity": 1,
                   "price": {"amount": "100.00", "currency": "PLN"}, "boughtAt": "2026-11-25T11:00:00.000Z"}],
    "summary": {"totalToPay": {"amount": "109.99", "currency": "PLN"}},
    "fulfillment": {"status": "NEW"},
}


def test_allegro_connect_orders_offers(client, fake):
    from app.db import SessionLocal
    from app.models import Contractor, Integration, Order, Product, ProductLink

    with SessionLocal() as db:
        db.add(Product(company_id=1, code="RACK-6", name="Szafa 6U", stock=Decimal("4")))
        db.add(Contractor(company_id=1, name="Kowal IT", nip="555-185-99-56"))
        db.commit()
    iid = _new(client, "allegro")
    client.post(f"/ustawienia/integracje/{iid}/klucze", data={"client_id": "allegro-client-id", "client_secret": "sekret-allegro"})
    r = client.post(f"/ustawienia/integracje/{iid}/allegro", follow_redirects=False)
    loc = r.headers["location"]
    assert loc.startswith("https://allegro.pl/auth/oauth/authorize?") and "client_id=allegro-client-id" in loc
    with SessionLocal() as db:
        state = db.get(Integration, iid).auth_state
    fake.on("allegro.pl/auth/oauth/token", lambda m, u, kw: {"access_token": "at", "refresh_token": "rt", "expires_in": 43199})
    fake.on("api.allegro.pl/me", lambda m, u, kw: {"login": "MARGOT_SKLEP"})
    forms = [ALLEGRO_FORM, {**ALLEGRO_FORM, "id": "bought-only", "status": "BOUGHT"}]
    fake.on("/order/checkout-forms", lambda m, u, kw: {"checkoutForms": forms, "count": len(forms)})
    fake.on("/sale/offers", lambda m, u, kw: {"offers": [
        {"id": "123456", "name": "Szafa rack 6U", "external": {"id": "RACK-6"}, "stock": {"available": 2},
         "sellingMode": {"price": {"amount": "100.00"}}, "publication": {"status": "ACTIVE"}},
        {"id": "999", "name": "Inna oferta", "external": None, "stock": {"available": 1},
         "sellingMode": {"price": {"amount": "50.00"}}, "publication": {"status": "ACTIVE"}}]})
    patched = []
    fake.on("/sale/product-offers/", lambda m, u, kw: patched.append(kw["json"]) or {})
    client.get(f"/ustawienia/integracje/allegro/powrot?code=abc&state={state}")
    with SessionLocal() as db:
        integ = db.get(Integration, iid)
        assert integ.state == "active" and integ.name == "MARGOT_SKLEP"
    client.post(f"/ustawienia/integracje/{iid}/pobierz")
    with SessionLocal() as db:
        rows = db.query(Order).filter(Order.integration_id == iid).all()
        assert len(rows) == 1  # BOUGHT bez ostatecznych danych pominiete
        z = rows[0]
        assert z.channel == "allegro" and z.invoice_requested and z.buyer_tax_id == "5551859956"
        assert z.contractor_id is not None and z.recipient_name == "Kowal IT" and "GDA01M" in z.pickup_point
        assert z.items[0].product_id is not None and str(z.total_gross) == "109.99" and z.payment_status == "paid"
    client.post(f"/ustawienia/integracje/{iid}/produkty", data={"action": "sync"})
    with SessionLocal() as db:
        links = {l.external_id: l for l in db.query(ProductLink).filter_by(integration_id=iid)}
        assert links["123456"].product_id is not None and links["999"].product_id is None
        lid = links["123456"].id
    assert "Szafa rack 6U" in client.get(f"/ustawienia/integracje/{iid}/produkty").text
    client.post(f"/ustawienia/integracje/{iid}/produkty", data={"action": "stock", "link_id": str(lid)})
    assert patched == [{"stock": {"available": 4, "unit": "UNIT"}}]
    client.post(f"/ustawienia/integracje/{iid}/produkty", data={"action": "create"})
    with SessionLocal() as db:
        assert db.query(Product).filter_by(company_id=1, name="Inna oferta").count() == 1
    # zly state z powrotu
    r = client.get("/ustawienia/integracje/allegro/powrot?code=x&state=zly", follow_redirects=False)
    assert r.headers["location"] == "/ustawienia/integracje"


def test_apaczka_signature():
    from app.integrations import apaczka
    import hashlib
    import hmac
    sig = apaczka.signature("id1", "order_send/", '{"a":1}', 1700000000, "sekret")
    assert sig == hmac.new(b"sekret", b'id1:order_send/:{"a":1}:1700000000', hashlib.sha256).hexdigest()
    assert apaczka.money(1220) == Decimal("12.20") and apaczka.money("9.92") == Decimal("9.92")


def test_apaczka_shipment_flow(client, fake):
    from app.db import SessionLocal
    from app.models import Integration, Shipment
    from test_orders import _order

    pdf = b"%PDF-1.4 etykieta"

    def api(m, u, kw):
        route = u.split("/api/v2/")[1]
        d = kw["data"]
        assert d["app_id"] == "APP1" and len(d["signature"]) == 64
        if route == "service_structure/":
            return {"status": 200, "response": {"services": [{"service_id": 41, "name": "InPost Paczkomat"},
                                                              {"service_id": 21, "name": "DPD Classic"}]}}
        if route == "order_valuation/":
            return {"status": 200, "response": {"price_table": {"41": {"price": 992, "price_gross": 1220},
                                                                "21": {"price": 1500, "price_gross": 1845}}}}
        if route == "order_send/":
            req = json.loads(d["request"])
            assert req["order"]["service_id"] == 41 and req["order"]["address"]["receiver"]["postal_code"] == "89-600"
            return {"status": 200, "response": {"order": {"id": 777, "waybill_number": "6200001", "status": "NEW",
                                                           "tracking_url": "https://apaczka.pl/sledz/6200001"}}}
        if route == "waybill/777/":
            return {"status": 200, "response": {"waybill": base64.b64encode(pdf).decode(), "type": "pdf"}}
        if route == "tracking/6200001/":
            return {"status": 200, "response": {"data": [{"status": "DELIVERED", "description": "Doręczona",
                                                          "updated_at": "2026-11-28 10:00"}]}}
        return {"status": 400, "message": "nieznane"}
    fake.on("apaczka.pl/api/v2/", api)
    zid = _order(client)
    assert "Podłącz Apaczkę" in client.get(f"/zamowienia/{zid}/paczka").text
    iid = _new(client, "apaczka")
    client.post(f"/ustawienia/integracje/{iid}/klucze", data={"app_id": "APP1", "app_secret": "SEKRET", "sender_name": "Firma",
                                                             "sender_postal_code": "89-600", "sender_city": "Chojnice"})
    with SessionLocal() as db:
        assert db.get(Integration, iid).state == "active"
    form = {"r_name": "Jan Kowalski", "r_line1": "ul. Krótka 1", "r_postal_code": "89-600", "r_city": "Chojnice",
            "r_phone": "600100200", "p_l": "30", "p_w": "20", "p_h": "10", "p_kg": "2"}
    page = client.post(f"/zamowienia/{zid}/paczka", data={**form, "action": "price"}).text
    assert "InPost Paczkomat" in page and "12,20" in page and "Zamów kuriera" in page
    r = client.post(f"/zamowienia/{zid}/paczka", data={**form, "action": "send", "service_id": "41",
                                                        "service_name": "InPost Paczkomat"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        s = db.query(Shipment).filter_by(order_id=zid).one()
        assert s.waybill == "6200001" and s.label == pdf and s.state == "ordered"
        sid = s.id
    assert "6200001" in client.get(f"/zamowienia/{zid}").text
    lab = client.get(f"/zamowienia/przesylki/{sid}/etykieta")
    assert lab.headers["content-type"] == "application/pdf" and lab.content == pdf
    from app.integrations import service
    with SessionLocal() as db:
        service.track_open(db)
    with SessionLocal() as db:
        assert db.get(Shipment, sid).state == "delivered"
    client.post("/firma/2")
    assert client.get(f"/zamowienia/przesylki/{sid}/etykieta").status_code == 404
    client.post("/firma/1")


def test_handlowiec_cannot_open_integrations():
    from app import users
    assert not users.can_open("handlowiec", "/ustawienia/integracje")
    assert users.can_open("magazyn", "/zamowienia/5/paczka")
