"""Configuración leída de variables de entorno (o de un archivo .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_ids: frozenset[int]
    data_dir: Path
    currency: str
    timezone: ZoneInfo
    gmail_query: str | None
    gmail_interval_minutes: int
    gmail_lookback_days: int
    pdf_passwords: tuple[str, ...]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "finanzas.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "extractos"

    @property
    def gmail_credentials(self) -> Path:
        return self.data_dir / "gmail_credentials.json"

    @property
    def gmail_token(self) -> Path:
        return self.data_dir / "gmail_token.json"


def load_config(require_tokens: bool = True) -> Config:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and require_tokens:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN (créalo con @BotFather en Telegram).")
    if require_tokens and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise SystemExit("Falta ANTHROPIC_API_KEY (consíguela en platform.claude.com).")
    ids = frozenset(
        int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
    )
    return Config(
        telegram_token=token,
        allowed_user_ids=ids,
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        currency=os.environ.get("CURRENCY", "MXN"),
        timezone=ZoneInfo(os.environ.get("TIMEZONE", "America/Mexico_City")),
        gmail_query=os.environ.get("GMAIL_QUERY") or None,
        gmail_interval_minutes=int(os.environ.get("GMAIL_INTERVAL_MINUTES", "15")),
        gmail_lookback_days=int(os.environ.get("GMAIL_LOOKBACK_DAYS", "60")),
        # Contraseñas para abrir estados de cuenta en PDF (p. ej. tu RFC), separadas por coma.
        pdf_passwords=tuple(p for p in os.environ.get("PDF_PASSWORDS", "").split(",") if p),
    )
