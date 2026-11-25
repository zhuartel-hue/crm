"""Poczta firm: pobieranie przez IMAP, wysylka przez SMTP, powiazanie z kontrahentami i fakturami.

- Odbior: INBOX i folder wyslanych (flaga \\Sent z LIST, inaczej typowe nazwy). Pierwsze pobranie
  bierze wiadomosci z ostatnich FIRST_SYNC_DAYS dni (najwyzej FIRST_SYNC_MAX), potem tylko nowe UID.
  Na serwerze niczego nie zmieniamy (BODY.PEEK, folder otwarty tylko do odczytu).
- Wysylka: SMTP (SSL albo STARTTLS); kopia trafia do folderu wyslanych przez IMAP APPEND
  (Gmail robi to sam, wiec dla Gmaila pomijamy).
- Kontrahent: po adresie e-mail (pole e-mail kontrahenta moze miec kilka adresow po przecinku).
  Faktura: numer faktury tej firmy w temacie.
"""
from __future__ import annotations

import datetime as dt
import email
import email.policy
import email.utils
import html
import imaplib
import json
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import secretbox
from ..models import Contractor, Invoice, MailAccount, MailAttachment, MailMessage

from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Warsaw")
FIRST_SYNC_DAYS = 60
FIRST_SYNC_MAX = 300
BATCH_MAX = 200
MAX_ATTACHMENT = 15 * 1024 * 1024
SENT_NAMES = ("Sent", "INBOX.Sent", "Sent Items", "Sent Messages", "Wysłane", "INBOX.Wysłane", "[Gmail]/Sent Mail")

# Do testow: fabryki polaczen mozna podmienic.
imap_factory = None
smtp_factory = None


class MailError(Exception):
    pass


# ------------------------------------------------------------------ pomocnicze

