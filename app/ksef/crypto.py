"""Szyfrowanie wg KSeF: RSA-OAEP (SHA-256, MGF1-SHA-256) kluczem publicznym MF, AES-256-CBC z PKCS#7, skroty SHA-256."""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.asymmetric import padding as apadding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def sha256_b64(data: bytes) -> str:
    return b64(hashlib.sha256(data).digest())


def sha256_b64url(data: bytes) -> str:
    """Skrot do linku w kodzie QR: Base64URL bez znakow '='."""
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


def rsa_oaep_encrypt(cert_b64_der: str, data: bytes) -> bytes:
    cert = x509.load_der_x509_certificate(base64.b64decode(cert_b64_der))
    return cert.public_key().encrypt(
        data, apadding.OAEP(mgf=apadding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    )


def encrypt_token(cert_b64_der: str, token: str, timestamp_ms: int) -> str:
    """Token KSeF do POST /auth/ksef-token: "token|timestampMs" zaszyfrowany RSA-OAEP, w Base64."""
    return b64(rsa_oaep_encrypt(cert_b64_der, f"{token}|{timestamp_ms}".encode()))


@dataclass
class SessionKey:
    key: bytes
    iv: bytes

    @classmethod
    def new(cls) -> "SessionKey":
        return cls(os.urandom(32), os.urandom(16))

    def encrypt(self, data: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        enc = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).encryptor()
        return enc.update(padded) + enc.finalize()

    def decrypt(self, data: bytes) -> bytes:
        dec = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).decryptor()
        padded = dec.update(data) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()

    def encryption_info(self, cert_b64_der: str, public_key_id: str | None = None) -> dict:
        info = {"encryptedSymmetricKey": b64(rsa_oaep_encrypt(cert_b64_der, self.key)),
                "initializationVector": b64(self.iv)}
        if public_key_id:
            info["publicKeyId"] = public_key_id
        return info
