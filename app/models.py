"""Model danych: firmy, kontrahenci, auta, faktury."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

Money = Numeric(14, 2)


class Company(Base):
    """Firma wystawiajaca faktury (Margot, ARI ERDMANN WORLDS)."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Organizacja (klient abonamentu), do ktorej nalezy firma. Etap B: NULL tylko w starej bazie do migracji 0100.
    organisation_id: Mapped[int | None] = mapped_column(ForeignKey("organisations.id"), nullable=True, index=True)
    short_name: Mapped[str] = mapped_column(String(40))  # do przelacznika, np. "Margot"
    name: Mapped[str] = mapped_column(String(300))  # pelna nazwa na fakturze
    nip: Mapped[str] = mapped_column(String(20), default="")
    street: Mapped[str] = mapped_column(String(200), default="")
    postal_code: Mapped[str] = mapped_column(String(10), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    bank_name: Mapped[str] = mapped_column(String(100), default="")
    bank_account: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    issue_place: Mapped[str] = mapped_column(String(100), default="")
    # Wzorce numeracji: {nr} kolejny numer, {MM} miesiac, {RRRR} rok.
    pattern_vat: Mapped[str] = mapped_column(String(60), default="FV/{nr}/{MM}/{RRRR}")
    # Nieuzywane (faktura marza usunieta 2026-11-25); kolumna zostaje w bazie.
    pattern_margin: Mapped[str] = mapped_column(String(60), default="FM/{nr}/{MM}/{RRRR}")
    pattern_proforma: Mapped[str] = mapped_column(String(60), default="PRO/{nr}/{MM}/{RRRR}")
    pattern_correction: Mapped[str] = mapped_column(String(60), default="FK/{nr}/{MM}/{RRRR}")
    pattern_advance: Mapped[str] = mapped_column(String(60), default="FZ/{nr}/{MM}/{RRRR}")
    pattern_wz: Mapped[str] = mapped_column(String(60), default="WZ/{nr}/{MM}/{RRRR}")
    pattern_pz: Mapped[str] = mapped_column(String(60), default="PZ/{nr}/{MM}/{RRRR}")
    pattern_offer: Mapped[str] = mapped_column(String(60), default="OF/{nr}/{MM}/{RRRR}")
    # "month" - numeracja od 1 co miesiac, "year" - co rok.
    numbering_reset: Mapped[str] = mapped_column(String(10), default="month")
    default_payment_days: Mapped[int] = mapped_column(Integer, default=7)
    # Wyglad faktury PDF: logo (PNG/JPEG) i wzor (INVOICE_TEMPLATES).
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    logo_type: Mapped[str] = mapped_column(String(20), default="")
    invoice_template: Mapped[str] = mapped_column(String(20), default="klasyczny", server_default="klasyczny")
    # KSeF: "off" (wylaczony), "test", "demo", "prod"; token KSeF zaszyfrowany kluczem z SECRET_KEY (app/secretbox.py).
    ksef_env: Mapped[str] = mapped_column(String(10), default="off")
    ksef_token_enc: Mapped[str] = mapped_column(Text, default="")
    # Logowanie: "token" albo "cert" (certyfikat KSeF: .crt jawnie, klucz prywatny zaszyfrowany secretboxem).
    ksef_auth: Mapped[str] = mapped_column(String(10), default="token")
    ksef_cert_pem: Mapped[str] = mapped_column(Text, default="")
    ksef_key_enc: Mapped[str] = mapped_column(Text, default="")
    # Faktury kosztowe z KSeF: data trwalego zapisu ostatniej pobranej (ISO), od niej kolejne pobranie.
    ksef_purchase_cursor: Mapped[str] = mapped_column(String(40), default="")
    ksef_purchase_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ksef_purchase_error: Mapped[str] = mapped_column(Text, default="")

    @property
    def address_lines(self) -> list[str]:
        return [x for x in (self.street, f"{self.postal_code} {self.city}".strip()) if x]


class OfferTemplate(Base):
    """Wzor oferty: gotowy tytul, tresc i pozycje, od ktorych zaczyna sie nowa oferte."""

    __tablename__ = "offer_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(300), default="")  # wewnetrzny tytul (dla zespolu)
    notes: Mapped[str] = mapped_column(Text, default="")
    prices_gross: Mapped[bool] = mapped_column(default=False)
    valid_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


OFFER_PDF_TEMPLATES = {"katalog": "Katalog – duże zdjęcia i opisy", "galeria": "Galeria – kafelki ze zdjęciami",
                       "zwiezla": "Zwięzła – elegancka tabela bez zdjęć",
                       "prezentacja": "Prezentacja – strona tytułowa i zestawienie"}
INVOICE_TEMPLATES = {"klasyczny": "Klasyczny", "nowoczesny": "Nowoczesny (niebieski)", "elegancki": "Elegancki"}


class Contractor(Base):
    """Kontrahent jednej firmy (kazda firma ma swoja baze klientow)."""

    __tablename__ = "contractors"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", name="fk_contractors_company"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    is_company: Mapped[bool] = mapped_column(default=True)  # False = osoba prywatna
    nip: Mapped[str] = mapped_column(String(20), default="")
    pesel: Mapped[str] = mapped_column(String(11), default="")
    street: Mapped[str] = mapped_column(String(200), default="")
    postal_code: Mapped[str] = mapped_column(String(10), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    country: Mapped[str] = mapped_column(String(2), default="PL")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(String(200), default="")
    customer_type: Mapped[str] = mapped_column(String(20), default="", server_default="")  # CUSTOMER_TYPES
    regon: Mapped[str] = mapped_column(String(20), default="", server_default="")
    krs: Mapped[str] = mapped_column(String(20), default="", server_default="")
    bank_account: Mapped[str] = mapped_column(String(40), default="", server_default="")
    corr_address: Mapped[str] = mapped_column(String(300), default="", server_default="")  # adres korespondencyjny
    next_contact: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    # Faktury dla tego kontrahenta ida do KSeF od razu po wystawieniu (takze cykliczne).
    ksef_auto: Mapped[bool] = mapped_column(default=False, server_default=sa_false())
    # Skad pochodzi (np. "firmao:123"), do ponownego importu bez dubli.
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    contacts: Mapped[list["Contact"]] = relationship(back_populates="contractor", order_by="Contact.last_name")
    note_rows: Mapped[list["ContractorNote"]] = relationship(back_populates="contractor", cascade="all, delete-orphan",
                                                            order_by="ContractorNote.at.desc()")

    @property
    def address_lines(self) -> list[str]:
        return [x for x in (self.street, f"{self.postal_code} {self.city}".strip()) if x]

    @property
    def customer_type_label(self) -> str:
        return CUSTOMER_TYPES.get(self.customer_type, self.customer_type)


CUSTOMER_TYPES = {"CUSTOMER": "Klient", "SUPPLIER": "Dostawca", "PARTNER": "Partner", "SUBCONTRACTOR": "Podwykonawca",
                  "PRODUCER": "Producent", "INVESTOR": "Inwestor", "COMPETITION": "Konkurencja", "OTHER": "Inny"}

SMS_STATUSES = {"waiting": "czeka na bramkę SMS", "sent": "wysłany", "error": "błąd", "received": "odebrany"}


class SmsMessage(Base):
    """SMS do (albo od) kontrahenta. Bez podlaczonej bramki zostaje zapisany jako 'waiting' i widoczny w historii."""

    __tablename__ = "sms_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True,
                                                      index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    direction: Mapped[str] = mapped_column(String(3), default="out")
    phone: Mapped[str] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="waiting")
    provider_ref: Mapped[str] = mapped_column(String(100), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    contractor: Mapped[Contractor | None] = relationship()

    @property
    def status_label(self) -> str:
        return SMS_STATUSES.get(self.status, self.status)


class Contact(Base):
    """Osoba kontaktowa jednej firmy, zwykle pracownik klienta."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", name="fk_contacts_company"), index=True)
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"),
                                                      nullable=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    position: Mapped[str] = mapped_column(String(150), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    phone2: Mapped[str] = mapped_column(String(40), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    contractor: Mapped[Contractor | None] = relationship(back_populates="contacts")

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.email or f"Osoba #{self.id}"


class ContractorNote(Base):
    """Notatka przy kontrahencie (jak notatki handlowe w Firmao): data, kto, tresc."""

    __tablename__ = "contractor_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    contractor_id: Mapped[int] = mapped_column(ForeignKey("contractors.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime)
    author: Mapped[str] = mapped_column(String(100), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    contractor: Mapped[Contractor] = relationship(back_populates="note_rows")
    contact: Mapped[Contact | None] = relationship()


CAR_STATUSES = {"available": "Na placu", "reserved": "Zarezerwowany", "sold": "Sprzedany"}


class Car(Base):
    """Auto na placu z cena zakupu (do liczenia zysku na aucie)."""

    __tablename__ = "cars"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    make: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(80), default="")
    vin: Mapped[str] = mapped_column(String(17), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mileage_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registration: Mapped[str] = mapped_column(String(15), default="")
    purchase_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    purchase_document: Mapped[str] = mapped_column(String(120), default="")  # np. umowa kupna, faktura VAT-marza
    asking_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="available")
    notes: Mapped[str] = mapped_column(Text, default="")

    company: Mapped[Company] = relationship()

    @property
    def label(self) -> str:
        parts = [self.make, self.model]
        if self.year:
            parts.append(str(self.year))
        return " ".join(p for p in parts if p)

    @property
    def status_label(self) -> str:
        return CAR_STATUSES.get(self.status, self.status)


INVOICE_KINDS = {
    "vat": "Faktura VAT",
    "proforma": "Faktura proforma",
    "advance": "Faktura zaliczkowa",
    "final": "Faktura końcowa",
    "correction": "Faktura korygująca",
}
# Rodzaje, ktore mozna zalozyc od zera (korekte robi sie z wystawionej faktury, koncowa z zaliczkowej).
NEW_KINDS = ("vat", "proforma", "advance")
# Serie numeracji: faktura koncowa idzie w serii zwyklych faktur VAT.
NUMBER_SERIES = {"vat": "vat", "final": "vat", "proforma": "proforma", "advance": "advance", "correction": "correction"}
VAT_RATES = ["23", "8", "5", "0", "zw", "np"]
PAYMENT_METHODS = {"transfer": "Przelew", "cash": "Gotówka", "card": "Karta", "compensation": "Kompensata"}


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_invoice_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(String(10))  # INVOICE_KINDS
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft / issued
    number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    sale_date: Mapped[dt.date] = mapped_column(Date)
    issue_place: Mapped[str] = mapped_column(String(100), default="")
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    payment_method: Mapped[str] = mapped_column(String(15), default="transfer")
    paid: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    prices_gross: Mapped[bool] = mapped_column(default=True)  # ceny pozycji wpisane brutto

    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id"), nullable=True)
    # Kopia danych nabywcy z chwili wystawienia (faktura sie nie zmienia, gdy zmienimy kontrahenta).
    buyer_name: Mapped[str] = mapped_column(String(300), default="")
    buyer_nip: Mapped[str] = mapped_column(String(20), default="")
    buyer_address: Mapped[str] = mapped_column(Text, default="")

    car_id: Mapped[int | None] = mapped_column(ForeignKey("cars.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    # Korekta: faktura korygowana. Faktura VAT z proformy: ta proforma.
    related_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    correction_reason: Mapped[str] = mapped_column(Text, default="")
    # Zaliczkowa: otrzymana zaliczka brutto (wpisywana) i wartosc calego zamowienia brutto (z pozycji).
    advance_gross: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    order_total: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # Zaliczkowa: faktura koncowa, ktora ja rozlicza.
    settled_by_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)

    total_net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    # Stawka "zw": podstawa prawna zwolnienia (FA(3) P_19A).
    exemption_basis: Mapped[str] = mapped_column(String(256), default="")
    # Mechanizm podzielonej platnosci (FA(3) P_18A).
    split_payment: Mapped[bool] = mapped_column(default=False)

    # KSeF: status "" (nie wysylano) / "sent" (w trakcie) / "accepted" / "rejected" / "error"
    ksef_status: Mapped[str] = mapped_column(String(10), default="")
    ksef_env: Mapped[str] = mapped_column(String(10), default="")
    ksef_number: Mapped[str] = mapped_column(String(40), default="")
    ksef_session_ref: Mapped[str] = mapped_column(String(40), default="")
    ksef_invoice_ref: Mapped[str] = mapped_column(String(40), default="")
    ksef_message: Mapped[str] = mapped_column(Text, default="")
    ksef_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # Wyslany XML (do kodu QR liczy sie skrot dokladnie tego pliku) i UPO.
    ksef_xml: Mapped[str] = mapped_column(Text, default="")
    ksef_hash: Mapped[str] = mapped_column(String(64), default="")
    ksef_upo: Mapped[str] = mapped_column(Text, default="")

    # Faktura wystawiona z faktury cyklicznej.
    recurring_id: Mapped[int | None] = mapped_column(ForeignKey("recurring_invoices.id", ondelete="SET NULL"),
                                                     nullable=True, index=True)
    # Przypomnienia o platnosci wyslane mailem.
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    # Nieuzywane (dawna faktura marza); kolumna zostaje w bazie.
    margin_total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)  # np. "firmao:123"
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()
    contractor: Mapped[Contractor | None] = relationship()
    car: Mapped[Car | None] = relationship()
    items: Mapped[list[InvoiceItem]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.position"
    )
    related: Mapped[Invoice | None] = relationship(remote_side="Invoice.id", foreign_keys=[related_id])
    settled_by: Mapped[Invoice | None] = relationship(
        remote_side="Invoice.id", foreign_keys=[settled_by_id], back_populates="advances"
    )
    advances: Mapped[list[Invoice]] = relationship(
        foreign_keys=[settled_by_id], back_populates="settled_by", order_by="Invoice.issue_date"
    )
    payments: Mapped[list["InvoicePayment"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoicePayment.date"
    )

    @property
    def days_overdue(self) -> int:
        if self.due_date is None or self.to_pay <= 0:
            return 0
        return max(0, (dt.date.today() - self.due_date).days)

    @property
    def before_items(self) -> list[InvoiceItem]:
        """Korekta: pozycje przed korekta (stan z faktury korygowanej)."""
        return [i for i in self.items if i.is_before]

    @property
    def after_items(self) -> list[InvoiceItem]:
        """Pozycje faktury; w korekcie stan po korekcie."""
        return [i for i in self.items if not i.is_before]

    @property
    def kind_label(self) -> str:
        return INVOICE_KINDS.get(self.kind, self.kind)

    @property
    def payment_label(self) -> str:
        return PAYMENT_METHODS.get(self.payment_method, self.payment_method)

    @property
    def is_accounting(self) -> bool:
        """Proforma nie jest faktura w rozumieniu VAT (nie liczy sie do sprzedazy, nie idzie do KSeF)."""
        return self.kind != "proforma"

    @property
    def to_pay(self) -> Decimal:
        return (self.total_gross or Decimal("0")) - (self.paid or Decimal("0"))

    @property
    def display_number(self) -> str:
        return self.number or f"Szkic #{self.id}"


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"))
    unit: Mapped[str] = mapped_column(String(10), default="szt.")
    unit_price: Mapped[Decimal] = mapped_column(Money)  # netto albo brutto, wg invoice.prices_gross
    vat_rate: Mapped[str] = mapped_column(String(3), default="23")
    net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    # Nieuzywane (dawna faktura marza); kolumny zostaja w bazie.
    purchase_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    margin: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # Korekta: True dla pozycji "przed korekta" (kopia z faktury korygowanej).
    is_before: Mapped[bool] = mapped_column(default=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))

    invoice: Mapped[Invoice] = relationship(back_populates="items")
    product: Mapped["Product | None"] = relationship()


class NumberSequence(Base):
    """Licznik numerow: osobny dla firmy, rodzaju faktury i okresu."""

    __tablename__ = "number_sequences"
    __table_args__ = (UniqueConstraint("company_id", "kind", "period", name="uq_sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(String(10))
    period: Mapped[str] = mapped_column(String(7))  # "2027" albo "2027-01"
    last: Mapped[int] = mapped_column(Integer, default=0)


# --- dostep dla Claude (OAuth, klucze API) i dziennik zmian -----------------

SCOPES = {
    "read": "Odczyt faktur, aut i kontrahentów",
    "write": "Szkice faktur, nowe auta i kontrahenci, płatności",
    "issue": "Wystawianie faktur (nadanie numeru)",
}


class OAuthClient(Base):
    """Aplikacja zarejestrowana przez OAuth (np. konektor Claude)."""

    __tablename__ = "oauth_clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    redirect_uris: Mapped[str] = mapped_column(Text, default="")  # jeden adres w linii
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class AuthCode(Base):
    __tablename__ = "oauth_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    client_id: Mapped[str] = mapped_column(String(64))
    redirect_uri: Mapped[str] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(String(128))
    scopes: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(default=False)
    organisation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ApiToken(Base):
    """Token dostepu (access/refresh z OAuth albo staly klucz API). W bazie tylko skrot SHA-256."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # access / refresh / key
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grant_id: Mapped[str] = mapped_column(String(32), default="")  # wspolny dla access i refresh z jednej zgody
    # Organizacja, ktorej dane widzi token (etap B). NULL tylko w tokenach sprzed migracji 0100.
    organisation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scopes: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def scope_list(self) -> list[str]:
        return self.scopes.split()


class AuditLog(Base):
    """Kto, co i kiedy zmienil (www albo Claude/API)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(60))
    company_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity: Mapped[str] = mapped_column(String(30), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")


class Product(Base):
    """Produkt z katalogu jednej firmy: kod, cena, VAT, opis i zdjecia."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", name="fk_products_company"), index=True)
    code: Mapped[str] = mapped_column(String(60), default="", index=True)
    name: Mapped[str] = mapped_column(String(300))
    unit: Mapped[str] = mapped_column(String(20), default="szt.")
    price_net: Mapped[Decimal | None] = mapped_column(Money)
    vat_rate: Mapped[str] = mapped_column(String(5), default="23")
    ean: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)
    # Usluga (dojazd, roboczogodziny...) nie ma stanu magazynowego.
    is_service: Mapped[bool] = mapped_column(default=False)
    stock: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    # Skad pochodzi (np. "firmao:123", "shopify:gid"), do ponownego importu bez dubli.
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    photos: Mapped[list["ProductPhoto"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ProductPhoto.position",
    )

    @property
    def price_gross(self) -> Decimal | None:
        if self.price_net is None:
            return None
        pct = Decimal(self.vat_rate) if self.vat_rate.isdigit() else Decimal("0")
        return (self.price_net * (100 + pct) / 100).quantize(Decimal("0.01"))

    @property
    def label(self) -> str:
        return f"{self.code} · {self.name}" if self.code else self.name


class ProductPhoto(Base):
    """Zdjecie produktu w bazie (jest w nocnej kopii razem z danymi). JPEG max 1600 px + miniatura."""

    __tablename__ = "product_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    filename: Mapped[str] = mapped_column(String(200), default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    thumb: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="photos")


CONTRACT_FILE_KINDS = {"contract": "umowa", "annex": "aneks", "termination": "wypowiedzenie", "protocol": "protokół",
                       "other": "inny dokument"}


class ContractFile(Base):
    """Skan umowy, aneksu itd. dodany recznie do umowy. Plik w bazie, wiec jest w nocnej kopii razem z danymi."""

    __tablename__ = "contract_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    recurring_id: Mapped[int] = mapped_column(ForeignKey("recurring_invoices.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12), default="contract")  # CONTRACT_FILE_KINDS
    title: Mapped[str] = mapped_column(String(200), default="")
    doc_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    filename: Mapped[str] = mapped_column(String(200), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    added_by: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def kind_label(self) -> str:
        return CONTRACT_FILE_KINDS.get(self.kind, self.kind)


class ContractorFile(Base):
    """Plik dodany recznie na karcie kontrahenta (skan, zdjecie, dokument). Plik w bazie, wiec jest w kopii."""

    __tablename__ = "contractor_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    contractor_id: Mapped[int] = mapped_column(ForeignKey("contractors.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(200), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    added_by: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class AppSetting(Base):
    """Ustawienia programu zmieniane w aplikacji (np. skrot hasla administratora)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


BANK_STATES = ("auto", "manual", "suggested", "none", "ignored", "out")


class BankTransaction(Base):
    """Operacja z wyciagu bankowego (MT940). Wplyw moze byc zaksiegowany na fakture."""

    __tablename__ = "bank_transactions"
    __table_args__ = (
        UniqueConstraint("company_id", "fingerprint", name="uq_bank_tx_fingerprint"),
        Index("ix_bank_tx_company_date", "company_id", "booking_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    account: Mapped[str] = mapped_column(String(40), default="")
    booking_date: Mapped[dt.date] = mapped_column(Date)
    value_date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)  # dodatnia = wplyw
    currency: Mapped[str] = mapped_column(String(3), default="PLN")
    title: Mapped[str] = mapped_column(Text, default="")
    counterparty: Mapped[str] = mapped_column(String(300), default="")
    counterparty_account: Mapped[str] = mapped_column(String(40), default="")
    op_code: Mapped[str] = mapped_column(String(5), default="")
    bank_ref: Mapped[str] = mapped_column(String(40), default="")
    fingerprint: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(200), default="")
    state: Mapped[str] = mapped_column(String(10), default="none")  # BANK_STATES
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    applied_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()
    invoice: Mapped[Invoice | None] = relationship()


BANK_CONNECTION_STATES = ("pending", "active", "expired", "error", "closed")


class BankConnection(Base):
    """Polaczenie firmy z bankiem przez posrednika PSD2 (Enable Banking). Wlasciciel zgody: firma.

    session_id to identyfikator zgody u posrednika (nie jest haslem; klucz aplikacji jest w app_settings, zaszyfrowany).
    """

    __tablename__ = "bank_connections"
    __table_args__ = (Index("ix_bank_connections_company", "company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    provider: Mapped[str] = mapped_column(String(20), default="enablebanking")
    aspsp_name: Mapped[str] = mapped_column(String(120), default="")
    aspsp_country: Mapped[str] = mapped_column(String(2), default="PL")
    psu_type: Mapped[str] = mapped_column(String(10), default="business")
    auth_state: Mapped[str] = mapped_column(String(64), default="")
    session_id: Mapped[str] = mapped_column(String(80), default="")
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # [{"uid": "...", "iban": "PL..", "name": "...", "currency": "PLN"}]
    accounts_json: Mapped[str] = mapped_column(Text, default="[]")
    state: Mapped[str] = mapped_column(String(10), default="pending")  # BANK_CONNECTION_STATES
    last_sync: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    synced_until: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()

    @property
    def accounts(self) -> list[dict]:
        import json

        try:
            return json.loads(self.accounts_json or "[]")
        except ValueError:
            return []

    @property
    def days_left(self) -> int | None:
        if self.valid_until is None:
            return None
        return (self.valid_until - dt.datetime.now(dt.UTC).replace(tzinfo=None)).days


class MailAccount(Base):
    """Skrzynka pocztowa firmy (IMAP do odbioru, SMTP do wysylki). Haslo zaszyfrowane (app/secretbox.py)."""

    __tablename__ = "mail_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    email: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    imap_host: Mapped[str] = mapped_column(String(200), default="")
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    smtp_host: Mapped[str] = mapped_column(String(200), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=465)
    smtp_security: Mapped[str] = mapped_column(String(10), default="ssl")  # ssl / starttls
    username: Mapped[str] = mapped_column(String(200), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    signature: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)
    # {"INBOX": {"validity": "...", "last": 123}, "Sent": {...}}
    sync_state: Mapped[str] = mapped_column(Text, default="{}")
    last_sync: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()

    @property
    def label(self) -> str:
        return f"{self.display_name} <{self.email}>" if self.display_name else self.email


class MailMessage(Base):
    __tablename__ = "mail_messages"
    __table_args__ = (
        Index("ix_mail_account_date", "account_id", "date"),
        Index("ix_mail_contractor", "contractor_id"),
        Index("ix_mail_invoice", "invoice_id"),
        Index("ix_mail_msgid", "account_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String(3), default="in")  # in / out
    folder: Mapped[str] = mapped_column(String(200), default="")
    uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_id: Mapped[str] = mapped_column(String(300), default="")
    in_reply_to: Mapped[str] = mapped_column(String(300), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    from_addr: Mapped[str] = mapped_column(String(200), default="")
    from_name: Mapped[str] = mapped_column(String(200), default="")
    to_addrs: Mapped[str] = mapped_column(Text, default="")
    cc_addrs: Mapped[str] = mapped_column(Text, default="")
    date: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    body_text: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    seen: Mapped[bool] = mapped_column(default=False)
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped[MailAccount] = relationship()
    contractor: Mapped[Contractor | None] = relationship()
    invoice: Mapped[Invoice | None] = relationship()
    attachments: Mapped[list[MailAttachment]] = relationship(
        back_populates="message", cascade="all, delete-orphan", order_by="MailAttachment.id"
    )

    @property
    def snippet(self) -> str:
        return " ".join((self.body_text or "").split())[:160]


class MailAttachment(Base):
    __tablename__ = "mail_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("mail_messages.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(300), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # None = za duzy, nie pobrany

    message: Mapped[MailMessage] = relationship(back_populates="attachments")


DELIVERY_KINDS = {"wz": "WZ – wydanie zewnętrzne", "pz": "PZ – przyjęcie zewnętrzne"}


class Delivery(Base):
    """Dokument magazynowy: WZ (wydanie towaru odbiorcy, z podpisem) albo PZ (przyjecie od dostawcy)."""

    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_delivery_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(String(3), default="wz")  # DELIVERY_KINDS
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft / issued
    number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True)
    prices_gross: Mapped[bool] = mapped_column(default=False)
    total_net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    recipient_name: Mapped[str] = mapped_column(String(300), default="")
    recipient_address: Mapped[str] = mapped_column(Text, default="")
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    signed_by: Mapped[str] = mapped_column(String(200), default="")
    signature_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    signed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)  # np. "firmao:123"
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()
    contractor: Mapped[Contractor | None] = relationship()
    invoice: Mapped[Invoice | None] = relationship()
    items: Mapped[list[DeliveryItem]] = relationship(
        back_populates="delivery", cascade="all, delete-orphan", order_by="DeliveryItem.position"
    )

    @property
    def kind_short(self) -> str:
        return (self.kind or "wz").upper()

    @property
    def display_number(self) -> str:
        return self.number or f"Szkic {self.kind_short} #{self.id}"


class DeliveryItem(Base):
    __tablename__ = "delivery_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("deliveries.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    code: Mapped[str] = mapped_column(String(60), default="")
    name: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"))
    unit: Mapped[str] = mapped_column(String(10), default="szt.")
    unit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)  # netto albo brutto wg delivery.prices_gross
    vat_rate: Mapped[str] = mapped_column(String(3), default="23")
    net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    delivery: Mapped[Delivery] = relationship(back_populates="items")
    product: Mapped["Product | None"] = relationship()


# Statusy 1:1 jak w Firmao (slownik offer_status), w tej samej kolejnosci.
OFFER_STATUSES = {"incomplete": "niekompletna", "new": "nowa", "prepared": "przygotowana",
                  "calculating": "w trakcie kalkulacji", "negotiating": "w trakcie negocjacji", "sent": "wysłana",
                  "waiting_pay": "oczekiwanie na wpłatę", "accepted": "zaakceptowana", "ordered": "zamówiona",
                  "in_progress": "w trakcie realizacji", "done": "zrealizowana", "rejected": "odrzucona"}
OFFER_LEGACY = {"cancelled": "anulowana"}  # dawny status Sygnavo, tylko do wyswietlania
OFFER_OPEN = ("incomplete", "new", "prepared", "calculating", "negotiating", "sent")  # czekaja na decyzje klienta
OFFER_WON = ("waiting_pay", "accepted", "ordered", "in_progress", "done")


class Offer(Base):
    """Oferta dla klienta: pozycje z cenami, waznosc, status. Z oferty powstaje faktura albo WZ."""

    __tablename__ = "offers"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_offer_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="new")  # OFFER_STATUSES
    number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    valid_until: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    probability: Mapped[int | None] = mapped_column(Integer, nullable=True)  # szansa w %
    title: Mapped[str] = mapped_column(String(300), default="")  # wewnetrzny tytul (dla zespolu)
    heading: Mapped[str] = mapped_column(String(300), default="", server_default="")  # naglowek na PDF
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True)
    recipient_name: Mapped[str] = mapped_column(String(300), default="")
    recipient_address: Mapped[str] = mapped_column(Text, default="")
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    prices_gross: Mapped[bool] = mapped_column(default=False)
    total_net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    notes: Mapped[str] = mapped_column(Text, default="")  # na PDF dla klienta
    internal_notes: Mapped[str] = mapped_column(Text, default="")  # tylko w programie
    pdf_template: Mapped[str] = mapped_column(String(20), default="katalog", server_default="katalog")  # OFFER_PDF_TEMPLATES
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id", ondelete="SET NULL"), nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="")
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)  # np. "firmao:123"
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()
    contractor: Mapped[Contractor | None] = relationship()
    invoice: Mapped[Invoice | None] = relationship()
    delivery: Mapped[Delivery | None] = relationship()
    items: Mapped[list[OfferItem]] = relationship(
        back_populates="offer", cascade="all, delete-orphan", order_by="OfferItem.position"
    )

    @property
    def display_number(self) -> str:
        return self.number or f"Oferta #{self.id}"

    @property
    def status_label(self) -> str:
        st = self._status_cfg()
        return st["label"] if st else (OFFER_STATUSES.get(self.status) or OFFER_LEGACY.get(self.status, self.status))

    @property
    def status_class(self) -> str:
        st = self._status_cfg()
        if st:
            return st["color"]
        return "ok" if self.status in OFFER_WON else ("due" if self.status == "rejected" else "")

    @property
    def is_open(self) -> bool:
        st = self._status_cfg()
        return st["group"] == "open" if st else self.status in OFFER_OPEN

    def _status_cfg(self) -> dict | None:
        """Status wg ustawien firmy (nazwa, kolor, grupa); bez sesji: wbudowane."""
        from sqlalchemy.orm import object_session

        from . import offer_statuses

        db = object_session(self)
        return offer_statuses.find(db, self.company_id, self.status) if db is not None and self.company_id else None

    @property
    def days_left(self) -> int | None:
        return (self.valid_until - dt.date.today()).days if self.valid_until else None

    @property
    def expired(self) -> bool:
        return self.is_open and self.valid_until is not None and self.valid_until < dt.date.today()


class OfferItem(Base):
    __tablename__ = "offer_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    code: Mapped[str] = mapped_column(String(60), default="")
    name: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"))
    unit: Mapped[str] = mapped_column(String(10), default="szt.")
    unit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    vat_rate: Mapped[str] = mapped_column(String(3), default="23")
    net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    offer: Mapped[Offer] = relationship(back_populates="items")
    product: Mapped["Product | None"] = relationship()


class StockMovement(Base):
    """Ruch magazynowy produktu: WZ (minus), PZ (plus), korekta reczna."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id", ondelete="SET NULL"), nullable=True)
    date: Mapped[dt.date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))  # ze znakiem
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped[Product] = relationship()
    delivery: Mapped[Delivery | None] = relationship()


