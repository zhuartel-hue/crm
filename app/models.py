"""Model danych: firmy, kontrahenci, auta, faktury."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
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
    pattern_margin: Mapped[str] = mapped_column(String(60), default="FM/{nr}/{MM}/{RRRR}")
    # "month" - numeracja od 1 co miesiac, "year" - co rok.
    numbering_reset: Mapped[str] = mapped_column(String(10), default="month")
    default_payment_days: Mapped[int] = mapped_column(Integer, default=7)

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
    """Auto na placu. Cena zakupu jest podstawa do liczenia marzy."""

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


INVOICE_KINDS = {"vat": "Faktura VAT", "margin": "Faktura VAT marża"}
VAT_RATES = ["23", "8", "5", "0", "zw", "np"]
PAYMENT_METHODS = {"transfer": "Przelew", "cash": "Gotówka", "card": "Karta", "compensation": "Kompensata"}


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_invoice_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(String(10))  # vat / margin
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

    total_net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    # Tylko marza: suma marz i VAT od marzy (do ewidencji, nie drukowane na fakturze).
    margin_total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship()
    contractor: Mapped[Contractor | None] = relationship()
    car: Mapped[Car | None] = relationship()
    items: Mapped[list[InvoiceItem]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.position"
    )

    @property
    def kind_label(self) -> str:
        return INVOICE_KINDS.get(self.kind, self.kind)

    @property
    def payment_label(self) -> str:
        return PAYMENT_METHODS.get(self.payment_method, self.payment_method)

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
    vat_rate: Mapped[str] = mapped_column(String(3), default="23")  # przy marzy zawsze 23 (od marzy)
    net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    vat: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    # Marza: cena nabycia pozycji (laczna), marza i VAT od marzy.
    purchase_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    margin: Mapped[Decimal | None] = mapped_column(Money, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="items")


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
