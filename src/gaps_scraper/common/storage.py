"""Connexions SQLite partagées par les lecteurs et les écrivains."""

from contextlib import contextmanager, closing
from pathlib import Path
import os
import json
import sqlite3


def data_directory():
    return Path(os.environ.get("GAPS_DATA_DIR", "data/private"))


def database_path():
    return data_directory() / "notes.sqlite3"


def prepare_directory(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


@contextmanager
def database(path):
    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        yield db


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
