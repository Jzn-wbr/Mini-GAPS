"""Capture ponctuelle privée, uniquement pour diagnostiquer le parsing."""
import argparse,fcntl,html,json,os,tempfile,time,sys
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import quote
from gaps_scraper.portal.client import Client,CaptureError
from gaps_scraper.notes.client import fetch_notes,selection_arguments
from gaps_scraper.common.credentials import credentials,CredentialsError
from gaps_scraper.common.storage import data_directory

def redact_known_secrets(source, secrets):
    # Réduction du risque uniquement : ces pages restent des données privées.
    variants = set()
    for secret in secrets:
        if secret:
            variants.update((secret, html.escape(secret), quote(secret, safe=""),
                             json.dumps(secret, ensure_ascii=True)[1:-1]))
    for value in sorted(variants, key=len, reverse=True):
        source = source.replace(value, "[SECRET_RETIRE]")
    return source

def capture(client, username, password, root, *, year=None, semester=None):
    sources, observation = fetch_notes(client, username, password, year=year, semester=semester)
    secrets = [username, password] + [c.value for c in client.cookies]
    # N'écrire qu'après réception des deux pages ; ne jamais remplacer une capture.
    directory = Path(tempfile.mkdtemp(prefix="capture-", dir=root))
    for name, source in sources.items():
        path = directory / name
        path.touch(mode=0o600)
        path.write_text(redact_known_secrets(source, secrets), encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "captured_at_unix": int(time.time()),
        "authentication": "student_marker_confirmed",
        "files": ["student.html", "notes.html", "notes-result.html"],
        "scope": "authenticated_notes_with_ajax",
        "notes_ajax_completed": True,
        "notes_observation": observation,
        "grades_parsed": False,
        "default_term_verified": False,
    }
    path = directory / "manifest.json"
    path.touch(mode=0o600)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return directory

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection_arguments(parser)
    args = parser.parse_args()
    os.umask(0o077)
    root = data_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".capture.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CaptureError("Une capture est déjà en cours.") from None
        username, password = credentials()
        directory = capture(Client(), username, password, root, year=args.year, semester=args.semester)
    print(f"Capture privée créée : {directory}")
    print("Connexion et réponse AJAX vérifiées. Parsing métier des notes encore à valider.")
    print("Ne partage pas les fichiers HTML bruts : ils contiennent des données personnelles.")

if __name__ == "__main__":
    try:
        main()
    except (CaptureError, CredentialsError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except HTTPError as exc:
        print(f"Échec HTTP {exc.code}. Aucune nouvelle tentative automatique.", file=sys.stderr)
        exc.close()
        sys.exit(1)
    except (URLError, TimeoutError, OSError, ValueError, LookupError):
        print("Erreur réseau, encodage ou stockage. Aucune nouvelle tentative automatique.", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("Capture annulée.", file=sys.stderr)
        sys.exit(1)
