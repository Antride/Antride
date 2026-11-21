"""Punto de entrada: python main.py"""

import logging

from finanzas.bot import build_app
from finanzas.config import load_config


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config()
    if not cfg.allowed_user_ids:
        logging.warning("ALLOWED_USER_IDS está vacío: el bot solo responderá /start con tu id.")
    build_app(cfg).run_polling()


if __name__ == "__main__":
    main()
