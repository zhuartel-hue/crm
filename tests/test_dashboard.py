"""Edytowalna strona Start: wybor kafelkow, kolejnosc i rozmiar, osobno dla kazdej firmy."""


def test_default_layout_and_save_reset(client):
    page = client.get("/").text
    assert "Dostosuj" in page and "Zadania na dziś" in page and 'data-k="invoices"' in page
    r = client.post("/start/uklad", json={"items": [{"k": "invoices", "w": 4, "h": 5}, {"k": "stat_month", "w": 9, "h": 0},
                                                    {"k": "nieznany"}, {"k": "invoices", "w": 1, "h": 1}]})
    assert r.status_code == 200
    assert r.json()["items"] == [{"k": "invoices", "w": 4, "h": 5}, {"k": "stat_month", "w": 4, "h": 1}]
    page = client.get("/").text
    grid = page[page.index('id="dash"'):page.index('id="dash-hidden"')]
    assert 'data-k="invoices" data-w="4" data-h="5"' in grid and 'data-k="mails"' not in grid
    assert grid.index('data-k="invoices"') < grid.index('data-k="stat_month"')
    assert '<option value="mails">Ostatnie maile</option>' in page  # schowany kafelek do dodania
    # druga firma ma swoj (domyslny) uklad
    client.post("/firma/2")
    assert 'data-k="mails"' in client.get("/").text.split('id="dash-hidden"')[0]
    client.post("/firma/1")
    assert client.post("/start/uklad", json={"reset": True}).json()["ok"]
    assert 'data-k="mails"' in client.get("/").text.split('id="dash-hidden"')[0]


def test_table_columns_saved_per_user(client):
    assert client.get("/ustawienia/kolumny", params={"t": "/wz#0"}).json() == {}
    client.post("/ustawienia/kolumny", json={"t": "/wz#0", "order": [1, 0, 2, "x"], "widths": {"0": 10, "1": 250, "a": 3}})
    assert client.get("/ustawienia/kolumny", params={"t": "/wz#0"}).json() == {"order": [1, 0, 2], "widths": {"0": 40, "1": 250}}
    client.post("/ustawienia/kolumny", json={"t": "/wz#0", "reset": True})
    assert client.get("/ustawienia/kolumny", params={"t": "/wz#0"}).json() == {}