class InvoicePayment(Base):
    """Wplata zapisana recznie (gotowka, przelew spoza wyciagu, wplata czesciowa). Wplaty z banku sa w bank_transactions."""

    __tablename__ = "invoice_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    method: Mapped[str] = mapped_column(String(15), default="transfer")  # PAYMENT_METHODS
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    invoice: Mapped[Invoice] = relationship(back_populates="payments")

    @property
    def method_label(self) -> str:
        return PAYMENT_METHODS.get(self.method, self.method)


PURCHASE_STATUSES = {"new": "do weryfikacji", "accepted": "zatwierdzona", "rejected": "odrzucona"}


class PurchaseInvoice(Base):
    """Faktura kosztowa (firma jest nabywca) pobrana z KSeF. XML jest zrodlem, reszta to wyciag do listy."""

    __tablename__ = "purchase_invoices"
    __table_args__ = (UniqueConstraint("company_id", "ksef_number", name="uq_purchase_ksef"),
                      Index("ix_purchase_company_date", "company_id", "issue_date"))

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    ksef_env: Mapped[str] = mapped_column(String(10), default="")
    ksef_number: Mapped[str] = mapped_column(String(40))
    invoice_number: Mapped[str] = mapped_column(String(256), default="")
    invoice_type: Mapped[str] = mapped_column(String(10), default="")  # Vat, Kor, Zal...
    issue_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    stored_at: Mapped[str] = mapped_column(String(40), default="")  # permanentStorageDate z KSeF
    seller_nip: Mapped[str] = mapped_column(String(20), default="")
    seller_name: Mapped[str] = mapped_column(String(512), default="")
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True)
    net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="PLN")
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    bank_account: Mapped[str] = mapped_column(String(40), default="")
    xml: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="new")  # PURCHASE_STATUSES
    paid_at: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)  # np. "firmao:123"
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()
    contractor: Mapped[Contractor | None] = relationship()

    @property
    def status_label(self) -> str:
        return PURCHASE_STATUSES.get(self.status, self.status)


