"""Pobieranie w tle: zamowienia ze sklepow i Allegro, statusy przesylek. Jedno zadanie "integrations-sync"."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Integration, Shipment
from ..services import audit
from . import allegro, apaczka, shopify
from .common import IntegrationError, now

log = logging.getLogger("sygnavo.integrations")
ORDER_SOURCES = {"shopify": shopify, "allegro": allegro}


def sync_one(db: Session, integ: Integration, actor: str = "system") -> dict:
    mod = ORDER_SOURCES.get(integ.kind)
    if mod is None:
        return {}
    try:
        res = mod.sync(db, integ)
        integ.last_error = ""
        if integ.state == "error":
            integ.state = "active"
        integ.last_sync = now()
        if res.get("new"):
            audit(db, actor, "integration.sync", f"{integ.kind_label}: {res['new']} nowych zamówień",
                  company_id=integ.company_id, entity="integration", entity_id=integ.id)
        db.commit()
        return res
    except IntegrationError as e:
        db.rollback()
        integ = db.get(Integration, integ.id)
        integ.last_error = str(e)[:500]
        integ.last_sync = now()
        db.commit()
        raise
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("Integracja %s (%s) nie powiodła się", integ.id, integ.kind)
        integ = db.get(Integration, integ.id)
        integ.last_error = "Błąd techniczny przy pobieraniu. Spróbujemy ponownie przy kolejnym pobraniu."
        db.commit()
        raise IntegrationError(integ.last_error) from None


def track_open(db: Session) -> None:
    rows = db.scalars(select(Shipment).where(Shipment.state.in_(("ordered", "in_transit")),
                                             Shipment.integration_id.is_not(None))).all()
    for s in rows:
        integ = db.get(Integration, s.integration_id)
        if integ is None or integ.state != "active" or integ.kind != "apaczka":
            continue
        try:
            apaczka.track(integ, s)
            db.commit()
        except IntegrationError as e:
            db.rollback()
            log.info("Śledzenie %s: %s", s.waybill, e)


def sync_all(db: Session) -> None:
    ids = db.scalars(select(Integration.id).where(Integration.state.in_(("active", "error")),
                                                  Integration.kind.in_(tuple(ORDER_SOURCES)))).all()
    for iid in ids:
        integ = db.get(Integration, iid)
        try:
            sync_one(db, integ)
        except IntegrationError as e:
            log.info("Integracja %s: %s", iid, e)
    track_open(db)
