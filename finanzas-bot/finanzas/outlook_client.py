"""Acceso de solo lectura a Outlook / Hotmail / Microsoft 365 (Microsoft Graph + MSAL)."""

from __future__ import annotations

import base64
import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .gmail_client import MAX_BODY_CHARS, MAX_PDF_BYTES, EmailMessage, html_to_text, unlock_pdf

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
# Solo lectura: el bot nunca puede enviar, borrar ni modificar correos.
SCOPES = ["Mail.Read"]
ID_PREFIX = "outlook:"

# Palabras que delatan un correo con movimientos (se buscan en asunto, remitente y vista previa).
KEYWORDS = [
    "compra", "cargo", "consumo", "estado de cuenta", "transaccion", "retiro", "pago de servicio",
    "domiciliacion", "tu pedido", "recibo", "factura", "has recibido", "deposito",
    "transferencia", "spei", "suscripcion", "payment", "receipt", "order", "pedido",
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()


def looks_financial(subject: str, sender: str, preview: str, keywords: list[str] | None = None) -> bool:
    text = _norm(f"{subject} {sender} {preview}")
    return any(_norm(k) in text for k in (keywords or KEYWORDS))


class OutlookClient:
    name = "outlook"

    def __init__(self, client_id: str, tenant: str, token_path: Path,
                 keywords: list[str] | None = None):
        self.client_id = client_id
        self.tenant = tenant
        self.token_path = token_path
        self.keywords = keywords
        self._app = None
        self._cache = None
        self._skip_folders: set[str] | None = None

    @property
    def is_linked(self) -> bool:
        return bool(self.client_id) and self.token_path.exists()

    def _msal(self):
        if self._app is None:
            import msal

            self._cache = msal.SerializableTokenCache()
            if self.token_path.exists():
                self._cache.deserialize(self.token_path.read_text())
            self._app = msal.PublicClientApplication(
                self.client_id, authority=f"https://login.microsoftonline.com/{self.tenant}",
                token_cache=self._cache)
        return self._app

    def _save_cache(self) -> None:
        if self._cache is not None and self._cache.has_state_changed:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(self._cache.serialize())
            self.token_path.chmod(0o600)

    def authorize_interactive(self) -> None:
        """Vinculación con código: abre la URL en cualquier dispositivo y escribe el código."""
        if not self.client_id:
            raise SystemExit("Falta OUTLOOK_CLIENT_ID en .env (ver README, 'Conectar Outlook').")
        app = self._msal()
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise SystemExit(f"No se pudo iniciar la vinculación: {flow.get('error_description')}")
        print(flow["message"])  # "Para iniciar sesión, abre https://microsoft.com/devicelogin ..."
        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise SystemExit(f"No se pudo vincular: {result.get('error_description')}")
        self._save_cache()

    def _token(self) -> str:
        app = self._msal()
        accounts = app.get_accounts()
        result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
        if not result or "access_token" not in result:
            raise RuntimeError("La sesión de Outlook expiró: ejecuta 'python main.py --vincular-outlook'")
        self._save_cache()
        return result["access_token"]

    def _get(self, url: str, params: dict | None = None) -> dict:
        import requests

        r = requests.get(url if url.startswith("http") else GRAPH + url, params=params,
                         headers={"Authorization": f"Bearer {self._token()}"}, timeout=60)
        r.raise_for_status()
        return r.json()

    def _folders_to_skip(self) -> set[str]:
        if self._skip_folders is None:
            self._skip_folders = set()
            for well_known in ("junkemail", "deleteditems"):
                try:
                    self._skip_folders.add(self._get(f"/me/mailFolders/{well_known}")["id"])
                except Exception:
                    pass
        return self._skip_folders

    def find_new(self, lookback_days: int, query: str | None, max_results: int) -> list[str]:
        """Ids de correos recientes que parecen financieros (más nuevos primero)."""
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        url, params = "/me/messages", {
            "$filter": f"receivedDateTime ge {since}",
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,bodyPreview,parentFolderId",
            "$top": "100",
        }
        skip = self._folders_to_skip()
        keywords = [k.strip() for k in query.split(",")] if query else self.keywords
        out: list[str] = []
        scanned = 0
        while url and scanned < 2000 and len(out) < max_results:
            data = self._get(url, params)
            params = None  # nextLink ya trae los parámetros
            for m in data.get("value", []):
                scanned += 1
                if m.get("parentFolderId") in skip:
                    continue
                sender = (m.get("from") or {}).get("emailAddress", {})
                if looks_financial(m.get("subject", ""),
                                   f"{sender.get('name', '')} {sender.get('address', '')}",
                                   m.get("bodyPreview", ""), keywords):
                    out.append(ID_PREFIX + m["id"])
            url = data.get("@odata.nextLink")
        return out[:max_results]

    def get(self, message_id: str, pdf_passwords: list[str]) -> EmailMessage:
        raw_id = message_id.removeprefix(ID_PREFIX)
        m = self._get(f"/me/messages/{raw_id}",
                      {"$select": "subject,from,receivedDateTime,body,hasAttachments"})
        body = m.get("body") or {}
        text = body.get("content", "")
        if body.get("contentType", "").lower() == "html":
            text = html_to_text(text)
        pdfs = []
        if m.get("hasAttachments"):
            atts = self._get(f"/me/messages/{raw_id}/attachments",
                             {"$select": "name,contentType,size"}).get("value", [])
            for a in atts:
                name = a.get("name") or ""
                is_pdf = name.lower().endswith(".pdf") or a.get("contentType") == "application/pdf"
                if not is_pdf or a.get("size", 0) > MAX_PDF_BYTES:
                    continue
                full = self._get(f"/me/messages/{raw_id}/attachments/{a['id']}")
                if not full.get("contentBytes"):
                    continue
                unlocked = unlock_pdf(base64.b64decode(full["contentBytes"]), pdf_passwords)
                if unlocked:
                    pdfs.append((name or "documento.pdf", unlocked))
                else:
                    log.warning("No pude abrir el PDF %s (¿falta su contraseña en PDF_PASSWORDS?)",
                                name)
        sender = (m.get("from") or {}).get("emailAddress", {})
        received = m.get("receivedDateTime", "")
        return EmailMessage(
            id=message_id,
            sender=f"{sender.get('name', '')} <{sender.get('address', '')}>",
            subject=m.get("subject", ""),
            date=received,
            internal_date=received[:10],
            body=text[:MAX_BODY_CHARS],
            pdfs=pdfs,
        )