TEMPLATE_KINDS = {"invoice": "Wysyłka faktury", "reminder": "Przypomnienie o płatności", "wz": "Wysyłka WZ",
                  "offer": "Wysyłka oferty", "recurring": "Faktura cykliczna", "general": "Ogólny"}


class EmailTemplate(Base):
    """Szablon maila firmy. Znaczniki {numer}, {kwota} itd. wypelnia program (app/templates_mail.py)."""

    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(15), default="general")  # TEMPLATE_KINDS
    name: Mapped[str] = mapped_column(String(120))
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(default=False)  # domyslny dla rodzaju
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()

    @property
    def kind_label(self) -> str:
        return TEMPLATE_KINDS.get(self.kind, self.kind)


RECURRING_INTERVALS = {1: "co miesiąc", 2: "co 2 miesiące", 3: "co kwartał", 6: "co pół roku", 12: "co rok"}
RECURRING_MODES = {"draft": "tworzy szkic do sprawdzenia", "issue": "wystawia fakturę",
                   "send": "wystawia i wysyła mailem"}


class RecurringInvoice(Base):
    """Faktura cykliczna (abonament, umowa): wzor faktury i harmonogram. app/recurring.py tworzy z niej faktury."""

    __tablename__ = "recurring_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True)
    buyer_name: Mapped[str] = mapped_column(String(300), default="")
    buyer_nip: Mapped[str] = mapped_column(String(20), default="")
    buyer_address: Mapped[str] = mapped_column(Text, default="")
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    prices_gross: Mapped[bool] = mapped_column(default=False)
    payment_method: Mapped[str] = mapped_column(String(15), default="transfer")
    due_days: Mapped[int] = mapped_column(Integer, default=14)
    interval_months: Mapped[int] = mapped_column(Integer, default=1)
    day_of_month: Mapped[int] = mapped_column(Integer, default=1)  # 1-28, 0 = ostatni dzien miesiaca
    next_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    mode: Mapped[str] = mapped_column(String(10), default="draft")  # RECURRING_MODES
    # status umowy (nowa / aktywna / wygasla, wlasne w ustawieniach); tylko informacja, fakturowanie osobno
    contract_status: Mapped[str] = mapped_column(String(12), default="active", server_default="active")
    email_to: Mapped[str] = mapped_column(String(300), default="")
    template_id: Mapped[int | None] = mapped_column(ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    # Umowa: numer (drukowany na fakturach), daty, adnotacje. active=False = umowa zakonczona.
    number: Mapped[str] = mapped_column(String(60), default="", server_default="")
    signed_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    invoicing: Mapped[bool] = mapped_column(default=True, server_default=sa_true())  # faktury cykliczne wlaczone
    mail_account_id: Mapped[int | None] = mapped_column(ForeignKey("mail_accounts.id", ondelete="SET NULL"),
                                                        nullable=True)
    contract_notes: Mapped[str] = mapped_column(Text, default="", server_default="")  # adnotacje do umowy
    source_ref: Mapped[str] = mapped_column(String(120), default="", server_default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()
    contractor: Mapped[Contractor | None] = relationship()
    mail_account: Mapped["MailAccount | None"] = relationship()

    @property
    def display_number(self) -> str:
        return self.number or f"bez numeru #{self.id}"

    @property
    def status(self) -> str:
        if not self.active:
            return "ended"
        if self.start_date and self.start_date > dt.date.today():
            return "future"
        return "active" if self.invoicing else "no_invoices"

    @property
    def status_label(self) -> str:
        return {"ended": "zakończona", "future": "od " + (f"{self.start_date:%d.%m.%Y}" if self.start_date else ""),
                "active": "wystawia faktury", "no_invoices": "nie wystawia faktur"}[self.status]

    @property
    def contract_status_cfg(self) -> dict | None:
        from sqlalchemy.orm import object_session

        from . import offer_statuses

        db = object_session(self)
        return offer_statuses.find(db, self.company_id, self.contract_status, "contract") \
            if db is not None and self.company_id else None

    @property
    def contract_status_label(self) -> str:
        st = self.contract_status_cfg
        return st["label"] if st else self.contract_status

    @property
    def contract_status_class(self) -> str:
        st = self.contract_status_cfg
        return st["color"] if st else ""

    @property
    def status_hint(self) -> str:
        return {"no_invoices": "Umowa trwa, ale ma wyłączone wystawianie faktur (np. w Firmao była „Nowa” albo nie ma "
                               "pozycji). Zaznacz „Wystawiaj faktury” w umowie, żeby ruszyła.",
                "ended": "Umowa zakończona, faktury już nie powstają.",
                "future": "Umowa zaczyna się później, faktury ruszą od daty startu.",
                "active": "Faktury powstają zgodnie z harmonogramem."}[self.status] + (
            " Do startu Sygnavo tylko szkic: nic się nie wystawia ani nie wysyła." if self.mode == "draft" else "")

    @property
    def interval_label(self) -> str:
        return RECURRING_INTERVALS.get(self.interval_months, f"co {self.interval_months} mies.")

    @property
    def mode_label(self) -> str:
        return RECURRING_MODES.get(self.mode, self.mode)


class Equipment(Base):
    """Sprzet u klienta (kamera, rejestrator, wozek...): numery, gwarancja, lokalizacja, przeglady, historia serwisu."""

    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(100), default="")
    inventory_no: Mapped[str] = mapped_column(String(100), default="")
    serial_no: Mapped[str] = mapped_column(String(150), default="", index=True)
    barcode: Mapped[str] = mapped_column(String(100), default="")
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True,
                                                      index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    supplier: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    purchase_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    install_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    warranty_until: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    warranty_remind_days: Mapped[int] = mapped_column(Integer, default=30)
    service_interval_months: Mapped[int] = mapped_column(Integer, default=0)  # 0 = bez przegladow okresowych
    next_service: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    contractor: Mapped[Contractor | None] = relationship()
    contact: Mapped[Contact | None] = relationship()
    product: Mapped["Product | None"] = relationship()
    services: Mapped[list["ServiceEntry"]] = relationship(back_populates="equipment", cascade="all, delete-orphan",
                                                          order_by="ServiceEntry.date.desc(), ServiceEntry.id.desc()")

    def warranty_state(self, today: dt.date | None = None) -> str:
        """'' (brak), 'ok', 'soon' (konczy sie w okresie przypomnienia), 'over'."""
        if not self.warranty_until:
            return ""
        today = today or dt.date.today()
        if self.warranty_until < today:
            return "over"
        return "soon" if (self.warranty_until - today).days <= self.warranty_remind_days else "ok"

    def service_state(self, today: dt.date | None = None) -> str:
        if not self.next_service:
            return ""
        today = today or dt.date.today()
        if self.next_service < today:
            return "over"
        return "soon" if (self.next_service - today).days <= 14 else "ok"


SERVICE_KINDS = {"przeglad": "Przegląd", "naprawa": "Naprawa", "montaz": "Montaż", "gwarancja": "Naprawa gwarancyjna",
                 "inne": "Inne"}


class ServiceEntry(Base):
    """Wpis w historii serwisu urzadzenia (przeglad, naprawa...), opcjonalnie z WZ albo faktura."""

    __tablename__ = "service_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id", ondelete="CASCADE"), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(15), default="przeglad")
    description: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(100), default="")
    delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id", ondelete="SET NULL"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    equipment: Mapped[Equipment] = relationship(back_populates="services")
    delivery: Mapped["Delivery | None"] = relationship()
    invoice: Mapped["Invoice | None"] = relationship()

    @property
    def kind_label(self) -> str:
        return SERVICE_KINDS.get(self.kind, self.kind)


