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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "finanzas.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "extractos"


def load_config() -> Config:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN (créalo con @BotFather en Telegram).")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
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
    )
