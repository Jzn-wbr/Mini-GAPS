"""Point d'entrée commun : python -m gaps_scraper <commande>."""

import argparse
import runpy
import sys


COMMANDS = {
    "scheduler": ("jobs.daemon", "Lancer le cron du conteneur scraper"),
    "sync": ("jobs.sync", "Synchroniser les trois catégories en une connexion"),
    "api": ("api.serve", "Lancer l'API privée"),
    "devices": ("api.devices", "Associer, lister ou révoquer un appareil"),
    "push": ("notifications.cli", "Configurer et envoyer les notifications"),
    "notes": ("jobs.notes", "Synchroniser les notes"),
    "absences": ("jobs.absences", "Synchroniser toutes les absences"),
    "horaire": ("jobs.schedule", "Synchroniser l'horaire individuel"),
    "capture": ("diagnostics.capture", "Capturer les pages des notes pour diagnostic"),
    "inspect": ("diagnostics.inspect", "Inspecter une capture locale"),
}


def main():
    parser = argparse.ArgumentParser(prog="python -m gaps_scraper", description="Consultation personnelle de GAPS")
    commands = parser.add_subparsers(dest="command", required=True)
    for command, (_, description) in COMMANDS.items():
        commands.add_parser(command, help=description, add_help=False)
    args, remainder = parser.parse_known_args()
    sys.argv = [f"python -m gaps_scraper {args.command}", *remainder]
    runpy.run_module("gaps_scraper."+COMMANDS[args.command][0], run_name="__main__")


if __name__ == "__main__":
    main()
