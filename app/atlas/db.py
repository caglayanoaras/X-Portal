"""Connections to atlas.db, the SQLite file Atlas keeps apart from the portal DB.

The file is created on start if it is missing, so deleting it (with the server
stopped) resets Atlas and leaves the rest of the portal untouched. Uploaded
images and files live in it too (atlas_file), so it is the one file to back up.
"""
import os
import sqlite3
from contextlib import contextmanager

from app.core.config import settings

SCHEMA_VERSION = 2

_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "iiissgguuoocc")


def fold(value) -> str:
    """Case- and Turkish-accent-insensitive form used by search and 'contains'."""
    return str(value).translate(_FOLD).lower()


def db_path() -> str:
    path = settings.ATLAS_DB_PATH
    return path if os.path.isabs(path) else os.path.abspath(path)


def _sql_fold(value):
    return None if value is None else fold(value)


def _sql_join(separator, *parts):
    return separator.join(str(p) for p in parts if p is not None and str(p) != "")


@contextmanager
def connect():
    """A connection in autocommit mode; writes go through transaction()."""
    conn = sqlite3.connect(db_path(), timeout=5, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.create_function("atlas_fold", 1, _sql_fold, deterministic=True)
        conn.create_function("atlas_join", -1, _sql_join, deterministic=True)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn):
    """BEGIN IMMEDIATE … COMMIT; any error rolls everything back, DDL included."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


_META_TABLES = """
CREATE TABLE IF NOT EXISTS atlas_class (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    path        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '',
    icon        TEXT NOT NULL DEFAULT '',
    roles       TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS atlas_attribute (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id  INTEGER NOT NULL REFERENCES atlas_class(id) ON DELETE CASCADE,
    key       TEXT NOT NULL,
    label     TEXT NOT NULL,
    type      TEXT NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0,
    required  INTEGER NOT NULL DEFAULT 0,
    is_unique INTEGER NOT NULL DEFAULT 0,
    is_title  INTEGER NOT NULL DEFAULT 0,
    show      INTEGER NOT NULL DEFAULT 1,
    options   TEXT NOT NULL DEFAULT '[]',
    lookup    TEXT NOT NULL DEFAULT '',
    UNIQUE (class_id, key)
);
CREATE TABLE IF NOT EXISTS atlas_relationship (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id        INTEGER NOT NULL REFERENCES atlas_class(id) ON DELETE CASCADE,
    key             TEXT NOT NULL,
    label           TEXT NOT NULL,
    target_class_id INTEGER NOT NULL REFERENCES atlas_class(id),
    cardinality     TEXT NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    required        INTEGER NOT NULL DEFAULT 0,
    show            INTEGER NOT NULL DEFAULT 1,
    reverse_label   TEXT NOT NULL DEFAULT '',
    UNIQUE (class_id, key)
);
CREATE TABLE IF NOT EXISTS atlas_file (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    width      INTEGER,
    height     INTEGER,
    sha256     TEXT NOT NULL,
    data       BLOB NOT NULL,
    preview    BLOB,
    thumb      BLOB,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);
"""


# Columns added after the first version; an older atlas.db gets them on start.
_ADDED_COLUMNS = {
    "atlas_class": {"color": "TEXT NOT NULL DEFAULT ''", "icon": "TEXT NOT NULL DEFAULT ''"},
}


def init_db() -> None:
    """Create atlas.db and its definition tables if they don't exist yet, and
    add any columns an older file is missing."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_META_TABLES)
        for table, columns in _ADDED_COLUMNS.items():
            have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
