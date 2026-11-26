"""Connections to atlas.db, the SQLite file Atlas keeps apart from the portal DB.

The file is created on start if it is missing, so deleting it (with the server
stopped) resets Atlas and leaves the rest of the portal untouched. Uploaded
images and files live in it too (atlas_file), so it is the one file to back up.
"""
import os
import sqlite3
from contextlib import contextmanager

from app.core.config import settings

SCHEMA_VERSION = 4

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


class Connection(sqlite3.Connection):
    """An Atlas connection. .users: the portal's users (users.py), read the first
    time something needs them."""
    _users = None

    @property
    def users(self):
        if self._users is None:
            from app.atlas.users import Users          # users.py reads the portal database
            self._users = Users.load()
        return self._users


@contextmanager
def connect():
    """A connection in autocommit mode; writes go through transaction()."""
    conn = sqlite3.connect(db_path(), timeout=5, isolation_level=None, factory=Connection)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.create_function("atlas_fold", 1, _sql_fold, deterministic=True)
        conn.create_function("atlas_join", -1, _sql_join, deterministic=True)
        # A user attribute holds a portal user's id: these give their name and username.
        conn.create_function("atlas_user", 1, lambda user_id: None if user_id is None else conn.users.name(user_id),
                             deterministic=True)
        conn.create_function("atlas_username", 1,
                             lambda user_id: None if user_id is None else conn.users.username(user_id),
                             deterministic=True)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn):
    """BEGIN IMMEDIATE … COMMIT; any error rolls everything back, DDL included.

    Inside a transaction that is already open (an import runs every row in one)
    it is a savepoint instead: an error undoes only this part, and the outer
    transaction decides what is kept."""
    if conn.in_transaction:
        conn.execute("SAVEPOINT atlas_part")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK TO atlas_part")
            conn.execute("RELEASE atlas_part")
            raise
        conn.execute("RELEASE atlas_part")
        return
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
    columns   TEXT NOT NULL DEFAULT '[]',
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
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    data       BLOB NOT NULL
);
-- An image's thumbnail and preview, apart from the image. SQLite reads through the
-- earlier columns of a row to reach a later one, so the file's bytes come last in
-- atlas_file, and a thumbnail stored next to them would mean reading the whole image.
CREATE TABLE IF NOT EXISTS atlas_file_view (
    file_id INTEGER PRIMARY KEY REFERENCES atlas_file(id) ON DELETE CASCADE,
    thumb   BLOB,
    preview BLOB
);
"""


# Columns added after the first version; an older atlas.db gets them on start.
_ADDED_COLUMNS = {
    "atlas_class": {"color": "TEXT NOT NULL DEFAULT ''", "icon": "TEXT NOT NULL DEFAULT ''"},
    "atlas_attribute": {"columns": "TEXT NOT NULL DEFAULT '[]'"},     # a matrix's columns
}


def init_db() -> None:
    """Create atlas.db and its definition tables if they don't exist yet, and
    bring an older file up to date (it only gains columns, tables and copies)."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.executescript(_META_TABLES)
        for table, columns in _ADDED_COLUMNS.items():
            have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        have = {row["name"] for row in conn.execute("PRAGMA table_info(atlas_file)")}
        if version < 4 and {"thumb", "preview"} <= have:
            # Before version 4 thumbnails and previews sat after the image in atlas_file.
            # They are copied once; the old columns keep theirs, and nothing reads them now.
            with transaction(conn):
                conn.execute("INSERT OR IGNORE INTO atlas_file_view (file_id, thumb, preview) "
                             "SELECT id, thumb, preview FROM atlas_file WHERE thumb IS NOT NULL OR preview IS NOT NULL")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
