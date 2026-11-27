"""Jedno miejsce na zapytania HTTP integracji (w testach podmieniane przez monkeypatch `request`)."""
from __future__ import annotations

import httpx

TIMEOUT = 30.0


def request(method: str, url: str, **kw) -> httpx.Response:
    from .. import config

    if config.TEST_PANEL:
        from .common import IntegrationError

        raise IntegrationError(config.TEST_PANEL_MSG)
    kw.setdefault("timeout", TIMEOUT)
    return httpx.request(method, url, **kw)
