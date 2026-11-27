# Magazyn: WZ i PZ
Aktualizacja: 2026-11-27

WZ to wydanie towaru albo usługi klientowi, PZ to przyjęcie od dostawcy (menu **Zakupy i magazyn → Magazyn WZ / PZ**).
Wystawienie WZ zmniejsza stan produktów, PZ go zwiększa. Usługi (np. dojazd, roboczogodziny) stanu nie zmieniają.

## Lista dokumentów
- Filtr obok tytułu: **WZ i PZ**, **tylko WZ**, **tylko PZ** albo **WZ bez faktury** (wystawione WZ, do których nie
  ma jeszcze faktury). Szukanie po numerze albo odbiorcy działa w trakcie pisania.
- Kolumna **Zapłata** pokazuje stan faktury do tej WZ:
  - **zapłacono**: faktura jest w całości opłacona,
  - **częściowo**: część wpłacona (kwota do zapłaty w podpowiedzi),
  - **nie zapłacono**: faktura wystawiona, brak wpłaty,
  - **szkic faktury**: faktura jest jeszcze szkicem,
  - **bez faktury**: do WZ nie ma faktury.
  Zapłatę zaznacza się na fakturze (ręcznie albo automatycznie z banku), WZ bierze ją z faktury.

## Faktura z jednej WZ
Na WZ kliknij **Wystaw fakturę z WZ**. Powstaje szkic faktury VAT z pozycjami i cenami z WZ.

## Jedna faktura z kilku WZ
1. Na liście zaznacz pola przy wystawionych WZ jednego odbiorcy (najwygodniej z filtrem **WZ bez faktury**).
2. Kliknij **Faktura z zaznaczonych WZ**.
3. Powstaje jeden szkic faktury VAT ze wszystkimi pozycjami, w kolejności dat WZ. W uwagach faktury są numery WZ.
4. Sprawdź szkic i wystaw fakturę. Do wysyłki klientowi i do KSeF faktura idzie dopiero po Twoim wystawieniu.

Zasady:
- WZ muszą mieć tego samego odbiorcę (tego samego kontrahenta z bazy albo tę samą nazwę) i być z tej samej firmy.
- Wszystkie WZ muszą mieć ceny netto albo wszystkie brutto.
- WZ, która ma już fakturę, nie ma pola do zaznaczenia.
- Na fakturze jest sama nazwa pozycji. Kod produktu zostaje wewnątrz Sygnavo.
- Po utworzeniu szkicu każda WZ pokazuje link do faktury. Gdy usuniesz szkic faktury, WZ znów są „bez faktury”.
