"""Logowanie do KSeF certyfikatem KSeF (typ Authentication) albo innym certyfikatem: podpis XAdES
dokumentu AuthTokenRequest (schemat http://ksef.mf.gov.pl/auth/token/2.0, uwierzytelnianie.md).

Certyfikat KSeF z Aplikacji Podatnika KSeF to plik .crt i klucz prywatny .key (zaszyfrowany haslem
podanym przy generowaniu). Klucz odszyfrowujemy raz, przy wgraniu, i trzymamy w bazie zaszyfrowany
kluczem programu (secretbox); hasla nie zapisujemy.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

NS = "http://ksef.mf.gov.pl/auth/token/2.0"


class CertificateError(ValueError):
    pass


@dataclass
class KsefCertificate:
    cert_pem: str
    key_pem: str  # PKCS8 bez hasla (w bazie zaszyfrowany secretboxem)


def _load_cert(data: bytes) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(data)
    except ValueError:
        try:
            return x509.load_der_x509_certificate(data)
        except ValueError as e:
            raise CertificateError("Plik certyfikatu (.crt) jest nieczytelny.") from e


def _load_key(data: bytes, password: str):
    pw = password.encode() if password else None
    errors = []
    for loader in (serialization.load_pem_private_key, serialization.load_der_private_key):
        try:
            return loader(data, password=pw)
        except TypeError as e:  # klucz zaszyfrowany bez hasla albo haslo do niezaszyfrowanego klucza
            raise CertificateError("Klucz prywatny wymaga hasła podanego przy generowaniu certyfikatu."
                                   if pw is None else "Ten klucz nie jest zaszyfrowany: zostaw hasło puste.") from e
        except (ValueError, Exception) as e:  # noqa: BLE001  zly format albo zle haslo
            errors.append(e)
    raise CertificateError("Nie mogę odczytać klucza prywatnego (.key). Sprawdź plik i hasło.")


def load(cert_bytes: bytes, key_bytes: bytes, password: str = "") -> tuple[KsefCertificate, dict]:
    """Sprawdza pare certyfikat + klucz; zwraca dane do zapisu i opis certyfikatu."""
    cert = _load_cert(cert_bytes)
    key = _load_key(key_bytes, password)
    if not isinstance(key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
        raise CertificateError("Obsługiwane są klucze RSA i EC.")
    pub = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != pub:
        raise CertificateError("Klucz prywatny nie pasuje do certyfikatu.")
    now = dt.datetime.now(dt.timezone.utc)
    if cert.not_valid_after_utc < now:
        raise CertificateError(f"Certyfikat wygasł {cert.not_valid_after_utc:%d.%m.%Y}.")
    info = {
        "subject": cert.subject.rfc4514_string(),
        "serial": format(cert.serial_number, "X"),
        "valid_from": cert.not_valid_before_utc.date().isoformat(),
        "valid_to": cert.not_valid_after_utc.date().isoformat(),
    }
    kc = KsefCertificate(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
        key_pem=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode(),
    )
    return kc, info


def info(cert_pem: str) -> dict:
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    return {"subject": cert.subject.rfc4514_string(), "valid_to": cert.not_valid_after_utc.date().isoformat()}


def auth_request(challenge: str, nip: str, kc: KsefCertificate) -> bytes:
    """Podpisany (XAdES-BES, enveloped) AuthTokenRequest dla kontekstu NIP."""
    from lxml import etree
    from signxml import DigestAlgorithm, SignatureMethod
    from signxml.xades import XAdESSigner

    key = serialization.load_pem_private_key(kc.key_pem.encode(), password=None)
    method = SignatureMethod.ECDSA_SHA256 if isinstance(key, ec.EllipticCurvePrivateKey) else SignatureMethod.RSA_SHA256
    root = etree.Element("{%s}AuthTokenRequest" % NS, nsmap={None: NS})
    etree.SubElement(root, "{%s}Challenge" % NS).text = challenge
    ci = etree.SubElement(root, "{%s}ContextIdentifier" % NS)
    etree.SubElement(ci, "{%s}Nip" % NS).text = nip
    etree.SubElement(root, "{%s}SubjectIdentifierType" % NS).text = "certificateSubject"
    signer = XAdESSigner(signature_algorithm=method, digest_algorithm=DigestAlgorithm.SHA256)
    signed = signer.sign(root, key=kc.key_pem.encode(), cert=[kc.cert_pem])
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8")
