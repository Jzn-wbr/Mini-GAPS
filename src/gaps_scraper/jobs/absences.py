"""Récupère toutes les absences d'une année puis termine."""

import argparse
import fcntl
import json
import os
from pathlib import Path
from gaps_scraper.common.storage import data_directory
import sqlite3
import sys
from urllib.error import HTTPError, URLError

from gaps_scraper.absences.client import fetch_absences
from gaps_scraper.absences.parser import parse_absences
from gaps_scraper.absences.store import synchronize_absences
from gaps_scraper.portal.client import Client, CaptureError
from gaps_scraper.common.credentials import credentials, CredentialsError
from gaps_scraper.common.parsing import ParseError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, help="Année de début ; sinon sélection annuelle de GAPS.")
    parser.add_argument("--review", action="store_true", help="Écrit aussi le JSON structuré dans data/private pour contrôle local.")
    args = parser.parse_args()
    os.umask(0o077)
    private = data_directory()
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (private / ".capture.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CaptureError("Une récupération GAPS est déjà en cours.") from None
        username, password = credentials()
        source, metadata = fetch_absences(Client(), username, password, year=args.year)
        document = parse_absences(source, **metadata)
        result = synchronize_absences(private / "notes.sqlite3", document)
        if args.review:
            path = private / f'absences-review-{metadata["year"]}.json'
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            path.chmod(0o600)
    print(json.dumps({"mode": "live", "academic_year": document["academic_year"]["label"],
                      "rate_filter": "all", **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (CaptureError, ParseError, CredentialsError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except HTTPError as exc:
        print(f"Échec HTTP {exc.code}. Dernières données conservées.", file=sys.stderr)
        exc.close()
        sys.exit(1)
    except (OSError, URLError, ValueError, sqlite3.Error):
        print("Erreur réseau ou stockage. Synchronisation non validée.", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("Synchronisation annulée.", file=sys.stderr)
        sys.exit(1)
