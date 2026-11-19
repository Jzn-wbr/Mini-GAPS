"""Utilise le lien d'export individuel réellement proposé par GAPS."""

from hashlib import sha256
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from gaps_scraper.portal.client import BASE, CaptureError, authenticate, check_origin, login_forms
from gaps_scraper.common.parsing import Tree, clean


def export_selection(page, student_id):
    tree = Tree()
    tree.feed(page)
    links = []
    for node in tree.root.descendants("a"):
        href = node.attrs.get("href", "")
        if "icalendarversion" in parse_qs(urlsplit(href).query):
            links.append(href)
    if len(links) != 1 or login_forms(page):
        raise CaptureError("Lien d'export individuel absent ou ambigu.")
    url = urljoin(BASE + "/consultation/horaires/", links[0])
    check_origin(url)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    keys = {"annee", "trimestre", "type", "id", "icalendarversion", "individual"}
    if (parsed.path != "/consultation/horaires/" or set(query) != keys
            or any(len(v) != 1 for v in query.values())
            or query["id"] != [student_id] or query["type"] != ["2"]
            or query["individual"] != ["1"] or query["icalendarversion"] != ["2"]
            or not re.fullmatch(r"\d{4}", query["annee"][0])
            or not re.fullmatch(r"\d+", query["trimestre"][0])):
        raise CaptureError("Export d'horaire non reconnu ou destiné à un autre compte.")
    # Le code période de l'horaire diffère de celui des notes : lire le libellé.
    labels = []
    for node in tree.root.descendants("span"):
        match = re.fullmatch(r"Semestre ([12]) - (\d{4})/(\d{4})", clean(node.text()))
        if match:
            labels.append(match.groups())
    if (len(labels) != 1 or labels[0][1] != query["annee"][0]
            or int(labels[0][2]) != int(labels[0][1])+1):
        raise CaptureError("Sélection du semestre de l'horaire non reconnue.")
    return url, {"academic_year": {"value": labels[0][1], "label": f"{labels[0][1]}-{labels[0][2]}"},
                 "semester": {"value": "S"+labels[0][0], "label": "Semestre "+labels[0][0]},
                 "source_period": query["trimestre"][0],
                 "account_id": sha256((BASE+":"+student_id).encode()).hexdigest()}


def fetch_schedule(client, username, password):
    _, student_id = authenticate(client, username, password)
    page = client.fetch("/consultation/horaires/")
    url, selection = export_selection(page, student_id)
    source = client.fetch(url)
    if not source.lstrip().startswith("BEGIN:VCALENDAR"):
        raise CaptureError("L'export ne renvoie pas un calendrier ; session expirée ou format inconnu.")
    return source, selection