TASK_STATUSES = {"todo": "Do zrobienia", "doing": "W trakcie", "done": "Zrobione", "cancelled": "Anulowane"}
TASK_PRIORITIES = {"low": "Niski", "normal": "Normalny", "high": "Wysoki", "urgent": "Pilny"}


class Task(Base):
    """Zadanie (takze zlecenie serwisowe): termin, klient, sprzet, adres, podzadania, licznik czasu."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="todo")
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    category: Mapped[str] = mapped_column(String(100), default="")
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True,
                                                      index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True,
                                                     index=True)
    address: Mapped[str] = mapped_column(String(300), default="")
    start_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    all_day: Mapped[bool] = mapped_column(default=True)
    done_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    assignee: Mapped[str] = mapped_column(String(100), default="")
    minutes_spent: Mapped[int] = mapped_column(Integer, default=0)
    timer_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    contractor: Mapped[Contractor | None] = relationship()
    contact: Mapped[Contact | None] = relationship()
    equipment: Mapped[Equipment | None] = relationship()
    parent: Mapped["Task | None"] = relationship(remote_side=[id], back_populates="subtasks")
    subtasks: Mapped[list["Task"]] = relationship(back_populates="parent", cascade="all, delete-orphan",
                                                  order_by="Task.id")

    @property
    def is_open(self) -> bool:
        return self.status in ("todo", "doing")

    @property
    def when(self) -> dt.datetime | None:
        """Data do kalendarza: poczatek, a jak go nie ma, termin."""
        return self.start_at or self.due_at

    def overdue(self, now: dt.datetime | None = None) -> bool:
        if not self.is_open or not self.due_at:
            return False
        now = now or dt.datetime.now()
        date_only = self.all_day or self.due_at.time() == dt.time(0, 0)
        return self.due_at.date() < now.date() if date_only else self.due_at < now

    @property
    def status_label(self) -> str:
        return TASK_STATUSES.get(self.status, self.status)

    @property
    def priority_label(self) -> str:
        return TASK_PRIORITIES.get(self.priority, self.priority)

    def total_minutes(self, now: dt.datetime | None = None) -> int:
        extra = 0
        if self.timer_started_at:
            extra = int(((now or dt.datetime.now()) - self.timer_started_at).total_seconds() // 60)
        return (self.minutes_spent or 0) + max(0, extra)


# ---------------------------------------------------------------- zamowienia

ORDER_STATUSES = {"new": "nowe", "sent": "wysłane", "confirmed": "potwierdzone", "in_progress": "w realizacji", "waiting": "czeka na towar",
                  "ready": "gotowe do wydania", "done": "zrealizowane", "cancelled": "anulowane"}
ORDER_DIRECTIONS = {"sale": "od klienta", "purchase": "do dostawcy"}


class Order(Base):
    """Zamowienie od klienta (albo do dostawcy): pozycje, termin realizacji, status. Z niego faktura i WZ."""

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_order_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10), default="sale")  # ORDER_DIRECTIONS
    status: Mapped[str] = mapped_column(String(12), default="new")  # statusy w offer_statuses (kind "order")
    number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)  # termin realizacji
    title: Mapped[str] = mapped_column(String(300), default="")
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True)
    recipient_name: Mapped[str] = mapped_column(String(300), default="")
    recipient_address: Mapped[str] = mapped_column(Text, default="")
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    customer_ref: Mapped[str] = mapped_column(String(100), default="")  # numer zamowienia klienta
    prices_gross: Mapped[bool] = mapped_column(default=False)
    total_net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    notes: Mapped[str] = mapped_column(Text, default="")
    internal_notes: Mapped[str] = mapped_column(Text, default="")
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="")
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()
    contractor: Mapped[Contractor | None] = relationship()
    offer: Mapped[Offer | None] = relationship()
    invoice: Mapped[Invoice | None] = relationship()
    delivery: Mapped[Delivery | None] = relationship()
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.position"
    )

    @property
    def display_number(self) -> str:
        return self.number or f"Zamówienie #{self.id}"

    def _status_cfg(self) -> dict | None:
        from sqlalchemy.orm import object_session

        from . import offer_statuses
        db = object_session(self)
        return offer_statuses.find(db, self.company_id, self.status, "order") if db is not None else None

    @property
    def status_label(self) -> str:
        st = self._status_cfg()
        return st["label"] if st else ORDER_STATUSES.get(self.status, self.status)

    @property
    def status_class(self) -> str:
        st = self._status_cfg()
        return st["color"] if st else ""

    @property
    def is_open(self) -> bool:
        st = self._status_cfg()
        return (st["group"] == "open") if st else self.status not in ("done", "cancelled")

    @property
    def overdue(self) -> bool:
        return self.is_open and self.due_date is not None and self.due_date < dt.date.today()

    @property
    def direction_label(self) -> str:
        return ORDER_DIRECTIONS.get(self.direction, self.direction)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    code: Mapped[str] = mapped_column(String(60), default="")
    name: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"))
    unit: Mapped[str] = mapped_column(String(10), default="szt.")
    unit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    vat_rate: Mapped[str] = mapped_column(String(3), default="23")
    net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped["Product | None"] = relationship()


# ---------------------------------------------------------------- polaczenia telefoniczne

CALL_DIRECTIONS = {"in": "przychodzące", "out": "wychodzące", "internal": "wewnętrzne"}
CALL_STATUSES = {"answered": "odebrane", "missed": "nieodebrane", "busy": "zajęte", "failed": "nieudane",
                 "cancelled": "przerwane", "other": "inne"}


class PhoneCall(Base):
    """Polaczenie telefoniczne (historia z Firmao, potem z centrali). Laczone z klientem po numerze telefonu."""

    __tablename__ = "phone_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10), default="in")  # CALL_DIRECTIONS
    status: Mapped[str] = mapped_column(String(10), default="answered")  # CALL_STATUSES
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    phone: Mapped[str] = mapped_column(String(40), default="", index=True)  # numer rozmowcy (same cyfry)
    user_label: Mapped[str] = mapped_column(String(100), default="")  # kto prowadzil rozmowe
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True,
                                                      index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    done: Mapped[bool] = mapped_column(default=False)
    source_ref: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    contractor: Mapped[Contractor | None] = relationship()
    contact: Mapped[Contact | None] = relationship()
    task: Mapped[Task | None] = relationship()

    @property
    def direction_label(self) -> str:
        return CALL_DIRECTIONS.get(self.direction, self.direction)

    @property
    def status_label(self) -> str:
        return CALL_STATUSES.get(self.status, self.status)

    @property
    def duration(self) -> str:
        m, s = divmod(int(self.seconds or 0), 60)
        return f"{m}:{s:02d}" if m < 60 else f"{m // 60}:{m % 60:02d}:{s:02d}"


class Organisation(Base):
    """Klient abonamentu (najemca). Ma swoje firmy i swoich uzytkownikow; nie widzi danych innych organizacji."""

    __tablename__ = "organisations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(30), default="wewnetrzny", server_default="wewnetrzny")
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")  # active / blocked
    # Funkcje w fazie wczesnej wlaczone tej organizacji (app/features.py), klucze oddzielone przecinkami.
    features: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    """Osoba logujaca sie do programu. Rola decyduje, ktore czesci programu widzi (app/users.py ROLES)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey("organisations.id"), index=True)
    login: Mapped[str] = mapped_column(String(80), unique=True)  # zawsze male litery
    name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default="handlowiec")
    password_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(default=True, server_default=sa_true())
    must_change_password: Mapped[bool] = mapped_column(default=False, server_default=sa_false())
    # Weryfikacja dwuetapowa (TOTP): sekret zaszyfrowany secretboxem; pusty = wylaczona.
    totp_secret_enc: Mapped[str] = mapped_column(Text, default="", server_default="")
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
