"""Client HTTP et authentification GAPS, sans navigateur automatisé."""
import http.cookiejar
from html.parser import HTMLParser
import re
from urllib.parse import urlencode,urljoin,urlsplit
from urllib.request import build_opener,HTTPCookieProcessor,HTTPRedirectHandler,Request

BASE="https://gaps.heig-vd.ch"
MAX_BYTES=4*1024*1024

class CaptureError(Exception):
    pass

def check_origin(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "gaps.heig-vd.ch"
            or parts.port not in (None, 443) or parts.username or parts.password):
        raise CaptureError("Redirection externe ou SSO : arrêt sans transmettre les identifiants.")

class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_origin(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action", ""),
                            "method": attrs.get("method", "get").lower(), "fields": {}}
        elif tag == "input" and self.current is not None and attrs.get("name"):
            self.current["fields"][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None

def login_forms(source):
    parser = LoginForm()
    parser.feed(source)
    return [f for f in parser.forms if {"login", "password"} <= f["fields"].keys()]

def authenticated_student(source):
    matches = re.findall(r"\bconst\s+DEFAULT_STUDENT_ID\s*=\s*(\d+)\s*;", source)
    if login_forms(source) or len(set(matches)) != 1 or int(matches[0]) <= 0:
        raise CaptureError("Connexion non validée : session refusée/expirée ou structure inattendue.")
    return matches[0]

class Client:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()  # Jamais écrits sur disque.
        self.opener = build_opener(SameOriginRedirect(), HTTPCookieProcessor(self.cookies))

    def fetch(self, path, fields=None):
        url = urljoin(BASE, path)
        check_origin(url)
        request = Request(url, data=None if fields is None else urlencode(fields).encode(),
                          headers={"User-Agent": "PersonalGapsCapture/0.1"})
        with self.opener.open(request, timeout=20) as response:
            check_origin(response.url)
            if response.status != 200:
                raise CaptureError("Réponse HTTP inattendue.")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise CaptureError("Réponse trop volumineuse : capture arrêtée.")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                return raw.decode(charset)
            except UnicodeDecodeError:
                return raw.decode("windows-1252")

def authenticate(client, username, password):
    cached = getattr(client, "authentication", None)
    if cached is not None:
        return cached
    landing = client.fetch("/consultation/")
    forms = login_forms(landing)
    if len(forms) != 1 or forms[0]["method"] != "post":
        raise CaptureError("Formulaire direct absent ou ambigu : aucun identifiant envoyé.")
    form = forms[0]
    target = urljoin(BASE + "/consultation/", form["action"])
    check_origin(target)
    client.fetch(target, {**form["fields"], "login": username, "password": password})
    student = client.fetch("/consultation/etudiant/")
    student_id = authenticated_student(student)
    client.authentication = (student, student_id)
    return student, student_id
