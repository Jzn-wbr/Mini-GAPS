"""Configuration locale et livraison des notifications en attente."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys

from gaps_scraper.api.auth import initialize_auth
from gaps_scraper.notifications.sender import deliver, initialize_keys
from gaps_scraper.notifications.store import enqueue, initialize_push
from gaps_scraper.common.storage import data_directory, database_path


def key_path():
    return Path(os.environ.get('GAPS_VAPID_KEY','data/push/vapid.pem'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='action',required=True)
    init=commands.add_parser('init');init.add_argument('--subject',required=True,help='URL HTTPS de contact ou mailto:adresse')
    commands.add_parser('send',help='Traiter la file persistante')
    args=parser.parse_args();os.umask(0o077)
    path=database_path();initialize_auth(path);initialize_push(path)
    with (data_directory()/'.capture.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.action=='init':
            initialize_keys(path,key_path(),args.subject);print('Clés Web Push prêtes ; clé privée conservée localement.')
        else:
            enqueue(path);print(json.dumps(deliver(path,key_path()),ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,sqlite3.Error):
        print('Opération Web Push impossible : vérifier la configuration locale.',file=sys.stderr);sys.exit(1)
