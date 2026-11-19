"""Reconnaissance des sélecteurs et de la zone de résultats de GAPS."""
from html.parser import HTMLParser
import re

class StructureError(ValueError):
    pass

def normalized(text):
    return " ".join(text.split())

class NotesPage(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.selections = {}
        self.select = None
        self.option = None
        self.results = []
        self.result_tags = []
        self.result = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.select = {"id": attrs.get("id"), "options": []}
        elif tag == "option" and self.select is not None:
            self.finish_option()
            self.option = {"value": attrs.get("value"), "selected": "selected" in attrs,
                           "text": []}
        if attrs.get("id") == "result":
            if self.result is not None:
                raise StructureError("Résultats imbriqués inattendus.")
            self.result = {"text": [], "grade_tables": 0}
            self.result_tags = []
        if self.result is not None:
            if tag not in self.VOID:
                self.result_tags.append(tag)
            if tag == "table" and "displayArray" in attrs.get("class", "").split():
                self.result["grade_tables"] += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def finish_option(self):
        if self.option is not None:
            self.option["label"] = normalized("".join(self.option.pop("text")))
            self.select["options"].append(self.option)
            self.option = None

    def handle_endtag(self, tag):
        if tag == "option":
            self.finish_option()
        elif tag == "select" and self.select is not None:
            self.finish_option()
            name = self.select["id"]
            if name in ("selyear", "selta"):
                if name in self.selections:
                    raise StructureError("Sélecteur dupliqué.")
                self.selections[name] = self.select["options"]
            self.select = None
        if self.result is not None and tag in self.result_tags:
            index = len(self.result_tags) - 1 - self.result_tags[::-1].index(tag)
            self.result_tags = self.result_tags[:index]
            if not self.result_tags:
                self.results.append(self.result)
                self.result = None

    def handle_data(self, text):
        if self.option is not None:
            self.option["text"].append(text)
        if self.result is not None and not {"script", "style"}.intersection(self.result_tags):
            self.result["text"].append(text)

    def selected(self, name):
        options = self.selections.get(name, [])
        marked = [o for o in options if o["selected"]]
        if not options or len(marked) > 1:
            raise StructureError("Sélection par défaut absente ou ambiguë.")
        option = (marked or options)[0]
        if option["value"] is None:
            raise StructureError("Valeur de sélection absente.")
        return {"value": option["value"], "label": option["label"]}

def inspect_notes(source, *, ajax_completed=False):
    page = NotesPage()
    page.feed(source)
    year = page.selected("selyear")
    semester = page.selected("selta")
    if not re.fullmatch(r"\d{4}", year["value"]) or semester["value"] not in ("-1", "0", "1"):
        raise StructureError("Valeurs année/semestre inconnues.")
    if len(page.results) != 1 or page.result is not None:
        raise StructureError("Zone de résultat absente, dupliquée ou incomplète.")
    result = page.results[0]
    text = normalized("".join(result["text"]))
    if text == "Aucun contrôle continu trouvé" and not result["grade_tables"]:
        state = "explicit_empty" if ajax_completed else "initial_empty_unverified"
    elif result["grade_tables"] and text != "Aucun contrôle continu trouvé":
        state = "grade_tables_present_unparsed"
    else:
        raise StructureError("Contenu inconnu : ne pas interpréter comme une liste vide.")
    return {"schema_version": 1, "kind": "notes_page_observation",
            "academic_year": year, "semester": semester, "page_state": state,
            "grades_parsed": False, "synchronization_validated": False}

def selectors_from_page(source):
    """Conserve les sélecteurs validés sans réinjecter le résultat initial."""
    from html import escape
    page = NotesPage()
    page.feed(source)
    elements = []
    for name in ("selyear", "selta"):
        selected = page.selected(name)
        elements.append(f'<select id="{name}"><option selected value="'
                        + escape(selected["value"], quote=True) + '">'
                        + escape(selected["label"]) + '</option></select>')
    return "".join(elements)
