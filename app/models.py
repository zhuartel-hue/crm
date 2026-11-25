"""Model danych: firmy, kontrahenci, auta, faktury."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

Money = Numeric(14, 2)


class Company(Base):
    """Firma wystawiajaca faktury (Margot, ARI ERDMANN WORLDS)."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    # "month" - numeracja od 1 co miesiac, "year" - co rok.
    numbering_reset: Mapped[str] = mapped_column(String(10), default="month")
    default_payment_days: Mapped[int] = mapped_column(Integer, default=7)
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


class Contractor(Base):
    """Kontrahent, wspolny dla obu firm."""

    __tablename__ = "contractors"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def address_lines(self) -> list[str]:
        return [x for x in (self.street, f"{self.postal_code} {self.city}".strip()) if x]


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

    # Przypomnienia o platnosci wyslane mailem.
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    # Nieuzywane (dawna faktura marza); kolumna zostaje w bazie.
    margin_total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

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


class ApiToken(Base):
    """Token dostepu (access/refresh z OAuth albo staly klucz API). W bazie tylko skrot SHA-256."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # access / refresh / key
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grant_id: Mapped[str] = mapped_column(String(32), default="")  # wspolny dla access i refresh z jednej zgody
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
    """Produkt z katalogu (wspolny dla obu firm): kod, cena, VAT, opis i zdjecia."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()
    contractor: Mapped[Contractor | None] = relationship()

    @property
    def status_label(self) -> str:
        return PURCHASE_STATUSES.get(self.status, self.status)
