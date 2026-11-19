"""Un passage des trois récupérations, une connexion GAPS, puis arrêt."""

import argparse
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
import sqlite3
import sys
from urllib.error import HTTPError, URLError

from gaps_scraper.absences.client import fetch_absences
from gaps_scraper.absences.parser import parse_absences
from gaps_scraper.absences.store import synchronize_absences
from gaps_scraper.api.auth import known_accounts
from gaps_scraper.portal.client import BASE, CaptureError, Client, authenticate
from gaps_scraper.notes.client import fetch_notes
from gaps_scraper.common.credentials import credentials, CredentialsError
from gaps_scraper.common.parsing import ParseError
from gaps_scraper.notes.programme import enrich_notes
from gaps_scraper.notes.parser import parse_notes
from gaps_scraper.common.storage import canonical
from gaps_scraper.notes.store import synchronize
from gaps_scraper.schedule.client import fetch_schedule
from gaps_scraper.schedule.parser import parse_schedule
from gaps_scraper.schedule.store import synchronize_schedule
from gaps_scraper.common.state import COMPONENTS, initialize_state, record_attempt, scheduled
from gaps_scraper.common.storage import data_directory
from gaps_scraper.common.runtime import configure_logging


def error_code(exc):
    if isinstance(exc,HTTPError):
        return "authentication" if exc.code in (401,403) else "portal_unavailable"
    if isinstance(exc,(URLError,OSError)):
        return "network"
    if isinstance(exc,ParseError):
        return "parsing"
    if isinstance(exc,sqlite3.Error):
        return "storage"
    return "portal_or_session"


def sync_all(client, username, password, path):
    initialize_state(path)
    try:
        _,sid=authenticate(client,username,password)
    except (CaptureError,HTTPError,URLError,OSError) as exc:
        code=error_code(exc)
        for account in known_accounts(path):
            for component in COMPONENTS:
                record_attempt(path,account,component,error=code)
        return {component:{"ok":False,"error":code} for component in COMPONENTS}
    account=sha256((BASE+":"+sid).encode()).hexdigest()
    results={}
    for component in COMPONENTS:
        try:
            if component == "notes":
                sources,_=fetch_notes(client,username,password)
                document=parse_notes(sources["notes.html"],sources["notes-result.html"])
                document["account_id"]=account
                enrich_notes(client,document)
                result=synchronize(path,document)
                scope=canonical([account,document["academic_year"]["value"],document["semester"]["value"]])
            elif component == "absences":
                source,metadata=fetch_absences(client,username,password)
                document=parse_absences(source,**metadata)
                result=synchronize_absences(path,document)
                scope=canonical([account,document["academic_year"]["value"]])
            else:
                source,metadata=fetch_schedule(client,username,password)
                document=parse_schedule(source,metadata)
                result=synchronize_schedule(path,document)
                scope=canonical([account,document["academic_year"]["value"],document["source_period"]])
            record_attempt(path,account,component,scope=scope)
            results[component]={"ok":True,**result}
        except (CaptureError,ParseError,HTTPError,URLError,OSError,sqlite3.Error) as exc:
            code=error_code(exc)
            record_attempt(path,account,component,error=code)
            results[component]={"ok":False,"error":code}
    return results


def report_results(results, *, scheduled_run, logger):
    if not scheduled_run:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    # Codes contrôlés seulement : aucune exception brute ni contenu scolaire.
    errors = {name: result["error"] for name, result in results.items() if not result["ok"]}
    if errors:
        logger.warning("Échec de synchronisation : %s", json.dumps(errors))
    else:
        logger.info("Synchronisation réussie : %s", json.dumps(results))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduled",action="store_true",help="N'exécuter que les créneaux 06h00–23h50 en Suisse.")
    args=parser.parse_args()
    logger = configure_logging()
    if args.scheduled and not scheduled(datetime.now(timezone.utc)):
        logger.info("Hors créneau de synchronisation : aucun accès à GAPS.")
        return
    os.umask(0o077)
    private=data_directory();private.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (private/".capture.lock").open("a") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Synchronisation déjà en cours : passage ignoré.")
            return
        username,password=credentials()
        results=sync_all(Client(),username,password,private/"notes.sqlite3")
        # Même verrou pour la synchronisation et l'envoi des messages en attente.
        from gaps_scraper.api.auth import initialize_auth
        from gaps_scraper.notifications.store import initialize_push, enqueue
        from gaps_scraper.notifications.sender import deliver
        from gaps_scraper.notifications.cli import key_path
        path=private/'notes.sqlite3'
        try:
            initialize_auth(path);initialize_push(path);enqueue(path)
            push_result = deliver(path,key_path())
            if args.scheduled:
                if push_result.get('processed', 0) > push_result.get('accepted_by_provider', 0):
                    logger.warning('Notifications non acceptées : %s', json.dumps(push_result))
                else:
                    logger.info('Notifications : %s', json.dumps(push_result))
            else:
                print(json.dumps({'notifications':push_result},ensure_ascii=False))
        except (ValueError,OSError,sqlite3.Error):
            logger.warning('Notifications en attente : configuration ou stockage indisponible.')
    report_results(results, scheduled_run=args.scheduled, logger=logger)
    if any(not result["ok"] for result in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (CredentialsError,sqlite3.Error,OSError):
        print("Synchronisation impossible : configuration ou stockage indisponible.",file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt,EOFError):
        print("Synchronisation annulée.",file=sys.stderr)
        sys.exit(1)
