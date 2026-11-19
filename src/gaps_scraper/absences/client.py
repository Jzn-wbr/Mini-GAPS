"""Reproduit uniquement les opérations de consultation des absences de GAPS."""

from hashlib import sha256
import json
import re

from gaps_scraper.absences.parser import dom, selected_option
from gaps_scraper.portal.client import BASE, CaptureError, authenticate, login_forms

YEAR_SELECTOR = "studentAbsGrid_yearSelectorId"
RATE_SELECTOR = "studentAbsGrid_rateSelectorId"


def smart_part(client, arguments, target):
    response = client.fetch("/consultation/etudiant/", {
        "rs": "smartReplacePart", "rsargs": json.dumps(arguments)})
    try:
        if not response.strip().startswith("+:"):
            raise ValueError
        payload = json.loads(response.strip()[2:])
        if not isinstance(payload, str):
            raise ValueError
        parts = payload.split("@££@")
        if len(parts) != 3 or parts[0].strip() or not parts[1].strip() or login_forms(parts[1]):
            raise ValueError
        roots = [n for n in dom(parts[1]).descendants("div") if n.attrs.get("id") == target]
        if len(roots) != 1:
            raise ValueError
    except (ValueError, TypeError):
        raise CaptureError("Réponse de consultation des absences refusée ou inconnue.") from None
    return parts[1]


def fetch_absences(client, username, password, *, year=None):
    if year is not None and (type(year) is not int or not 2000 <= year <= 2100):
        raise CaptureError("Année d'absences invalide.")
    student, sid = authenticate(client, username, password)
    page_ids = [n.attrs.get("value") for n in dom(student).descendants("input") if n.attrs.get("id") == "upid"]
    if len(page_ids) != 1 or not page_ids[0]:
        raise CaptureError("Identifiant de page absent ou ambigu.")
    page_id = page_ids[0]
    details = smart_part(client, ["STUDENT_SELECT_ID", "studentDataDiv", page_id, None, None, int(sid), None], "studentDataDiv")
    root = dom(details)
    selected_year, years = selected_option(root, YEAR_SELECTOR)
    _, rates = selected_option(root, RATE_SELECTOR)
    chosen_year = str(year) if year is not None else selected_year
    if not re.fullmatch(r"\d{4}", chosen_year) or chosen_year not in {n.attrs.get("value") for n in years}:
        raise CaptureError("Année d'absences non proposée par GAPS.")
    all_options = [n for n in rates if n.attrs.get("value") == "0"]
    if len(all_options) != 1 or " ".join(all_options[0].text().split()) != "Toutes les absences":
        raise CaptureError("Option Toutes les absences non reconnue.")
    result = smart_part(client, [RATE_SELECTOR, "studentAbsGrid", page_id, None, None,
                                 chosen_year, "0", int(sid), None], "studentAbsGrid")
    account_id = sha256((BASE + ":" + sid).encode()).hexdigest()
    return result, {"year": chosen_year, "account_id": account_id}