def _utcnow() -> dt.datetime:
    """Czas lokalny (Polska) bez strefy: tak zapisujemy daty wiadomosci i pobrania."""
    return dt.datetime.now(LOCAL_TZ).replace(tzinfo=None)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title") and self.skip:
            self.skip -= 1
        elif tag in ("p", "div", "tr", "li", "table"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(s: str) -> str:
    p = _Text()
    try:
        p.feed(s or "")
        p.close()
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", s or "")
    text = "".join(p.out)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


# Imap folder names: modified UTF-7 (RFC 3501 5.1.3)
def _imap_utf7_decode(s: str) -> str:
    def dec(m):
        part = m.group(1)
        if part == "":
            return "&"
        import base64
        b = part.replace(",", "/")
        b += "=" * (-len(b) % 4)
        return base64.b64decode(b).decode("utf-16-be")
    return re.sub(r"&([^-]*)-", dec, s)


def _imap_utf7_encode(s: str) -> str:
    import base64
    out, buf = [], ""

    def flush():
        nonlocal buf
        if buf:
            out.append("&" + base64.b64encode(buf.encode("utf-16-be")).decode().rstrip("=").replace("/", ",") + "-")
            buf = ""
    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf += ch
    flush()
    return "".join(out)


def _quote(folder: str) -> str:
    enc = _imap_utf7_encode(folder)
    return '"' + enc.replace("\\", "\\\\").replace('"', '\\"') + '"'


def addresses(value: str) -> list[tuple[str, str]]:
    return [(n, a.lower()) for n, a in email.utils.getaddresses([value or ""]) if a and "@" in a]


def _emails_of(contractor_email: str) -> list[str]:
    return [e.strip().lower() for e in re.split(r"[,;\s]+", contractor_email or "") if "@" in e]


def contractor_for(db: Session, emails: list[str]) -> Contractor | None:
    wanted = {e.lower() for e in emails}
    if not wanted:
        return None
    for c in db.scalars(select(Contractor).where(Contractor.email != "")):
        if wanted & set(_emails_of(c.email)):
            return c
    return None


def invoice_for(db: Session, company_id: int, subject: str) -> Invoice | None:
    from ..bank.service import _compact, _number_in_title

    subj = _compact(subject)
    if not subj:
        return None
    found = [i for i in db.scalars(select(Invoice).where(Invoice.company_id == company_id, Invoice.number.is_not(None)))
             if _number_in_title(i, subj)]
    found.sort(key=lambda i: -len(i.number or ""))
    return found[0] if found else None


def _password(acc: MailAccount) -> str:
    pw = secretbox.open_(acc.password_enc)
    if not pw:
        raise MailError("Brak hasła do skrzynki. Wpisz je w Ustawieniach poczty.")
    return pw


def _state(acc: MailAccount) -> dict:
    try:
        return json.loads(acc.sync_state or "{}")
    except ValueError:
        return {}


# ------------------------------------------------------------------ IMAP

def connect_imap(acc: MailAccount):
    if imap_factory is not None:
        return imap_factory(acc)
    try:
        m = imaplib.IMAP4_SSL(acc.imap_host, acc.imap_port or 993, ssl_context=ssl.create_default_context(), timeout=30)
        m.login(acc.username or acc.email, _password(acc))
        return m
    except (imaplib.IMAP4.error, OSError) as e:
        raise MailError(f"IMAP {acc.imap_host}: {e}") from e


def sent_folder(m) -> str | None:
    typ, data = m.list()
    if typ != "OK":
        return None
    names = []
    for raw in data or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        mm = re.match(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
        if not mm:
            continue
        name = mm.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        name = _imap_utf7_decode(name)
        if "\\sent" in mm.group("flags").lower():
            return name
        names.append(name)
    for cand in SENT_NAMES:
        for n in names:
            if n.lower() == cand.lower():
                return n
    return None


def _parse(raw: bytes):
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    text = html_body = ""
    atts = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = part.get_content_disposition()
        ctype = part.get_content_type()
        if disp == "attachment" or (disp == "inline" and part.get_filename()) or (
                part.get_filename() and ctype not in ("text/plain", "text/html")):
            payload = part.get_payload(decode=True) or b""
            atts.append((part.get_filename() or "zalacznik", ctype, len(payload),
                         payload if len(payload) <= MAX_ATTACHMENT else None))
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError, KeyError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
        if ctype == "text/plain" and not text:
            text = content
        elif ctype == "text/html" and not html_body:
            html_body = content
    if not text.strip() and html_body:
        text = html_to_text(html_body)
    try:
        date = email.utils.parsedate_to_datetime(str(msg.get("Date"))) if msg.get("Date") else None
        if date is not None and date.tzinfo is not None:
            date = date.astimezone(LOCAL_TZ).replace(tzinfo=None)
    except (TypeError, ValueError):
        date = None
    return msg, text, html_body, atts, date


def _hdr(msg, name: str) -> str:
    try:
        v = msg.get(name)
        return str(v) if v is not None else ""
    except Exception:  # noqa: BLE001  (zle zakodowany naglowek)
        return ""


def store(db: Session, acc: MailAccount, raw: bytes, folder: str, uid: int | None, direction: str,
          seen: bool = False) -> MailMessage | None:
    msg, text, html_body, atts, date = _parse(raw)
    msgid = _hdr(msg, "Message-ID").strip()[:300]
    if msgid and db.scalar(select(MailMessage.id).where(MailMessage.account_id == acc.id,
                                                       MailMessage.message_id == msgid,
                                                       MailMessage.direction == direction)):
        return None
    frm = addresses(_hdr(msg, "From"))
    to = _hdr(msg, "To")
    cc = _hdr(msg, "Cc")
    subject = _hdr(msg, "Subject")[:500]
    if direction == "in":
        people = [a for _, a in frm]
    else:
        people = [a for _, a in addresses(to) + addresses(cc)]
    m = MailMessage(
        account_id=acc.id, direction=direction, folder=folder, uid=uid, message_id=msgid,
        in_reply_to=_hdr(msg, "In-Reply-To").strip()[:300], subject=subject,
        from_addr=frm[0][1] if frm else "", from_name=(frm[0][0] if frm else "")[:200], to_addrs=to, cc_addrs=cc,
        date=date or _utcnow(), body_text=text or "", body_html=html_body or "", seen=seen or direction == "out",
    )
    c = contractor_for(db, people)
    m.contractor_id = c.id if c else None
    inv = invoice_for(db, acc.company_id, subject)
    m.invoice_id = inv.id if inv else None
    for name, ctype, size, data in atts:
        m.attachments.append(MailAttachment(filename=name[:300], content_type=ctype[:100], size=size, data=data))
    db.add(m)
    return m


def _sync_folder(db: Session, acc: MailAccount, m, folder: str, direction: str, state: dict) -> int:
    typ, _ = m.select(_quote(folder), readonly=True)
    if typ != "OK":
        return 0
    _, data = m.response("UIDVALIDITY")
    v = data[0] if data else None
    validity = v.decode() if isinstance(v, bytes) else str(v or "")
    st = state.get(folder) or {}
    if validity and st.get("validity") and st.get("validity") != validity:
        st = {}
    last = int(st.get("last") or 0)
    if last:
        typ, data = m.uid("SEARCH", None, f"UID {last + 1}:*")
    else:
        since = (dt.date.today() - dt.timedelta(days=FIRST_SYNC_DAYS)).strftime("%d-%b-%Y")
        typ, data = m.uid("SEARCH", None, f"SINCE {since}")
    uids = sorted(int(x) for x in (data[0].split() if typ == "OK" and data and data[0] else []))
    uids = [u for u in uids if u > last]
    if not last:
        uids = uids[-FIRST_SYNC_MAX:]
    uids = uids[:BATCH_MAX]
    count = 0
    for uid in uids:
        typ, data = m.uid("FETCH", str(uid), "(FLAGS BODY.PEEK[])")
        if typ != "OK" or not data:
            continue
        raw = flags = None
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                flags, raw = item[0], item[1]
                break
        if raw is None:
            continue
        seen = b"\\Seen" in (flags or b"")
        if store(db, acc, raw, folder, uid, direction, seen=seen) is not None:
            count += 1
        last = max(last, uid)
    if not uids and not last:
        # pusta skrzynka przy pierwszym pobraniu: zapamietaj, zeby nastepnym razem nie szukac po dacie
        typ, data = m.uid("SEARCH", None, "ALL")
        all_uids = [int(x) for x in (data[0].split() if typ == "OK" and data and data[0] else [])]
        last = max(all_uids) if all_uids else 0
    state[folder] = {"validity": validity, "last": last}
    return count


def sync_account(db: Session, acc: MailAccount) -> int:
    """Pobiera nowe wiadomosci. Zwraca liczbe nowych. Bledy zapisuje w acc.last_error."""
    total = 0
    try:
        m = connect_imap(acc)
        try:
            state = _state(acc)
            total += _sync_folder(db, acc, m, "INBOX", "in", state)
            sent = sent_folder(m)
            if sent:
                total += _sync_folder(db, acc, m, sent, "out", state)
            acc.sync_state = json.dumps(state)
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass
        acc.last_error = ""
    except (MailError, imaplib.IMAP4.error, OSError, ValueError) as e:
        acc.last_error = str(e)[:500]
    acc.last_sync = _utcnow()
    return total


def sync_all(db: Session) -> int:
    n = 0
    for acc in db.scalars(select(MailAccount).where(MailAccount.active.is_(True))):
        n += sync_account(db, acc)
        db.commit()
    return n


# ------------------------------------------------------------------ SMTP

def connect_smtp(acc: MailAccount):
    if smtp_factory is not None:
        return smtp_factory(acc)
    ctx = ssl.create_default_context()
    try:
        if acc.smtp_security == "starttls":
            s = smtplib.SMTP(acc.smtp_host, acc.smtp_port or 587, timeout=30)
            s.starttls(context=ctx)
        else:
            s = smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port or 465, context=ctx, timeout=30)
        s.login(acc.username or acc.email, _password(acc))
        return s
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP {acc.smtp_host}: {e}") from e


def _valid_list(value: str) -> list[str]:
    out = []
    for part in re.split(r"[,;]", value or ""):
        part = part.strip()
        if not part:
            continue
        _, addr = email.utils.parseaddr(part)
        if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", addr or ""):
            raise MailError(f"Niepoprawny adres: {part}")
        out.append(part)
    return out


def send(db: Session, acc: MailAccount, to: str, subject: str, body: str, cc: str = "",
         attachments: list[tuple[str, str, bytes]] | None = None, in_reply_to: str = "",
         invoice_id: int | None = None) -> MailMessage:
    to_list = _valid_list(to)
    cc_list = _valid_list(cc)
    if not to_list:
        raise MailError("Podaj adres odbiorcy.")
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((acc.display_name, acc.email)) if acc.display_name else acc.email
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject or ""
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=acc.email.split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    text = body or ""
    if acc.signature and acc.signature.strip() not in text:
        text = text.rstrip() + "\n\n-- \n" + acc.signature.strip()
    msg.set_content(text)
    for name, ctype, data in attachments or []:
        main, _, sub = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=name)
    raw = msg.as_bytes()
    s = connect_smtp(acc)
    try:
        s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Wysyłka nie powiodła się: {e}") from e
    finally:
        try:
            s.quit()
        except Exception:  # noqa: BLE001
            pass
    # Kopia w wyslanych (Gmail zapisuje sam).
    if "gmail" not in (acc.smtp_host or "").lower():
        try:
            m = connect_imap(acc)
            try:
                folder = sent_folder(m)
                if folder:
                    m.append(_quote(folder), "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw)
            finally:
                m.logout()
        except Exception:  # noqa: BLE001  (wiadomosc juz wyslana; brak kopii nie jest bledem wysylki)
            pass
    stored = store(db, acc, raw, "", None, "out", seen=True)
    if stored is not None and invoice_id:
        stored.invoice_id = invoice_id
    db.flush()
    return stored

