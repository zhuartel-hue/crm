# Kontrahenci i karta kontrahenta
Aktualizacja: 2026-11-27 (karta jak w Firmao: maile, SMS, oś czasu)

## Link do kontrahenta na każdej liście
Na listach faktur, należności, WZ, ofert, zamówień, umów, kosztów, zadań, połączeń, poczty, sprzętu i w kalendarzu
nazwa kontrahenta jest **pogrubiona i podkreślona**. Kliknięcie otwiera jego kartę. Tak samo na podglądzie faktury,
WZ, oferty, zamówienia i faktury kosztowej. Gdy dokument nie jest powiązany z kontrahentem z bazy (np. jednorazowy
nabywca), nazwa jest zwykłym tekstem.

## Co jest na karcie
- Na górze: nazwa, NIP, adres, telefon, e-mail i szybkie przyciski **+ Faktura**, **+ WZ**, **+ Oferta**, **E-mail**.
- Podsumowanie: **Do zapłaty** (wszystkie nieopłacone faktury), **Po terminie**, **Sprzedaż razem**.
- Pasek skrótów z liczbami, który przenosi do sekcji: Notatki, Faktury, WZ/PZ, Oferty, Zamówienia, Umowy,
  Zadania, Sprzęt, Koszty, Rozmowy, Maile, Osoby, Historia.
- Sekcje w kolejności: notatki, faktury, WZ i PZ (z kolumną zapłaty), maile, rozmowy telefoniczne, oferty,
  zamówienia, umowy, zadania, sprzęt, koszty (faktury od tego kontrahenta, także dopasowane po NIP), osoby
  kontaktowe, historia zmian (kto i kiedy wystawił, zmienił albo zapłacił dokumenty tego kontrahenta).
- Dane kontrahenta edytujesz po rozwinięciu **Dane kontrahenta (edycja)**.

## Dane jak w Firmao
Oprócz nazwy, NIP i adresu: **typ** (klient, dostawca, partner, podwykonawca, producent, inwestor, konkurencja, inny),
**REGON**, **KRS**, **numer konta**, **adres do korespondencji** i **następny kontakt**. Te pola wczytują się też
z Firmao przy ponownym imporcie. Na liście kontrahentów filtr **skontaktować się dziś** pokazuje tych, z którymi
kontakt wypada dziś lub wcześniej, a filtr typu zawęża listę np. do dostawców.

## Oś czasu
Pierwsza sekcja karty. Jedna lista, od najnowszych: notatki, maile, SMS-y, rozmowy, faktury, WZ, oferty,
zamówienia, zadania, koszty i zmiany z dziennika. Kliknięcie wpisu otwiera dokument.

## Maile z karty
- **Napisz e-mail** (na górze albo w sekcji Maile) otwiera formularz na karcie: skrzynka, adresat (podpowiedzi z
  adresów kontrahenta i jego osób), temat, treść, załączniki. Po wysłaniu wracasz na kartę. **Pełny edytor /
  szablony** otwiera zwykłe okno poczty.
- Lista ostatnich 25 maili. Kliknięcie rozwija treść, pokazuje załączniki do pobrania i przycisk **Odpowiedz**.
  Wszystkie wiadomości są pod **Cała historia**.
- Maile pojawiają się, gdy firma ma podłączoną skrzynkę (Ustawienia → Poczta). Program łączy je z kontrahentem po
  adresach e-mail kontrahenta i jego osób kontaktowych.

## SMS
Sekcja **SMS**: numer (podpowiedzi z telefonów kontrahenta i osób), treść do 640 znaków, **Wyślij SMS**. Każdy SMS
trafia do historii kontrahenta i na oś czasu. Dopóki bramka SMS (np. SMSAPI) nie jest podłączona, SMS ma status
„czeka na bramkę SMS” i nie wychodzi do odbiorcy. Po podłączeniu bramki oczekujące SMS-y zostaną wysłane.
