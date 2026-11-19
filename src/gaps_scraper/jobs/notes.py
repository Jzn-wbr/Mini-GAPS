"""Scraper ponctuel des notes : réseau ou rejeu privé, puis transaction SQLite."""

import argparse
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
from gaps_scraper.common.storage import data_directory
import sqlite3
import sys
from urllib.error import HTTPError, URLError

from gaps_scraper.portal.client import BASE, CaptureError, Client, authenticated_student
from gaps_scraper.notes.client import fetch_notes, selection_arguments
from gaps_scraper.common.credentials import credentials, CredentialsError
from gaps_scraper.common.parsing import ParseError
from gaps_scraper.notes.programme import enrich_notes
from gaps_scraper.notes.parser import parse_notes
from gaps_scraper.notes.store import synchronize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-capture", type=Path,
                        help="Rejoue une capture locale terminée sans accès réseau.")
    selection_arguments(parser)
    args = parser.parse_args()
    if args.from_capture and (args.year is not None or args.semester is not None):
        parser.error("Le rejeu conserve la sélection de la capture : ne pas ajouter --year ou --semester.")
    os.umask(0o077)
    private = data_directory()
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (private / ".capture.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CaptureError("Une récupération GAPS est déjà en cours.") from None
        if args.from_capture:
            manifest = json.loads((args.from_capture / "manifest.json").read_text())
            if manifest.get("notes_ajax_completed") is not True:
                raise CaptureError("Capture sans résultat AJAX validé.")
            sources = {name: (args.from_capture / name).read_text(encoding="utf-8")
                       for name in ("student.html", "notes.html", "notes-result.html")}
        else:
            username, password = credentials()
            client = Client()
            sources, _ = fetch_notes(client, username, password, year=args.year, semester=args.semester)
        document = parse_notes(sources["notes.html"], sources["notes-result.html"])
        student_id = authenticated_student(sources["student.html"])
        document["account_id"] = sha256((BASE + ":" + student_id).encode()).hexdigest()
        if not args.from_capture:
            enrich_notes(client, document)
        # Le rejeu hors connexion ne doit pas faire passer des données anciennes
        # pour une synchronisation récente ni altérer la future base active.
        database = private / ("notes-replay.sqlite3" if args.from_capture else "notes.sqlite3")
        result = synchronize(database, document)
    print(json.dumps({"mode": "offline_replay" if args.from_capture else "live",
                      **result}, ensure_ascii=False, indent=2))


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
        print("Erreur réseau, capture ou stockage. Synchronisation non validée.", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("Synchronisation annulée.", file=sys.stderr)
        sys.exit(1)
