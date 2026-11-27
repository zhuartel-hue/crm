"""Jedno miejsce na zapytania HTTP integracji (w testach podmieniane przez monkeypatch `request`)."""
from __future__ import annotations

import httpx

TIMEOUT = 30.0


def request(method: str, url: str, **kw) -> httpx.Response:
    kw.setdefault("timeout", TIMEOUT)
    return httpx.request(method, url, **kw)
