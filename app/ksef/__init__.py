"""KSeF 2.0: faktury FA(3), API Ministerstwa Finansow (uwierzytelnienie tokenem KSeF, sesja interaktywna), kody QR.

Zrodla: https://github.com/CIRFMF/ksef-docs (uwierzytelnianie.md, sesja-interaktywna.md, kody-qr.md,
open-api.json) i schemat FA(3) 1-0E.
"""

ENVIRONMENTS = {
    "test": {"label": "Testowe MF", "api": "https://api-test.ksef.mf.gov.pl/v2", "qr": "https://qr-test.ksef.mf.gov.pl"},
    "demo": {"label": "Przedprodukcyjne (demo)", "api": "https://api-demo.ksef.mf.gov.pl/v2",
             "qr": "https://qr-demo.ksef.mf.gov.pl"},
    "prod": {"label": "Produkcyjne", "api": "https://api.ksef.mf.gov.pl/v2", "qr": "https://qr.ksef.mf.gov.pl"},
}
