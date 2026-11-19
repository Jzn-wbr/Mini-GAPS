"""Chargement HTTP et AJAX des notes."""
import json
from urllib.parse import urlencode
from gaps_scraper.portal.client import CaptureError,authenticate,login_forms
from gaps_scraper.notes.structure import inspect_notes,selectors_from_page,StructureError

def decode_ajax_result(response):
    """Suit les deux décodages JSON du client SAJAX/PageHelper, sans eval."""
    response = response.strip()
    if not response.startswith("+:"):
        raise CaptureError("Réponse AJAX refusée ou structure inattendue.")
    try:
        payload = json.loads(response[2:])
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict) or not isinstance(payload.get("parts"), dict):
            raise ValueError
        result = payload["parts"].get("result")
        if not isinstance(result, str) or not result.strip() or login_forms(result):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise CaptureError("Réponse AJAX non reconnue : aucune liste vide déduite.") from None
    return result

def selection_arguments(parser):
    parser.add_argument("--year", type=int, help="Année de début, ex. 2025 pour 2025–2026.")
    parser.add_argument("--semester", choices=("S1", "S2"), help="Semestre explicite.")

def fetch_notes(client, username, password, *, year=None, semester=None):
    if (year is None) != (semester is None):
        raise CaptureError("Préciser ensemble --year et --semester.")
    if year is not None and (type(year) is not int or not 2000 <= year <= 2100 or semester not in ("S1", "S2")):
        raise CaptureError("Année ou semestre invalide.")
    student, student_id = authenticate(client, username, password)
    notes = client.fetch("/consultation/controlescontinus/consultation.php")
    if year is not None:
        requested = {"idst": student_id, "year": str(year), "idas": "0" if semester == "S1" else "1"}
        notes = client.fetch("/consultation/controlescontinus/consultation.php?" + urlencode(requested))
    if login_forms(notes):
        raise CaptureError("La page des notes redemande une connexion.")
    # Le HTML initial est un placeholder ; le navigateur remplace #result via AJAX.
    try:
        initial = inspect_notes(notes)
    except StructureError:
        raise CaptureError("Sélection des notes ou structure de page inconnue.") from None
    if year is not None and (initial["academic_year"]["value"] != str(year)
                             or initial["semester"]["value"] != requested["idas"]):
        raise CaptureError("GAPS n'a pas appliqué l'année et le semestre demandés : arrêt.")
    query = urlencode({"idst": student_id,
                       "year": initial["academic_year"]["value"],
                       "idas": initial["semester"]["value"]})
    ajax = client.fetch("/consultation/controlescontinus/consultation.php?" + query,
                        {"rs": "replaceHtmlPart", "rsargs": json.dumps(["result", None])})
    result = decode_ajax_result(ajax)
    try:
        observation = inspect_notes(selectors_from_page(notes) + result, ajax_completed=True)
    except StructureError:
        raise CaptureError("Résultat AJAX inconnu : capture précédente conservée.") from None
    return {"student.html": student, "notes.html": notes, "notes-result.html": result}, observation
