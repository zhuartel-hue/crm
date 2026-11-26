"""Katalog produktow: wyszukiwanie, zdjecia (Pillow), dane dla faktur, Claude i API."""
from __future__ import annotations

import io
from decimal import Decimal

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import VAT_RATES, Product, ProductPhoto

MAX_SIDE = 1600
THUMB_SIDE = 400
MAX_UPLOAD = 25 * 1024 * 1024
MAX_PHOTOS = 20
Image.MAX_IMAGE_PIXELS = 60_000_000  # ochrona przed "bombami" pikselowymi


class PhotoError(ValueError):
    pass


def _jpeg(img: Image.Image, side: int, quality: int) -> bytes:
    im = img.copy()
    im.thumbnail((side, side), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()


def make_photo(raw: bytes, filename: str = "") -> ProductPhoto:
    """Zdjecie z pliku od uzytkownika: obrot wg EXIF, bez metadanych, JPEG max 1600 px i miniatura 400 px."""
    if len(raw) > MAX_UPLOAD:
        raise PhotoError(f"{filename or 'Plik'}: za duży (max 25 MB).")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise PhotoError(f"{filename or 'Plik'}: to nie jest obraz (JPG, PNG, WEBP, HEIC z iPhone'a zapisz jako JPG).")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    data = _jpeg(img, MAX_SIDE, 85)
    w, h = Image.open(io.BytesIO(data)).size
    return ProductPhoto(filename=(filename or "")[:200], width=w, height=h, data=data, thumb=_jpeg(img, THUMB_SIDE, 80))


def add_photos(product: Product, files: list[tuple[str, bytes]]) -> list[str]:
    """Dodaje zdjecia; zwraca bledy dla plikow, ktorych nie dalo sie przyjac."""
    errors = []
    pos = max((p.position for p in product.photos), default=-1) + 1
    for name, raw in files:
        if not raw:
            continue
        if len(product.photos) >= MAX_PHOTOS:
            errors.append(f"Najwyżej {MAX_PHOTOS} zdjęć na produkt.")
            break
        try:
            photo = make_photo(raw, name)
        except PhotoError as e:
            errors.append(str(e))
            continue
        photo.position = pos
        pos += 1
        product.photos.append(photo)
    return errors


def set_main_photo(product: Product, photo_id: int) -> None:
    photos = sorted(product.photos, key=lambda p: (p.id != photo_id, p.position))
    for i, p in enumerate(photos):
        p.position = i


def search(db: Session, company_id: int, query: str = "", active_only: bool = False,
           limit: int | None = None) -> list[Product]:
    stmt = select(Product).where(Product.company_id == company_id)
    if active_only:
        stmt = stmt.where(Product.active.is_(True))
    for word in (query or "").split():
        like = f"%{word}%"
        stmt = stmt.where(or_(Product.code.ilike(like), Product.name.ilike(like), Product.ean.ilike(like)))
    stmt = stmt.order_by(Product.active.desc(), Product.code, Product.name)
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def _m(v: Decimal | None) -> str | None:
    return None if v is None else f"{v:.2f}"


def product_dict(p: Product, base_url: str = "", with_description: bool = True) -> dict:
    d = dict(id=p.id, code=p.code, name=p.name, unit=p.unit, vat_rate=p.vat_rate, ean=p.ean, active=p.active,
             price_net=_m(p.price_net), price_gross=_m(p.price_gross),
             photos=[f"{base_url}/zdjecia/{ph.id}" for ph in p.photos])
    if with_description:
        d["description"] = p.description
    return d


def picker_json(products: list[Product]) -> dict:
    """Dane do podpowiedzi na formularzu faktury: klucz = tekst opcji w liscie."""
    return {p.label: dict(id=p.id, name=p.name, unit=p.unit, vat=p.vat_rate,
                          net=_m(p.price_net), gross=_m(p.price_gross)) for p in products}


def validate(p: Product) -> None:
    if not p.name.strip():
        raise ValueError("Podaj nazwę produktu.")
    if p.vat_rate not in VAT_RATES:
        raise ValueError("Stawka VAT: " + ", ".join(VAT_RATES) + ".")
    if p.price_net is not None and p.price_net < 0:
        raise ValueError("Cena nie może być ujemna.")
