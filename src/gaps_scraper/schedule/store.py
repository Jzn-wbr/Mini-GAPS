"""Dernier horaire valide et occurrences déjà observées, dans SQLite."""

from gaps_scraper.common.storage import database
from datetime import datetime, timezone
from hashlib import sha256

from gaps_scraper.common.storage import canonical


def synchronize_schedule(path, document):
    scope = canonical([document["account_id"], document["academic_year"]["value"], document["source_period"]])
    payload = canonical(document)
    digest = sha256(payload.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with database(path) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS schedule_snapshots (
                scope TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
                checked_at TEXT NOT NULL, changed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS schedule_occurrences (
                scope TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                present INTEGER NOT NULL, PRIMARY KEY(scope,id));
        ''')
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT digest FROM schedule_snapshots WHERE scope=?",(scope,)).fetchone()
        baseline, changed = old is None, old is None or old[0] != digest
        if changed:
            db.execute("INSERT INTO schedule_snapshots VALUES (?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                       "payload=excluded.payload,digest=excluded.digest,checked_at=excluded.checked_at,"
                       "changed_at=excluded.changed_at", (scope,payload,digest,now,now))
            db.execute("UPDATE schedule_occurrences SET present=0 WHERE scope=?", (scope,))
            for item in document["occurrences"]:
                db.execute("INSERT INTO schedule_occurrences VALUES (?,?,?,1) ON CONFLICT(scope,id) DO UPDATE SET "
                           "payload=excluded.payload,present=1", (scope,item["id"],canonical(item)))
        else:
            db.execute("UPDATE schedule_snapshots SET checked_at=? WHERE scope=?",(now,scope))
    return {"baseline_created": baseline, "snapshot_changed": changed,
            "series": len(document["series"]), "occurrences": len(document["occurrences"]),
            "notifications_sent": 0}
