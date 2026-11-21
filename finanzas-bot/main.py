"""Punto de entrada.

    python main.py                  # arranca el bot
    python main.py --vincular-gmail # autoriza (una sola vez) la lectura de tu Gmail
"""

import logging
import sys

from finanzas.config import load_config


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if "--vincular-gmail" in sys.argv:
        from finanzas.gmail_client import GmailClient

        cfg = load_config(require_tokens=False)
        GmailClient(cfg.gmail_credentials, cfg.gmail_token).authorize_interactive()
        print(f"✅ Gmail vinculado (solo lectura). Token guardado en {cfg.gmail_token}.")
        return

    from finanzas.bot import build_app

    cfg = load_config()
    if not cfg.allowed_user_ids:
        logging.warning("ALLOWED_USER_IDS está vacío: el bot solo responderá /start con tu id.")
    build_app(cfg).run_polling()


if __name__ == "__main__":
    main()
