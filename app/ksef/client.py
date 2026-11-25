"""Klient API KSeF 2.0: uwierzytelnienie tokenem KSeF i wysylka faktury w sesji interaktywnej.

Przebieg (uwierzytelnianie.md, sesja-interaktywna.md):
1. POST /auth/challenge -> challenge, timestampMs
2. POST /auth/ksef-token z "token|timestampMs" zaszyfrowanym RSA-OAEP (klucz KsefTokenEncryption)
3. GET /auth/rev2/{ref} (Bearer authenticationToken) az status 200
4. POST /auth/token/redeem -> accessToken
5. POST /sessions/online (FA(3), klucz AES zaszyfrowany kluczem SymmetricKeyEncryption)
6. POST /sessions/online/{ref}/invoices -> numer referencyjny faktury
7. GET /sessions/{ref}/invoices/{invRef} az status 200 -> numer KSeF; potem UPO
8. POST /sessions/online/{ref}/close
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from . import ENVIRONMENTS
from .crypto import SessionKey, encrypt_token, sha256_b64

FORM_CODE_FA3 = {"systemCode": "FA (3)", "schemaVersion": "1-0E", "value": "FA"}


class KsefError(Exception):
    def __init__(self, message: str, code: int | None = None, details: list | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or []

    def __str__(self) -> str:
        msg = super().__str__()
        if self.details:
            msg += " (" + "; ".join(str(d) for d in self.details) + ")"
        return msg


@dataclass
class SendResult:
    session_ref: str
    invoice_ref: str
    status_code: int
    status_text: str
    ksef_number: str = ""
    details: list = field(default_factory=list)
    upo_xml: str = ""


class KsefClient:
    def __init__(self, env: str, nip: str, token, *, http: httpx.Client | None = None,
                 poll_interval: float = 1.0, poll_timeout: float = 60.0):
        if env not in ENVIRONMENTS:
            raise KsefError(f"Nieznane środowisko KSeF: {env}")
        self.env = env
        self.nip = nip
        self.token = token
        self.http = http or httpx.Client(base_url=ENVIRONMENTS[env]["api"], timeout=30.0)
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.access_token: str | None = None
        self._certs: list[dict] | None = None

    # --- HTTP ---------------------------------------------------------------

    def _call(self, method: str, path: str, *, bearer: str | None = None, json=None, accept_xml=False):
        headers = {"Accept": "application/xml" if accept_xml else "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            r = self.http.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as e:
            raise KsefError(f"Brak połączenia z KSeF ({self.env}): {e}") from e
        if r.status_code >= 400:
            raise KsefError(*_error_from(r))
        if accept_xml:
            return r.text
        return r.json() if r.content else {}

    def _poll(self, fn, done):
        deadline = time.monotonic() + self.poll_timeout
        while True:
            data = fn()
            if done(data):
                return data
            if time.monotonic() > deadline:
                return data
            time.sleep(self.poll_interval)

    # --- klucze publiczne MF -----------------------------------------------------

    def _cert(self, usage: str) -> dict:
        if self._certs is None:
            self._certs = self._call("GET", "/security/public-key-certificates")
        for c in self._certs:
            if usage in (c.get("usage") or []):
                return c
        raise KsefError(f"KSeF nie zwrócił klucza publicznego {usage}.")

    # --- uwierzytelnienie -------------------------------------------------------

    def authenticate(self) -> str:
        """Logowanie tokenem KSeF (token: str) albo certyfikatem (token: xades.KsefCertificate)."""
        from .xades import KsefCertificate

        if isinstance(self.token, KsefCertificate):
            return self._authenticate_xades(self.token)
        ch = self._call("POST", "/auth/challenge")
        cert = self._cert("KsefTokenEncryption")
        body = {
            "challenge": ch["challenge"],
            "contextIdentifier": {"type": "Nip", "value": self.nip},
            "encryptedToken": encrypt_token(cert["certificate"], self.token, int(ch["timestampMs"])),
        }
        if cert.get("publicKeyId"):
            body["publicKeyId"] = cert["publicKeyId"]
        init = self._call("POST", "/auth/ksef-token", json=body)
        return self._finish_auth(init)

    def _authenticate_xades(self, kc) -> str:
        from .xades import auth_request

        ch = self._call("POST", "/auth/challenge")
        body = auth_request(ch["challenge"], self.nip, kc)
        try:
            r = self.http.post("/auth/xades-signature", content=body,
                               headers={"Content-Type": "application/xml", "Accept": "application/json"})
        except httpx.HTTPError as e:
            raise KsefError(f"Brak połączenia z KSeF ({self.env}): {e}") from e
        if r.status_code >= 400:
            raise KsefError(*_error_from(r))
        return self._finish_auth(r.json())

    def _finish_auth(self, init: dict) -> str:
        auth_token = init["authenticationToken"]["token"]
        ref = init["referenceNumber"]
        st = self._poll(lambda: self._call("GET", f"/auth/rev2/{ref}", bearer=auth_token),
                        lambda d: d["status"]["code"] != 100)
        code = st["status"]["code"]
        if code != 200:
            raise KsefError(f"Logowanie do KSeF nieudane: {st['status'].get('description', '')}", code,
                            st["status"].get("details"))
        tokens = self._call("POST", "/auth/token/redeem", bearer=auth_token)
        self.access_token = tokens["accessToken"]["token"]
        return self.access_token

    # --- wysylka -------------------------------------------------------------

    def send_invoice(self, xml: bytes, *, hash_of_corrected: str | None = None, fetch_upo: bool = True) -> SendResult:
        if not self.access_token:
            self.authenticate()
        at = self.access_token
        key = SessionKey.new()
        sym = self._cert("SymmetricKeyEncryption")
        opened = self._call("POST", "/sessions/online", bearer=at, json={
            "formCode": FORM_CODE_FA3, "encryption": key.encryption_info(sym["certificate"], sym.get("publicKeyId")),
        })
        session_ref = opened["referenceNumber"]
        try:
            enc = key.encrypt(xml)
            body = {
                "invoiceHash": sha256_b64(xml), "invoiceSize": len(xml),
                "encryptedInvoiceHash": sha256_b64(enc), "encryptedInvoiceSize": len(enc),
                "encryptedInvoiceContent": __import__("base64").b64encode(enc).decode(),
            }
            if hash_of_corrected:
                body["hashOfCorrectedInvoice"] = hash_of_corrected
            sent = self._call("POST", f"/sessions/online/{session_ref}/invoices", bearer=at, json=body)
            inv_ref = sent["referenceNumber"]
            res = self.invoice_status(session_ref, inv_ref, wait=True)
            if fetch_upo and res.status_code == 200:
                try:
                    res.upo_xml = self._call("GET", f"/sessions/{session_ref}/invoices/{inv_ref}/upo",
                                             bearer=at, accept_xml=True)
                except KsefError:
                    pass  # UPO mozna pobrac pozniej (sprawdz status)
            return res
        finally:
            try:
                self._call("POST", f"/sessions/online/{session_ref}/close", bearer=at)
            except KsefError:
                pass

    def invoice_status(self, session_ref: str, inv_ref: str, wait: bool = False) -> SendResult:
        if not self.access_token:
            self.authenticate()

        def get():
            return self._call("GET", f"/sessions/{session_ref}/invoices/{inv_ref}", bearer=self.access_token)

        data = self._poll(get, lambda d: d["status"]["code"] not in (100, 150)) if wait else get()
        st = data["status"]
        return SendResult(session_ref=session_ref, invoice_ref=inv_ref, status_code=st["code"],
                          status_text=st.get("description", ""), ksef_number=data.get("ksefNumber") or "",
                          details=st.get("details") or [])

    def upo(self, session_ref: str, inv_ref: str) -> str:
        if not self.access_token:
            self.authenticate()
        return self._call("GET", f"/sessions/{session_ref}/invoices/{inv_ref}/upo", bearer=self.access_token,
                          accept_xml=True)


def _error_from(r: httpx.Response) -> tuple[str, int | None, list]:
    """Bledy KSeF: {"exception": {"exceptionDetailList": [{"exceptionCode", "exceptionDescription", "details"}]}}
    albo problem+json. Zwracamy czytelny opis."""
    try:
        data = r.json()
    except ValueError:
        return f"KSeF HTTP {r.status_code}: {r.text[:300]}", r.status_code, []
    exc = data.get("exception") if isinstance(data, dict) else None
    if exc and exc.get("exceptionDetailList"):
        first = exc["exceptionDetailList"][0]
        details = [d for e in exc["exceptionDetailList"] for d in (e.get("details") or [])]
        return (f"KSeF {first.get('exceptionCode')}: {first.get('exceptionDescription')}",
                first.get("exceptionCode"), details)
    if isinstance(data, dict) and (data.get("title") or data.get("detail")):
        return f"KSeF HTTP {r.status_code}: {data.get('title', '')} {data.get('detail', '')}".strip(), r.status_code, []
    return f"KSeF HTTP {r.status_code}: {str(data)[:300]}", r.status_code, []
