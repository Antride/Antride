"""Acceso de solo lectura a Gmail (API oficial de Google con OAuth)."""

from __future__ import annotations

import base64
import html
import io
import logging
import re
from dataclasses import dataclass, field
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path

log = logging.getLogger(__name__)

# Solo lectura: el bot nunca puede enviar, borrar ni modificar correos.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

MAX_BODY_CHARS = 20_000
MAX_PDF_BYTES = 10 * 1024 * 1024

DEFAULT_QUERY = (
    "(compra OR compraste OR cargo OR consumo OR \"estado de cuenta\" OR transacción OR "
    "transaccion OR retiro OR \"pago de servicio\" OR domiciliación OR domiciliacion OR "
    "\"tu pedido\" OR recibo OR factura OR \"has recibido\" OR depósito OR deposito OR "
    "transferencia OR SPEI OR suscripción OR suscripcion OR \"payment\" OR \"receipt\") "
    "-category:promotions -category:social"
)


@dataclass
class EmailMessage:
    id: str
    sender: str
    subject: str
    date: str          # cabecera Date tal cual
    internal_date: str  # YYYY-MM-DD (fecha en que llegó)
    body: str
    pdfs: list[tuple[str, bytes]] = field(default_factory=list)  # (nombre, bytes)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"):
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    p = _TextExtractor()
    p.feed(raw)
    text = html.unescape("".join(p.parts))
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def unlock_pdf(data: bytes, passwords: list[str]) -> bytes | None:
    """Devuelve el PDF sin contraseña. Muchos bancos protegen el estado de cuenta (p. ej.
    con tu RFC o fecha de nacimiento); las contraseñas se configuran en PDF_PASSWORDS."""
    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        log.warning("PDF ilegible: %s", e)
        return None
    if not reader.is_encrypted:
        return data
    for pwd in passwords:
        try:
            if reader.decrypt(pwd):
                writer = PdfWriter()
                for page in reader.pages:
                    writer.add_page(page)
                out = io.BytesIO()
                writer.write(out)
                return out.getvalue()
        except Exception:
            continue
    return None


class GmailClient:
    name = "gmail"

    def __init__(self, credentials_path: Path, token_path: Path):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None

    @property
    def is_linked(self) -> bool:
        return self.token_path.exists()

    def authorize_interactive(self) -> None:
        """Abre el navegador para que autorices el acceso (se hace una sola vez)."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.credentials_path.exists():
            raise SystemExit(
                f"No encuentro {self.credentials_path}. Descárgalo de Google Cloud Console "
                "(ver README, sección 'Conectar Gmail')."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
        creds = flow.run_local_server(port=0)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(creds.to_json())
        self.token_path.chmod(0o600)

    def _svc(self):
        if self._service is None:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            if not creds.valid and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self.token_path.write_text(creds.to_json())
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def find_new(self, lookback_days: int, query: str | None, max_results: int) -> list[str]:
        """Ids de correos recientes que parecen financieros (más nuevos primero)."""
        return self.search(f"({query or DEFAULT_QUERY}) newer_than:{lookback_days}d", max_results)

    def search(self, query: str, max_results: int = 50) -> list[str]:
        resp = self._svc().users().messages().list(
            userId="me", q=query, maxResults=max_results).execute()
        return [m["id"] for m in resp.get("messages", [])]

    def get(self, message_id: str, pdf_passwords: list[str]) -> EmailMessage:
        from datetime import datetime, timezone

        svc = self._svc()
        msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        plain, htmls, pdfs = [], [], []

        def walk(part):
            mime = part.get("mimeType", "")
            body = part.get("body", {})
            filename = part.get("filename") or ""
            if filename.lower().endswith(".pdf") or mime == "application/pdf":
                if body.get("size", 0) > MAX_PDF_BYTES:
                    return
                if "attachmentId" in body:
                    att = svc.users().messages().attachments().get(
                        userId="me", messageId=message_id, id=body["attachmentId"]).execute()
                    raw = _decode(att["data"])
                elif body.get("data"):
                    raw = _decode(body["data"])
                else:
                    return
                unlocked = unlock_pdf(raw, pdf_passwords)
                if unlocked:
                    pdfs.append((filename or "documento.pdf", unlocked))
                else:
                    log.warning("No pude abrir el PDF %s (¿falta su contraseña en PDF_PASSWORDS?)",
                                filename)
                return
            if body.get("data") and mime == "text/plain":
                plain.append(_decode(body["data"]).decode("utf-8", "replace"))
            elif body.get("data") and mime == "text/html":
                htmls.append(_decode(body["data"]).decode("utf-8", "replace"))
            for p in part.get("parts", []) or []:
                walk(p)

        walk(msg["payload"])
        # Muchos correos de bancos traen el texto plano vacío o incompleto: se prefiere el HTML.
        text = "\n".join(html_to_text(h) for h in htmls) if htmls else "\n".join(plain)
        if not text.strip():
            text = msg.get("snippet", "")
        internal = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
        return EmailMessage(
            id=message_id,
            sender=headers.get("from", ""),
            subject=headers.get("subject", ""),
            date=headers.get("date", ""),
            internal_date=internal.date().isoformat(),
            body=text[:MAX_BODY_CHARS],
            pdfs=pdfs,
        )


def sender_address(sender: str) -> str:
    return parseaddr(sender)[1].lower()
