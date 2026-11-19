"""Lectures autorisées des instantanés, sans appeler le portail scolaire."""

import json
from gaps_scraper.common.state import freshness
from gaps_scraper.common.storage import database, canonical

TABLES = {"notes":"snapshots", "absences":"absence_snapshots", "horaire":"schedule_snapshots"}


def dataset(path, account_id, component, now, *, selection=None):
    table = TABLES[component]  # Aucun nom de table fourni par une requête cliente.
    with database(path) as db:
        state = db.execute("SELECT * FROM dataset_state WHERE account_id=? AND component=?",
                           (account_id,component)).fetchone()
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
        row = None
        if exists:
            if selection is not None:
                row = db.execute(f"SELECT payload,checked_at FROM {table} WHERE scope=? AND json_extract(payload,'$.account_id')=?",
                                 (canonical([account_id,*selection]),account_id)).fetchone()
            elif state and state["selected_scope"]:
                row = db.execute(f"SELECT payload,checked_at FROM {table} WHERE scope=? AND json_extract(payload,'$.account_id')=?",
                                 (state["selected_scope"],account_id)).fetchone()
            else:
                # Migration des premiers essais : isoler le compte avant de choisir une période.
                order = "CAST(json_extract(payload,'$.academic_year.value') AS INTEGER) DESC"
                if component == "notes":
                    order += ", CAST(json_extract(payload,'$.semester.value') AS INTEGER) DESC"
                row = db.execute(f"SELECT payload,checked_at FROM {table} WHERE json_extract(payload,'$.account_id')=? ORDER BY {order},checked_at DESC LIMIT 1",
                                 (account_id,)).fetchone()
    checked = row["checked_at"] if row else None
    error = state["error_code"] if state and selection is None else None
    return {"data":json.loads(row["payload"]) if row else None,
            "freshness":freshness(checked,error,now), "last_sync":checked,
            "last_attempt":state["last_attempt"] if state and selection is None else checked, "error":error}


def notes_periods(path, account_id):
    with database(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'").fetchone():return []
        rows=db.execute("SELECT payload FROM snapshots WHERE json_extract(payload,'$.account_id')=? ORDER BY CAST(json_extract(payload,'$.academic_year.value') AS INTEGER) DESC, CAST(json_extract(payload,'$.semester.value') AS INTEGER) DESC",(account_id,)).fetchall()
    return [{'academic_year':doc['academic_year'],'semester':doc['semester']}
            for row in rows for doc in [json.loads(row['payload'])]]
