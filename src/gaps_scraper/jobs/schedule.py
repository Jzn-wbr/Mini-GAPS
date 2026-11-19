"""Récupération ponctuelle de l'horaire individuel courant via iCalendar."""

import argparse
import fcntl
import json
import os
from pathlib import Path
from gaps_scraper.common.storage import data_directory
import sqlite3
import sys
from urllib.error import HTTPError, URLError

from gaps_scraper.portal.client import Client, CaptureError
from gaps_scraper.common.credentials import credentials, CredentialsError
from gaps_scraper.common.parsing import ParseError
from gaps_scraper.schedule.client import fetch_schedule
from gaps_scraper.schedule.parser import parse_schedule
from gaps_scraper.schedule.store import synchronize_schedule


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true", help="Écrit le JSON privé pour contrôle local.")
    args = parser.parse_args()
    os.umask(0o077)
    private = data_directory()
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (private/".capture.lock").open("a") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise CaptureError("Une récupération GAPS est déjà en cours.") from None
        username,password=credentials()
        source,selection=fetch_schedule(Client(),username,password)
        document=parse_schedule(source,selection)
        result=synchronize_schedule(private/"notes.sqlite3",document)
        if args.review:
            path=private/f'schedule-review-{selection["academic_year"]["value"]}-{selection["semester"]["value"]}.json'
            path.write_text(json.dumps(document,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            path.chmod(0o600)
    print(json.dumps({"mode":"live","academic_year":selection["academic_year"]["label"],
                      "semester":selection["semester"]["label"],**result},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    try:
        main()
    except (CaptureError,ParseError,CredentialsError) as exc:
        print(str(exc),file=sys.stderr);sys.exit(1)
    except HTTPError as exc:
        print(f"Échec HTTP {exc.code}. Dernier horaire conservé.",file=sys.stderr)
        exc.close();sys.exit(1)
    except (OSError,URLError,ValueError,sqlite3.Error):
        print("Erreur réseau ou stockage. Synchronisation non validée.",file=sys.stderr);sys.exit(1)
    except (KeyboardInterrupt,EOFError):
        print("Synchronisation annulée.",file=sys.stderr);sys.exit(1)
