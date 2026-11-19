"""Commandes locales d'association et de révocation ; aucune route d'administration."""

import argparse
import json
import os
import sqlite3
import sys

from gaps_scraper.api.auth import AuthError, initialize_auth, issue_pairing, list_devices, revoke_device
from gaps_scraper.common.storage import database_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action",required=True)
    pairing = commands.add_parser("pair",help="Générer un code temporaire")
    pairing.add_argument("--account",help="Identifiant local si plusieurs comptes existent")
    commands.add_parser("list",help="Lister les appareils associés, sans leurs jetons")
    revoke = commands.add_parser("revoke",help="Révoquer un appareil")
    revoke.add_argument("id")
    args = parser.parse_args()
    os.umask(0o077)
    path = database_path()
    initialize_auth(path)
    if args.action == "pair":
        code = issue_pairing(path,args.account)
        print("Code d'association — une seule utilisation, valable 10 minutes :")
        print(code)  # Sortie volontaire réservée à cette commande locale.
    elif args.action == "list":
        print(json.dumps(list_devices(path),ensure_ascii=False,indent=2))
    elif not revoke_device(path,args.id):
        raise AuthError("Appareil inconnu.")
    else:
        print("Appareil révoqué.")


if __name__ == "__main__":
    try:
        main()
    except (AuthError,sqlite3.Error,OSError):
        print("Commande impossible : vérifier le compte, l'appareil et le stockage local.",file=sys.stderr)
        sys.exit(1)
