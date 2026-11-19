"""État de récupération et fraîcheur, indépendants des données scolaires."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from gaps_scraper.common.storage import database

COMPONENTS = ("notes", "absences", "horaire")
LOCAL = ZoneInfo("Europe/Zurich")


def initialize_state(path):
    with database(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS dataset_state (
            account_id TEXT NOT NULL, component TEXT NOT NULL, selected_scope TEXT,
            last_attempt TEXT NOT NULL, last_success TEXT, error_code TEXT,
            PRIMARY KEY(account_id,component))''')


def record_attempt(path, account_id, component, *, scope=None, error=None, now=None):
    if component not in COMPONENTS:
        raise ValueError("Catégorie inconnue")
    initialize_state(path)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    with database(path) as db:
        db.execute('''INSERT INTO dataset_state VALUES (?,?,?,?,?,?)
            ON CONFLICT(account_id,component) DO UPDATE SET
            selected_scope=COALESCE(excluded.selected_scope,dataset_state.selected_scope),
            last_attempt=excluded.last_attempt,
            last_success=COALESCE(excluded.last_success,dataset_state.last_success),
            error_code=excluded.error_code''',
            (account_id,component,scope,stamp,stamp if error is None else None,error))


def scheduled(now):
    local = now.astimezone(LOCAL)
    return 6 <= local.hour < 24 and local.minute % 10 == 0


def expected_success(now):
    # Trois minutes pour laisser se terminer la récupération du créneau courant.
    local = (now - timedelta(minutes=3)).astimezone(LOCAL)
    if local.hour < 6:
        local = (local-timedelta(days=1)).replace(hour=23,minute=50,second=0,microsecond=0)
    else:
        local = local.replace(minute=local.minute//10*10,second=0,microsecond=0)
    return local.astimezone(timezone.utc)


def freshness(checked_at, error, now):
    if checked_at is None:
        return "unavailable"
    if error or datetime.fromisoformat(checked_at) < expected_success(now):
        return "stale"
    return "fresh"
