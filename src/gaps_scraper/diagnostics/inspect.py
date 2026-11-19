"""Inspecte une capture locale sans afficher les notes."""
import argparse,json,sys
from pathlib import Path
from gaps_scraper.notes.structure import inspect_notes,selectors_from_page,StructureError
from gaps_scraper.common.storage import data_directory

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path)
    args = parser.parse_args()
    directory = args.directory
    if directory is None:
        manifests = list(data_directory().glob("capture-*/manifest.json"))
        if not manifests:
            raise StructureError("Aucune capture terminée trouvée.")
        directory = max(manifests, key=lambda p: p.stat().st_mtime_ns).parent
    source = (directory / "notes.html").read_text(encoding="utf-8")
    result_file = directory / "notes-result.html"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if result_file.is_file() and manifest.get("notes_ajax_completed") is True:
        source = selectors_from_page(source) + result_file.read_text(encoding="utf-8")
        result = inspect_notes(source, ajax_completed=True)
    else:
        result = inspect_notes(source)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    try:
        main()
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("Capture illisible.", file=sys.stderr)
        sys.exit(1)
    except StructureError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
