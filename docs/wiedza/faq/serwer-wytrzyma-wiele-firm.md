# Czy program wytrzyma, gdy zarejestruje się kilkaset firm?
Moduł: serwer · Aktualizacja: 2026-11-27

Tak. Program do faktur generuje mały ruch: cała baza dwóch firm z kilkoma tysiącami faktur i zdjęciami
zajmuje kilkadziesiąt MB. Serwer powiększamy stopniowo (więcej procesora, pamięci i dysku) w kilka minut,
bez utraty danych i z kopią przed każdą zmianą. Przy kilkuset firmach baza dostaje osobny serwer, a
program działa na dwóch serwerach jednocześnie. Dane każdej firmy są oddzielone, więc żaden klient nie
widzi danych innego.
