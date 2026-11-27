"""Integracje Sygnavo: sklep internetowy (Shopify), Allegro i kurierzy (Apaczka).

Kazda integracja nalezy do jednej firmy (tabela integrations). Klucze i tokeny sa zaszyfrowane (secretbox).
Zamowienia ze sklepu i z Allegro trafiaja do Zamowien jako zwykle zamowienia z kanalem (channel). Sygnavo tylko
czyta zamowienia; nic nie wysyla klientom i nie zmienia nic w sklepie ani na Allegro bez klikniecia uzytkownika.
"""
