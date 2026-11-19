"""Mode d’exécution commun aux deux services Docker."""

import logging
import os


def development():
    value = os.environ.get("DEV", "false").strip().lower()
    if value not in ("true", "false"):
        raise ValueError("DEV doit valoir true ou false.")
    return value == "true"


def app_origin():
    dev = development()
    origin = os.environ.get("APP_ORIGIN", "").strip()
    if not origin:
        if not dev:
            raise ValueError("APP_ORIGIN est obligatoire avec DEV=false.")
        origin = "http://127.0.0.1:8000"
    if not dev and not origin.startswith("https://"):
        raise ValueError("Une origine HTTPS est obligatoire avec DEV=false.")
    return origin


def configure_logging():
    # Ne jamais activer DEBUG sur les bibliothèques HTTP : cookies et corps privés.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("gaps_scraper")
    logger.setLevel(logging.INFO if development() else logging.WARNING)
    return logger
