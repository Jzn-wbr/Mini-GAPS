"""Parsing strict des notes, sans recalcul des moyennes."""
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json,re
from gaps_scraper.common.parsing import Node,Tree,clean,number,ParseError
from gaps_scraper.notes.structure import inspect_notes,selectors_from_page,StructureError

def grade_value(text):
    text = clean(text)
    if text == "-":
        return {"value": None, "status": "unpublished", "ignored": False, "absent": False}
    ignored = text.startswith("[") and text.endswith("]")
    if ignored:
        text = text[1:-1].strip()
    absent = text.startswith("a ")
    if absent:
        text = text[2:].strip()
    value = number(text)
    if not Decimal("1") <= Decimal(value) <= Decimal("6"):
        raise ParseError("Note hors de la plage attendue.")
    return {"value": value, "status": "published", "ignored": ignored, "absent": absent}

def stable_id(year, semester, course, group, day, description):
    # Provisoire : une correction de date/intitulé change cette identité.
    key = [year, semester, course, group, day, description]
    return sha256(json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def parse_notes(page_source, result_source):
    try:
        observation = inspect_notes(selectors_from_page(page_source) + result_source,
                                    ajax_completed=True)
    except StructureError:
        raise ParseError("Structure de la page des notes inconnue.") from None
    document = Tree()
    document.feed(result_source)
    tables = [t for t in document.root.descendants("table")
              if "displayArray" in t.attrs.get("class", "").split()]
    courses, assessments, identities = [], [], set()
    course = group = None
    course_names = set()
    year = observation["academic_year"]["value"]
    semester = observation["semester"]["value"]
    for table in tables:
        course = group = None
        for row in table.descendants("tr"):
            cells = [c for c in row.children if isinstance(c, Node) and c.tag in ("td", "th")]
            if not cells:
                raise ParseError("Ligne de tableau vide ou imbriquée inconnue.")
            if len(cells) == 1 and "bigheader" in cells[0].attrs.get("class", "").split():
                match = re.fullmatch(r"(.+?)\s+-\s+moyenne\s*(hors examen)?\s*:\s*(\d+(?:[.,]\d+)?|-)",
                                     clean(cells[0].text()))
                if not match:
                    raise ParseError("En-tête de matière inconnu.")
                name = clean(match[1])
                if name in course_names:
                    raise ParseError("Matière dupliquée : identité à clarifier.")
                course_names.add(name)
                course = {"name": name, "mean": number(match[3], nullable=True),
                          "mean_excludes_exam": bool(match[2]), "groups": []}
                courses.append(course)
                group = None
            elif len(cells) == 6 and "rowspan" in cells[0].attrs:
                if course is None:
                    raise ParseError("Groupe sans matière.")
                headers = [clean(c.text()).lower() for c in cells[1:]]
                if headers != ["date", "descriptif", "moyenne", "coef.", "note"]:
                    raise ParseError("Colonnes de notes inconnues.")
                match = re.fullmatch(r"(.+?)\s+moyenne\s*:\s*(\d+(?:[.,]\d+)?|-)\s+poids\s*:\s*(\d+(?:[.,]\d+)?)",
                                     clean(cells[0].text()))
                if not match:
                    raise ParseError("En-tête de groupe inconnu.")
                group = {"name": clean(match[1]), "mean": number(match[2], nullable=True),
                         "weight": number(match[3])}
                if any(g["name"] == group["name"] for g in course["groups"]):
                    raise ParseError("Groupe dupliqué : identité à clarifier.")
                course["groups"].append(group)
            elif len(cells) == 5:
                if course is None or group is None:
                    raise ParseError("Épreuve sans matière ou groupe.")
                try:
                    day = datetime.strptime(clean(cells[0].text()), "%d.%m.%Y").date().isoformat()
                except ValueError:
                    raise ParseError("Date de l'épreuve invalide.") from None
                description = clean(cells[1].text())
                if not description:
                    raise ParseError("Intitulé d'épreuve vide.")
                # Les descriptions dépliables nécessitent une validation spécifique.
                if any("onclick" in n.attrs for n in cells[1].descendants("div")):
                    raise ParseError("Description dépliable non encore prise en charge.")
                weight = re.fullmatch(r"(\d+)(?:\s*/\s*(\d+))?\s*\((\d+(?:[.,]\d+)?)\s*%\)", clean(cells[3].text()))
                if not weight or (weight[2] is not None and int(weight[2]) <= 0):
                    raise ParseError("Pondération inconnue.")
                percentage = number(weight[3])
                # GAPS affiche aussi des rapports > 1 (ex. 3/2, 150 %).
                # Conserver la pondération source sans la plafonner à 100 %.
                identity = stable_id(year, semester, course["name"], group["name"], day, description)
                if identity in identities:
                    raise ParseError("Épreuves indiscernables : aucune ne sera écrasée.")
                identities.add(identity)
                assessments.append({"id": identity, "course": course["name"], "group": group["name"],
                                    "date": day, "description": description,
                                    "class_mean": number(cells[2].text(), nullable=True),
                                    "weight": {"numerator": int(weight[1]), "denominator": int(weight[2] or "1"),
                                               "percent": percentage},
                                    "grade": grade_value(cells[4].text())})
            else:
                raise ParseError("Ligne de notes inconnue : synchronisation annulée.")
    if observation["page_state"] != "explicit_empty" and not assessments:
        raise ParseError("Tableaux présents sans épreuve reconnue.")
    for item in courses:
        item["groups"].sort(key=lambda g: g["name"])
    courses.sort(key=lambda c: c["name"])
    return {"schema_version": 1, "academic_year": observation["academic_year"],
            "semester": observation["semester"], "timezone": "Europe/Zurich",
            "identity_strategy": "provisional_context_date_description_sha256",
            "courses": courses, "assessments": sorted(assessments, key=lambda a: a["id"]),
            "source_state": "explicit_empty" if not assessments else "assessments_present"}
