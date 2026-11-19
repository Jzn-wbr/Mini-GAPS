"""Analyse stricte du tableau annuel des absences, sans identité personnelle."""

from hashlib import sha256
import re

from gaps_scraper.common.parsing import Node, Tree, clean, number, ParseError


class AbsenceTree(Tree):
    """GAPS omet certaines fermetures td/tr, autorisées par HTML."""
    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            # Une cellule peut contenir des div, mais pas une cellule suivante.
            for node in reversed(self.stack):
                if node.tag in ("tr", "table"):
                    break
                if node.tag in ("td", "th"):
                    self.handle_endtag(node.tag)
                    break
        elif tag == "tr" and any(n.tag == "tr" for n in self.stack):
            self.handle_endtag("tr")
        super().handle_starttag(tag, attrs)


def dom(source):
    tree = AbsenceTree()
    tree.feed(source)
    return tree.root


def selected_option(root, identity):
    selects = [n for n in root.descendants("select") if n.attrs.get("id") == identity]
    if len(selects) != 1:
        raise ParseError("Sélecteur d'absences absent ou dupliqué.")
    options = list(selects[0].descendants("option"))
    selected = [n for n in options if "selected" in n.attrs]
    if len(selected) != 1 or not selected[0].attrs.get("value"):
        raise ParseError("Sélection des absences ambiguë.")
    return selected[0].attrs["value"], options


def count_cell(text):
    text = clean(text)
    if not text:
        return None
    match = re.fullmatch(r"(\d+)(?:\s*\[(\d+)\])?", text)
    if not match:
        raise ParseError("Compteur d'absences inconnu.")
    total, justified = int(match[1]), int(match[2] or 0)
    if justified > total:
        raise ParseError("Compteur de justifications incohérent.")
    return {"total": total, "justified": justified, "unjustified": total - justified}


def integer(text):
    if not re.fullmatch(r"\d+", clean(text)):
        raise ParseError("Nombre de périodes inconnu.")
    return int(clean(text))


def percent(text):
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*%", clean(text))
    if not match:
        raise ParseError("Taux d'absence inconnu.")
    return number(match[1])


def parse_absences(source, *, year, account_id):
    root = dom(source)
    grids = [n for n in root.descendants("div") if n.attrs.get("id") == "studentAbsGrid"]
    if len(grids) != 1:
        raise ParseError("Zone des absences absente ou dupliquée.")
    tables = [n for n in grids[0].descendants("table")
              if "absenceArray" in n.attrs.get("class", "").split()]
    if len(tables) != 1:
        raise ParseError("Tableau des absences absent ou dupliqué.")
    rows = list(tables[0].descendants("tr"))
    def cells(row):
        return [n for n in row.children if isinstance(n, Node) and n.tag in ("td", "th")]
    expected = ["Etudiant-e", "Orientation", "Unites", "E", "T1", "T2", "T3", "T4", "Total", "Relatif", "Absolu"]
    expected_sub = ["Périodes", "Taux", "Taux hors justifiées"] * 2
    if len(rows) < 3 or [clean(n.text()) for n in cells(rows[0])] != expected or [clean(n.text()) for n in cells(rows[1])] != expected_sub:
        raise ParseError("Colonnes du tableau des absences inconnues.")
    # La légende n'est ajoutée par GAPS que si des justifications sont présentes.
    footer = cells(rows[-1])
    if (len(footer) == 2 and not clean(footer[0].text())
            and footer[1].attrs.get("colspan") == "14"
            and clean(footer[1].text()).startswith("Les valeurs indiquent toutes les absences, justifiées ou non.")
            and clean(footer[1].text()).endswith("apparaît entre [].")):
        rows = rows[:-1]
    courses, identities = [], set()
    for index, row in enumerate(rows[2:], start=2):
        columns = cells(row)
        # Le générateur GAPS laisse une dernière ligne <tr class=a_r_0> vide.
        if not columns and index == len(rows)-1 and "a_r_0" in row.attrs.get("class", "").split():
            continue
        if "a_r_0" not in row.attrs.get("class", "").split():
            raise ParseError("Ligne d'absences inconnue.")
        columns = [n for n in columns if "s_cell" not in n.attrs.get("class", "").split()]
        if len(columns) != 13 or "l_cell" not in columns[0].attrs.get("class", "").split():
            raise ParseError("Structure d'une ligne d'absences inconnue.")
        name = clean(columns[0].text())
        identity = sha256((account_id + ":" + str(year) + ":" + name).encode()).hexdigest()
        if not name or identity in identities:
            raise ParseError("Cours d'absences vide ou dupliqué.")
        identities.add(identity)
        periods = {label: count_cell(columns[i+1].text()) for i, label in enumerate(("E", "T1", "T2", "T3", "T4"))}
        courses.append({"id": identity, "name": name, "periods": periods,
                        "total": count_cell(columns[6].text()),
                        "relative": {"periods": integer(columns[7].text()),
                                     "rate_percent": percent(columns[8].text()),
                                     "unjustified_rate_percent": percent(columns[9].text())},
                        "absolute": {"periods": integer(columns[10].text()),
                                     "rate_percent": percent(columns[11].text()),
                                     "unjustified_rate_percent": percent(columns[12].text())}})
        if courses[-1]["total"] is None:
            raise ParseError("Total d'absences non renseigné.")
    if not courses:
        raise ParseError("Tableau sans cours : absence de données non encore confirmée.")
    return {"schema_version": 1, "kind": "annual_absence_summary", "account_id": account_id,
            "academic_year": {"value": str(year), "label": f"{year}-{int(year)+1}"},
            "requested_rate_filter": "0", "filter_label": "Toutes les absences",
            "timezone": "Europe/Zurich", "courses": sorted(courses, key=lambda c: c["id"])}
