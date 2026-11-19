"""État SQLite privé et changements persistants, sans envoi de notifications."""

from gaps_scraper.common.storage import database,canonical
import json
from datetime import datetime, timezone
from hashlib import sha256




def synchronize(path, document):
    scope = canonical([document["account_id"], document["academic_year"]["value"],
                       document["semester"]["value"]])
    now = datetime.now(timezone.utc).isoformat()
    # Transaction unique pour état, identités déjà vues et journal des changements.
    with database(path) as db:
        db.execute("PRAGMA busy_timeout=10000")
        db.executescript('''
            CREATE TABLE IF NOT EXISTS snapshots (
                scope TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
                checked_at TEXT NOT NULL, changed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assessments (
                scope TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                present INTEGER NOT NULL, PRIMARY KEY(scope, id));
            CREATE TABLE IF NOT EXISTS changes (
                sequence INTEGER PRIMARY KEY, scope TEXT NOT NULL, assessment_id TEXT NOT NULL,
                kind TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL);
        ''')
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT digest FROM snapshots WHERE scope=?", (scope,)).fetchone()
        baseline = previous is None
        current = {a["id"]: a for a in document["assessments"]}
        known = {row[0]: (json.loads(row[1]), bool(row[2])) for row in db.execute(
            "SELECT id,payload,present FROM assessments WHERE scope=?", (scope,))}
        changes = []
        for identity, item in current.items():
            old, present = known.get(identity, (None, False))
            kind = None
            if not baseline:
                if old is None:
                    kind = "assessment_discovered"
                elif old["grade"]["status"] == "unpublished" and item["grade"]["status"] == "published":
                    kind = "grade_published"
                elif old["grade"] != item["grade"]:
                    kind = "grade_changed"
                elif not present:
                    kind = "assessment_reappeared"
                elif old != item:
                    kind = "assessment_metadata_changed"
            if kind:
                changes.append((identity, kind, {"previous": old, "current": item}))
            if old != item or not present:
                db.execute("INSERT INTO assessments VALUES (?,?,?,1) ON CONFLICT(scope,id) "
                           "DO UPDATE SET payload=excluded.payload,present=1",
                           (scope, identity, canonical(item)))
        for identity, (old, present) in known.items():
            if identity not in current and present:
                changes.append((identity, "assessment_missing_unconfirmed", {"previous": old}))
                db.execute("UPDATE assessments SET present=0 WHERE scope=? AND id=?", (scope, identity))
        for identity, kind, payload in changes:
            db.execute("INSERT INTO changes(scope,assessment_id,kind,observed_at,payload) VALUES (?,?,?,?,?)",
                       (scope, identity, kind, now, canonical(payload)))
        payload = canonical(document)
        digest = sha256(payload.encode()).hexdigest()
        changed = baseline or digest != previous[0]
        if changed:
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                       "payload=excluded.payload,digest=excluded.digest,checked_at=excluded.checked_at,"
                       "changed_at=excluded.changed_at", (scope, payload, digest, now, now))
        else:
            db.execute("UPDATE snapshots SET checked_at=? WHERE scope=?", (now, scope))
    return {"baseline_created": baseline, "snapshot_changed": changed,
            "assessments": len(current), "changes_recorded": len(changes),
            "notifications_sent": 0}
