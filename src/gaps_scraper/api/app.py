"""API privée pour la PWA ; aucune route ne déclenche le scraper."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from gaps_scraper.api.auth import (
    AuthError, PairRateLimit, authenticate_device,
    exchange_code, initialize_auth, revoke_device,
)
from gaps_scraper.api.data import dataset, notes_periods
from gaps_scraper.api.schemas import Absences, Dataset, Notes, NotesPeriod, PairRequest, Schedule, Session, PushSubscription, PushConfiguration
from gaps_scraper.notifications.store import initialize_push, configuration, subscribe, unsubscribe
from gaps_scraper.common.state import initialize_state


@dataclass(frozen=True)
class Settings:
    database: Path
    origin: str = "http://127.0.0.1:8000"

    def __post_init__(self):
        parsed = urlsplit(self.origin)
        local = parsed.hostname in ("localhost","127.0.0.1","::1")
        if (parsed.scheme not in ("http","https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path or (parsed.scheme != "https" and not local)):
            raise ValueError("APP_ORIGIN doit être une origine HTTPS sans chemin, sauf en boucle locale.")

    @property
    def secure(self):
        return self.origin.startswith("https://")

    @property
    def cookie_name(self):
        return "__Host-gaps_session" if self.secure else "gaps_local_session"


def create_app(settings, *, clock=time.time):
    initialize_auth(settings.database)
    initialize_state(settings.database)
    initialize_push(settings.database)
    app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=[urlsplit(settings.origin).hostname])

    @app.middleware("http")
    async def protections(request, call_next):
        response = None
        if request.url.path.startswith("/api/"):
            if request.headers.get("sec-fetch-site") == "cross-site":
                response = JSONResponse({"detail":"Origine refusée."},status_code=403)
            elif request.method not in ("GET","HEAD","OPTIONS") and request.headers.get("origin") != settings.origin:
                response = JSONResponse({"detail":"Origine refusée."},status_code=403)
            elif request.method not in ("GET","HEAD","OPTIONS"):
                if request.headers.get("content-type","").split(";")[0] != "application/json" and request.url.path == "/api/pair":
                    response = JSONResponse({"detail":"Corps JSON requis."},status_code=415)
                else:
                    # Lire un corps borné avant le validateur, y compris en transfert chunked.
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > (4096 if request.url.path=='/api/push/subscribe' else 2048):
                            response = JSONResponse({"detail":"Requête trop volumineuse."},status_code=413)
                            break
                    if response is None:
                        request._body = bytes(body)
        if response is None:
            response = await call_next(request)
        private = request.url.path.startswith("/api/") or request.url.path == "/healthz"
        response.headers["Cache-Control"] = "no-store" if private else "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'" if private else
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def bad_input(request, exc):
        # FastAPI ne doit pas réafficher le code d'association dans une erreur.
        return JSONResponse({"detail":"Requête invalide."},status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        return JSONResponse({"detail":"Données temporairement indisponibles."},status_code=503)

    @app.exception_handler(ResponseValidationError)
    async def response_error(request, exc):
        return JSONResponse({"detail":"Format de données non reconnu."},status_code=503)

    def device(request: Request):
        try:
            return authenticate_device(settings.database,request.cookies.get(settings.cookie_name),now=clock())
        except AuthError:
            raise HTTPException(401,"Appareil non associé ou session expirée.") from None

    @app.get("/healthz")
    def health():
        return {"status":"ok"}

    @app.post("/api/pair",response_model=Session)
    def pair(body: PairRequest,response: Response):
        try:
            token, session = exchange_code(settings.database,body.code,body.device_name,now=clock())
        except PairRateLimit:
            raise HTTPException(429,"Trop de tentatives. Réessayer plus tard.",headers={"Retry-After":"600"}) from None
        except AuthError:
            raise HTTPException(401,"Code invalide, expiré ou déjà utilisé.") from None
        response.set_cookie(settings.cookie_name,token,max_age=max(0,session['expires_at']-int(clock())),
                            secure=settings.secure,httponly=True,samesite="strict",path="/")
        return session

    @app.get("/api/session",response_model=Session)
    def session(request: Request,response: Response,current=Depends(device)):
        # Renouveler la conservation du cookie, sans repousser l'échéance serveur à cinq ans.
        response.set_cookie(settings.cookie_name,request.cookies[settings.cookie_name],
                            max_age=max(0,current['expires_at']-int(clock())),secure=settings.secure,
                            httponly=True,samesite='strict',path='/')
        return current

    @app.post("/api/logout",status_code=204)
    def logout(response: Response,current=Depends(device)):
        revoke_device(settings.database,current["id"])
        response.delete_cookie(settings.cookie_name,path="/",secure=settings.secure,httponly=True,samesite="strict")

    def read(component, model, current, *, selection=None):
        try:
            value = dataset(settings.database,current["account_id"],component,datetime.fromtimestamp(clock(),timezone.utc),selection=selection)
            return model.model_validate(value)
        except (ValidationError,ValueError,TypeError):
            raise HTTPException(503,"Format de données non reconnu.") from None

    @app.get("/api/notes",response_model=Dataset[Notes])
    def notes(year: int | None = Query(default=None,ge=2000,le=2100),
              semester: Literal['0','1'] | None = None,current=Depends(device)):
        if (year is None)!=(semester is None):raise HTTPException(422,'Préciser ensemble année et semestre.')
        selection=(str(year),semester) if year is not None else None
        return read("notes",Dataset[Notes],current,selection=selection)

    @app.get('/api/notes/periods',response_model=list[NotesPeriod])
    def available_notes_periods(current=Depends(device)):
        return notes_periods(settings.database,current['account_id'])

    @app.get("/api/absences",response_model=Dataset[Absences])
    def absences(current=Depends(device)):
        return read("absences",Dataset[Absences],current)

    @app.get("/api/horaire",response_model=Dataset[Schedule])
    def schedule(current=Depends(device)):
        return read("horaire",Dataset[Schedule],current)

    @app.get('/api/push/config',response_model=PushConfiguration)
    def push_configuration(current=Depends(device)):
        return configuration(settings.database,current['id'])

    @app.post('/api/push/subscribe',status_code=204)
    def push_subscribe(body: PushSubscription,current=Depends(device)):
        if not configuration(settings.database,current['id'])['enabled']:
            raise HTTPException(503,'Les notifications ne sont pas encore configurées.')
        try:
            subscribe(settings.database,current['id'],body.model_dump())
        except ValueError:
            raise HTTPException(422,'Abonnement de notification non pris en charge.') from None

    @app.post('/api/push/unsubscribe',status_code=204)
    def push_unsubscribe(current=Depends(device)):
        unsubscribe(settings.database,current['id'])

    web = Path(__file__).resolve().parents[1]/"web"

    @app.get("/",include_in_schema=False)
    def index():
        return FileResponse(web/"index.html")

    @app.get("/manifest.webmanifest",include_in_schema=False)
    def manifest():
        return FileResponse(web/"manifest.webmanifest",media_type="application/manifest+json")

    @app.get("/sw.js",include_in_schema=False)
    def service_worker():
        return FileResponse(web/"sw.js",media_type="application/javascript",
                            headers={"Service-Worker-Allowed":"/"})

    app.mount("/assets",StaticFiles(directory=web/"assets"),name="assets")

    return app
