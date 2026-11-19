"""Lance uniquement l'API privée, sans identifiants scolaires ni ordonnanceur."""

import argparse
import os
import sys
import sqlite3
import uvicorn

from gaps_scraper.api.app import Settings, create_app
from gaps_scraper.common.storage import database_path
from gaps_scraper.common.runtime import app_origin, configure_logging, development


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8000)
    args = parser.parse_args()
    os.umask(0o077)
    configure_logging()
    settings = Settings(database_path(),app_origin())
    uvicorn.run(create_app(settings),host=args.host,port=args.port,workers=1,
                access_log=False,proxy_headers=False,
                log_level="info" if development() else "warning")


if __name__ == "__main__":
    try:
        main()
    except (ValueError,OSError,sqlite3.Error):
        print("Configuration de l'API ou stockage invalide.",file=sys.stderr)
        sys.exit(1)
