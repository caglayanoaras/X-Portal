"""Portal users in Atlas.

A user attribute (the Technician of a test result) holds the id of a portal
user. Atlas keeps no copy of the users: an Atlas connection reads them from the
portal database the first time it needs them (names for titles, search, sort and
exports; checks on what is written), so a new user, or a name changed in the
portal, shows at once. In SQL, atlas_user(id) is a user's name and
atlas_username(id) their username (db.connect() provides both).

The portal doesn't delete a user that Atlas names (user_references() tells it);
deactivating the account keeps the name in Atlas.
"""
from __future__ import annotations

import os
import re

from app.atlas.db import connect, db_path, fold
from app.atlas.schema import q

_ID = re.compile(r"#?\s*(\d+)")                    # 12 or "#12"
_LABELLED = re.compile(r".*\(\s*([^()]+?)\s*\)")    # "Ayşe Yılmaz (ayse)", as exports write a user


def _norm(text) -> str:
    return " ".join(fold(text).split())


class Users:
    """The portal's users: id -> (username, name, active)."""

    def __init__(self, rows):
        self.by_id, self.by_username, self.by_folded, self.by_name = {}, {}, {}, {}
        for user_id, username, name, surname, active in rows:
            shown = " ".join(f"{name or ''} {surname or ''}".split()) or username
            self.by_id[user_id] = (username, shown, bool(active))
            self.by_username[username] = user_id
            self.by_folded.setdefault(_norm(username), []).append(user_id)
            self.by_name.setdefault(_norm(shown), []).append(user_id)

    @classmethod
    def load(cls) -> Users:
        from sqlmodel import Session, select     # here, so importing Atlas doesn't need the portal's database

        from app.core.database import engine
        from app.models import User
        with Session(engine) as session:
            return cls(session.exec(select(User.id, User.username, User.name, User.surname, User.is_active)).all())

    def name(self, user_id):
        """What Atlas shows for a user ("Ayşe Yılmaz"); "#12" for an id no user has."""
        if user_id is None:
            return None
        found = self.by_id.get(user_id)
        return found[1] if found else f"#{user_id}"

    def username(self, user_id):
        found = self.by_id.get(user_id)
        return found[0] if found else None

    def label(self, user_id) -> str:
        """A user as an export writes them: "Ayşe Yılmaz (ayse)", which an import reads back."""
        found = self.by_id.get(user_id)
        if not found:
            return f"#{user_id}"
        username, shown, _ = found
        return shown if shown == username else f"{shown} ({username})"

    def find(self, value):
        """The id of the user a value names (None for an empty value): an id (12 or "#12"),
        a username, "Ayşe Yılmaz (ayse)", or a name nobody else has. ValueError, with
        a message to follow the field's label, when no user or several fit."""
        if value is None:
            return None
        if isinstance(value, (bool, list, dict)):
            raise ValueError("must be a user: an id or a username")
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        text = " ".join(str(value).split())
        if not text:
            return None
        if text in self.by_username:
            return self.by_username[text]
        number = _ID.fullmatch(text)
        if number:
            user_id = int(number.group(1))
            if user_id in self.by_id:
                return user_id
            raise ValueError(f"there is no user #{user_id}")
        labelled = _LABELLED.fullmatch(text)
        if labelled:
            inner = labelled.group(1)
            ids = [self.by_username[inner]] if inner in self.by_username else self.by_folded.get(_norm(inner), [])
            if len(ids) == 1:
                return ids[0]
        for ids in (self.by_folded.get(_norm(text), []), self.by_name.get(_norm(text), [])):
            if len(ids) == 1:
                return ids[0]
            if ids:
                usernames = ", ".join(self.by_id[i][0] for i in ids[:5])
                raise ValueError(f"{len(ids)} users are named '{text}' ({usernames}); write the username")
        raise ValueError(f"there is no user '{text}'")

    def items(self) -> list:
        """Every user, by name: {"id", "username", "name", "active"}."""
        out = [{"id": i, "username": u, "name": n, "active": a} for i, (u, n, a) in self.by_id.items()]
        return sorted(out, key=lambda d: (_norm(d["name"]), d["username"]))


def user_references(user_id: int) -> list:
    """Where Atlas names a portal user: (class label, attribute label, how many objects)."""
    if not os.path.exists(db_path()):
        return []
    out = []
    with connect() as conn:
        rows = conn.execute("SELECT c.key, c.label, a.key, a.label FROM atlas_attribute a "
                            "JOIN atlas_class c ON c.id = a.class_id WHERE a.type = 'user' "
                            "ORDER BY c.label, a.position").fetchall()
        for class_key, class_label, key, label in rows:
            n = conn.execute(f'SELECT COUNT(*) FROM {q("obj_" + class_key)} WHERE {q(key)} = ?',
                             (user_id,)).fetchone()[0]
            if n:
                out.append((class_label, label, n))
    return out
