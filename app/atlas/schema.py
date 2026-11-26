"""Atlas class definitions and the tables behind them.

A class is a row in atlas_class plus its attributes (atlas_attribute) and
relationships (atlas_relationship). Each class also has real tables:

    obj_<class>           one row per object: the system columns (_id, _roles,
                          _created_at, _created_by, _updated_at, _updated_by,
                          _version) and one column per stored attribute
    link_<class>__<rel>   one per relationship, rows of (source_id, target_id)

Object columns are declared without a type, so SQLite keeps each value exactly
as values.coerce() produced it. Changing an attribute's type converts the
values in place; no table is ever rebuilt.

Keys (of classes, attributes and relationships) are ASCII, used by the API and
in table and column names, and fixed once created. Labels can change any time.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.atlas.db import fold, transaction
from app.atlas.errors import AtlasError
from app.atlas.values import coerce, role_ids

ATTRIBUTE_TYPES = ("string", "text", "int", "float", "boolean", "date", "datetime",
                   "choice", "user", "list", "matrix", "image", "file", "lookthrough")
# How the class builder names each type, for messages people read.
TYPE_NAMES = {"string": "text", "text": "long text", "int": "integer", "float": "float",
              "boolean": "yes / no", "date": "date", "datetime": "date and time", "choice": "choice",
              "user": "user", "list": "list", "matrix": "matrix", "image": "image", "file": "file",
              "lookthrough": "look-through"}
# A user attribute holds a portal user's id; Atlas reads the users from the portal (users.py).
# Types whose column holds the id of an uploaded file (atlas_file); see files.py.
FILE_TYPES = frozenset({"image", "file"})
# Types whose column holds many numbers as JSON: a list ([1, 2]) or a matrix, one row
# per entry and a value per column ([[0, 0], [0.1, 210]]); see values.py.
SERIES_TYPES = frozenset({"list", "matrix"})
MATRIX_COLUMNS_MAX = 20
CARDINALITIES = ("one", "many")
COLOR_PATTERN = re.compile(r"#[0-9a-f]{6}")                  # "#21358b"
ICON_PATTERN = re.compile(r"fa-[a-z0-9]+(?:-[a-z0-9]+)*")    # a Font Awesome name: "fa-flask"
KEY_PATTERN = re.compile(r"[a-z](?:_?[a-z0-9])*")   # no leading, trailing or double "_"
KEY_MAX = 40
# Query-string words of the objects API, so no field may use them as its key.
RESERVED_KEYS = frozenset({"q", "sort", "page", "page_size", "expand", "fields", "incoming_counts", "brief"})

# System fields every object has, with the type used to filter and sort them.
# Timestamps are UTC ISO 8601 ("2026-11-06T09:12:00Z").
SYSTEM_FIELDS = {
    "_id": "int",
    "_created_at": "timestamp",
    "_created_by": "string",
    "_updated_at": "timestamp",
    "_updated_by": "string",
    "_version": "int",
}

_SYSTEM_COLUMNS = (
    '"_id" INTEGER PRIMARY KEY AUTOINCREMENT',
    "\"_roles\" TEXT NOT NULL DEFAULT '[]'",
    '"_created_at" TEXT NOT NULL',
    '"_created_by" TEXT NOT NULL',
    '"_updated_at" TEXT NOT NULL',
    '"_updated_by" TEXT NOT NULL',
    '"_version" INTEGER NOT NULL DEFAULT 1',
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def q(name: str) -> str:
    """Quote an SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


# --------------------------------------------------------------------------- #
# Definitions
# --------------------------------------------------------------------------- #
@dataclass(eq=False)
class Attribute:
    key: str
    label: str
    type: str
    required: bool = False
    unique: bool = False
    title: bool = False          # part of the object's title (pickers, link columns)
    show: bool = True            # a column in the grid by default
    options: list = field(default_factory=list)   # choice
    lookup: str = ""                              # lookthrough: "relationship.attribute"
    columns: list = field(default_factory=list)   # matrix: [{"key": "strain", "label": "Strain (%)"}, …]
    cls: AtlasClass | None = field(default=None, repr=False)

    @property
    def stored(self) -> bool:
        return self.type != "lookthrough"

    @property
    def width(self) -> int:
        """How many values a matrix row holds."""
        return len(self.columns)


@dataclass(eq=False)
class Relationship:
    key: str
    label: str
    target_key: str
    cardinality: str = "many"    # how many targets one object links to
    required: bool = False
    show: bool = True
    reverse_label: str = ""      # how the link reads from the target's side
    cls: AtlasClass | None = field(default=None, repr=False)
    target: AtlasClass | None = field(default=None, repr=False)

    @property
    def link_table(self) -> str:
        return f"link_{self.cls.key}__{self.key}"


@dataclass(eq=False)
class AtlasClass:
    key: str
    label: str
    path: str = ""
    description: str = ""
    color: str = ""              # "#rrggbb"; "" shows the default grey
    icon: str = ""               # Font Awesome name, "fa-flask"; "" shows the default icon
    roles: list = field(default_factory=list)
    attributes: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    id: int | None = None
    created_at: str = ""
    created_by: str = ""
    updated_at: str = ""
    updated_by: str = ""

    @property
    def table(self) -> str:
        return f"obj_{self.key}"

    @property
    def stored_attributes(self) -> list:
        return [a for a in self.attributes if a.stored]

    def attr(self, key: str) -> Attribute | None:
        return next((a for a in self.attributes if a.key == key), None)

    def rel(self, key: str) -> Relationship | None:
        return next((r for r in self.relationships if r.key == key), None)


class Schema:
    """All classes, cross-linked: relationship targets and back-references."""

    def __init__(self, classes):
        self.classes = {c.key: c for c in classes}
        for cls in self.classes.values():
            for attr in cls.attributes:
                attr.cls = cls
            for rel in cls.relationships:
                rel.cls = cls
                rel.target = self.classes.get(rel.target_key)

    def get(self, key: str) -> AtlasClass:
        cls = self.classes.get(key)
        if cls is None:
            raise AtlasError(404, f"There is no class '{key}'.")
        return cls

    def incoming(self, cls: AtlasClass) -> list:
        """Relationships (of any class, this one included) that point at cls."""
        return [r for c in self.classes.values() for r in c.relationships if r.target is cls]

    def resolve(self, attr: Attribute, _seen=None) -> tuple[list, Attribute]:
        """A look-through as (the relationships it follows, the stored attribute it ends on)."""
        if attr.stored:
            return [], attr
        seen = _seen if _seen is not None else set()
        where = f"{attr.cls.label} › {attr.label}"
        if id(attr) in seen:
            raise AtlasError(400, f"The look-through '{where}' loops back on itself.")
        seen.add(id(attr))
        rel_key, _, attr_key = attr.lookup.partition(".")
        rel = attr.cls.rel(rel_key)
        if rel is None:
            raise AtlasError(400, f"The look-through '{where}' uses '{rel_key}', "
                                  f"which is not a relationship of {attr.cls.label}.")
        target = rel.target.attr(attr_key) if rel.target else None
        if target is None:
            target_label = rel.target.label if rel.target else rel.target_key
            raise AtlasError(400, f"The look-through '{where}' uses '{attr.lookup}', "
                                  f"but {target_label} has no attribute '{attr_key}'.")
        rels, final = self.resolve(target, seen)
        return [rel, *rels], final


def load_schema(conn) -> Schema:
    classes = {}
    for row in conn.execute("SELECT * FROM atlas_class ORDER BY path, label"):
        classes[row["id"]] = AtlasClass(
            key=row["key"], label=row["label"], path=row["path"], description=row["description"],
            color=row["color"], icon=row["icon"], roles=json.loads(row["roles"]), id=row["id"],
            created_at=row["created_at"], created_by=row["created_by"],
            updated_at=row["updated_at"], updated_by=row["updated_by"])
    for row in conn.execute("SELECT * FROM atlas_attribute ORDER BY class_id, position, id"):
        classes[row["class_id"]].attributes.append(Attribute(
            key=row["key"], label=row["label"], type=row["type"], required=bool(row["required"]),
            unique=bool(row["is_unique"]), title=bool(row["is_title"]), show=bool(row["show"]),
            options=json.loads(row["options"]), lookup=row["lookup"], columns=json.loads(row["columns"])))
    keys = {class_id: cls.key for class_id, cls in classes.items()}
    for row in conn.execute("SELECT * FROM atlas_relationship ORDER BY class_id, position, id"):
        classes[row["class_id"]].relationships.append(Relationship(
            key=row["key"], label=row["label"], target_key=keys.get(row["target_class_id"], ""),
            cardinality=row["cardinality"], required=bool(row["required"]), show=bool(row["show"]),
            reverse_label=row["reverse_label"]))
    return Schema(classes.values())


# --------------------------------------------------------------------------- #
# Parsing a definition sent to the API
# --------------------------------------------------------------------------- #
_CLASS_FIELDS = {"key", "label", "path", "description", "color", "icon", "roles", "attributes",
                 "relationships"}
_ATTRIBUTE_FIELDS = {"key", "label", "type", "required", "unique", "title", "show", "options", "lookup",
                     "columns"}
_RELATIONSHIP_FIELDS = {"key", "label", "target", "cardinality", "required", "show", "reverse_label"}
# Read-only fields a GET returns, so PUTting that same JSON back just works.
_READ_ONLY_FIELDS = {"created_at", "created_by", "updated_at", "updated_by", "permissions",
                     "incoming", "value_type", "many", "target_label", "target_color", "target_icon"}


def check_key(value, what: str) -> str:
    if not isinstance(value, str) or len(value) > KEY_MAX or not KEY_PATTERN.fullmatch(value):
        raise AtlasError(400, f"{what} must use lower-case letters, digits and single underscores, "
                              f"start with a letter and be at most {KEY_MAX} characters (e.g. batch_no).")
    return value


def slugify(text: str) -> str:
    """A key from a label, as the class builder makes them: "Gerilme (MPa)" -> "gerilme_mpa"."""
    text = unicodedata.normalize("NFKD", fold(text))
    text = re.sub(r"[^a-z0-9]+", "_", "".join(c for c in text if not unicodedata.combining(c))).strip("_")
    if text[:1].isdigit():
        text = "n_" + text
    return re.sub(r"_+", "_", text)[:KEY_MAX].rstrip("_")


def _unknown(data: dict, allowed: set, what: str) -> None:
    extra = sorted(set(data) - allowed - _READ_ONLY_FIELDS)
    if extra:
        raise AtlasError(400, f"Unknown field(s) in {what}: {', '.join(extra)}.")


def _text(value, what: str, *, required: bool = False, limit: int = 200) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise AtlasError(400, f"{what} must be text.")
    value = value.strip()
    if required and not value:
        raise AtlasError(400, f"{what} is required.")
    if len(value) > limit:
        raise AtlasError(400, f"{what} is longer than {limit} characters.")
    return value


def _flag(value, what: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool) or (isinstance(value, int) and value in (0, 1)):
        return bool(value)
    raise AtlasError(400, f"{what} must be true or false.")


def _items(value, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AtlasError(400, f"{what} must be a list.")
    return value


def _path(value) -> str:
    text = _text(value, "The folder")
    return "/".join(part.strip() for part in text.replace("\\", "/").split("/") if part.strip())


def _color(value) -> str:
    text = _text(value, "The color").lower()
    if text and not COLOR_PATTERN.fullmatch(text):
        raise AtlasError(400, "The color must be a hex color such as #21358b.")
    return text


def _icon(value) -> str:
    text = _text(value, "The icon")
    if text and (len(text) > 40 or not ICON_PATTERN.fullmatch(text)):
        raise AtlasError(400, "The icon must be a Font Awesome name such as fa-flask.")
    return text


def _parse_attribute(item, n: int) -> Attribute:
    if not isinstance(item, dict):
        raise AtlasError(400, f"Attribute {n} must be an object.")
    _unknown(item, _ATTRIBUTE_FIELDS, f"attribute {n}")
    label = _text(item.get("label"), f"The label of attribute {n}", required=True, limit=100)
    key = check_key(item.get("key"), f"The key of '{label}'")
    atype = item.get("type") or "string"
    if atype not in ATTRIBUTE_TYPES:
        raise AtlasError(400, f"'{label}' has an unknown type '{atype}'. Types: {', '.join(ATTRIBUTE_TYPES)}.")
    attr = Attribute(
        key=key, label=label, type=atype,
        required=_flag(item.get("required"), f"'{label}' required", False),
        unique=_flag(item.get("unique"), f"'{label}' unique", False),
        title=_flag(item.get("title"), f"'{label}' title", False),
        show=_flag(item.get("show"), f"'{label}' show", True))
    if atype == "choice":
        options = item.get("options")
        if not isinstance(options, list):
            raise AtlasError(400, f"The choice '{label}' needs a list of options.")
        for option in options:
            if option is None or isinstance(option, (dict, list)):
                raise AtlasError(400, f"The options of '{label}' must be text.")
            text = str(option).strip()
            if text and text not in attr.options:
                attr.options.append(text)
        if not attr.options:
            raise AtlasError(400, f"The choice '{label}' needs at least one option.")
    if atype == "lookthrough":
        lookup = item.get("lookup")
        if not isinstance(lookup, str) or not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", lookup.strip()):
            raise AtlasError(400, f"The look-through '{label}' needs a lookup like 'relationship.attribute'.")
        attr.lookup = lookup.strip()
        if attr.required or attr.unique:
            raise AtlasError(400, f"The look-through '{label}' can't be required or unique.")
    if atype == "matrix":
        attr.columns = _matrix_columns(item.get("columns"), label)
    if (atype in FILE_TYPES or atype in SERIES_TYPES) and (attr.unique or attr.title):
        raise AtlasError(400, f"The {TYPE_NAMES[atype]} '{label}' can't be unique or part of the title.")
    return attr


def _matrix_columns(value, label: str) -> list:
    """A matrix's columns: [{"key", "label"}, …], or just labels (keys are made from them)."""
    if not isinstance(value, list) or not value:
        raise AtlasError(400, f"The matrix '{label}' needs at least one column.")
    if len(value) > MATRIX_COLUMNS_MAX:
        raise AtlasError(400, f"The matrix '{label}' can have at most {MATRIX_COLUMNS_MAX} columns.")
    columns, keys = [], set()
    for n, item in enumerate(value, start=1):
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, dict):
            raise AtlasError(400, f"Column {n} of '{label}' must be a label or {{\"key\", \"label\"}}.")
        extra = sorted(set(item) - {"key", "label"})
        if extra:
            raise AtlasError(400, f"Unknown field(s) in column {n} of '{label}': {', '.join(extra)}.")
        name = _text(item.get("label"), f"The label of column {n} of '{label}'", required=True, limit=100)
        key = check_key(item.get("key") or slugify(name), f"The key of column '{name}' of '{label}'")
        if key in keys:
            raise AtlasError(400, f"The matrix '{label}' has two columns with the key '{key}'.")
        keys.add(key)
        columns.append({"key": key, "label": name})
    return columns


def _parse_relationship(item, n: int) -> Relationship:
    if not isinstance(item, dict):
        raise AtlasError(400, f"Relationship {n} must be an object.")
    _unknown(item, _RELATIONSHIP_FIELDS, f"relationship {n}")
    label = _text(item.get("label"), f"The label of relationship {n}", required=True, limit=100)
    key = check_key(item.get("key"), f"The key of '{label}'")
    target = item.get("target")
    if not isinstance(target, str) or not target:
        raise AtlasError(400, f"The relationship '{label}' needs a target class.")
    cardinality = item.get("cardinality") or "many"
    if cardinality not in CARDINALITIES:
        raise AtlasError(400, f"The cardinality of '{label}' must be 'one' or 'many'.")
    return Relationship(
        key=key, label=label, target_key=target, cardinality=cardinality,
        required=_flag(item.get("required"), f"'{label}' required", False),
        show=_flag(item.get("show"), f"'{label}' show", True),
        reverse_label=_text(item.get("reverse_label"), f"The reverse label of '{label}'", limit=100))


def parse_definition(payload, *, key: str | None = None) -> AtlasClass:
    """Validate a class definition: a POST body, or a PUT body with the key from the URL."""
    if not isinstance(payload, dict):
        raise AtlasError(400, "Send the class definition as a JSON object.")
    _unknown(payload, _CLASS_FIELDS, "the class")
    if key is None:
        key = check_key(payload.get("key"), "The class key")
    elif payload.get("key") not in (None, key):
        raise AtlasError(400, "A class key can't be changed.")
    try:
        roles = role_ids(payload.get("roles"))
    except ValueError as exc:
        raise AtlasError(400, f"roles {exc}.") from None
    cls = AtlasClass(
        key=key,
        label=_text(payload.get("label"), "The class label", required=True, limit=100),
        path=_path(payload.get("path")),
        description=_text(payload.get("description"), "The description", limit=2000),
        color=_color(payload.get("color")),
        icon=_icon(payload.get("icon")),
        roles=roles)
    used = set()

    def claim(member_key: str) -> None:
        if member_key in RESERVED_KEYS:
            raise AtlasError(400, f"'{member_key}' is reserved by the API; choose another key.")
        if member_key in used:
            raise AtlasError(400, f"The key '{member_key}' is used twice; keys must be unique within a class.")
        used.add(member_key)

    for n, item in enumerate(_items(payload.get("attributes"), "attributes"), start=1):
        attr = _parse_attribute(item, n)
        claim(attr.key)
        attr.cls = cls
        cls.attributes.append(attr)
    for n, item in enumerate(_items(payload.get("relationships"), "relationships"), start=1):
        rel = _parse_relationship(item, n)
        claim(rel.key)
        rel.cls = cls
        cls.relationships.append(rel)
    return cls


def _validate(schema: Schema) -> None:
    for cls in schema.classes.values():
        for rel in cls.relationships:
            if rel.target is None:
                raise AtlasError(400, f"'{cls.label} › {rel.label}' points at '{rel.target_key}', "
                                      f"which is not a class.")
        for attr in cls.attributes:
            final = None if attr.stored else schema.resolve(attr)[1]
            if final is not None and (final.type in FILE_TYPES or final.type in SERIES_TYPES):
                raise AtlasError(400, f"The look-through '{cls.label} › {attr.label}' can't show "
                                      f"{'an' if final.type == 'image' else 'a'} {TYPE_NAMES[final.type]}; "
                                      f"open the linked object to see it.")


# --------------------------------------------------------------------------- #
# Saving definitions (and the DDL that goes with them)
# --------------------------------------------------------------------------- #
def _unique_index(cls: AtlasClass, key: str) -> str:
    return f"ux_{cls.table}__{key}"


def _sync_unique(conn, cls: AtlasClass, attr: Attribute) -> None:
    name = q(_unique_index(cls, attr.key))
    if attr.unique:
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {q(cls.table)} ({q(attr.key)})")
    else:
        conn.execute(f"DROP INDEX IF EXISTS {name}")


def _sync_cardinality(conn, rel: Relationship) -> None:
    name = q(f"ux_{rel.link_table}__source")
    if rel.cardinality == "one":
        conn.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {q(rel.link_table)} ("source_id")')
    else:
        conn.execute(f"DROP INDEX IF EXISTS {name}")


def _create_link(conn, rel: Relationship) -> None:
    conn.execute(
        f'CREATE TABLE {q(rel.link_table)} ('
        f'"source_id" INTEGER NOT NULL REFERENCES {q(rel.cls.table)}("_id") ON DELETE CASCADE, '
        f'"target_id" INTEGER NOT NULL REFERENCES {q(rel.target.table)}("_id") ON DELETE CASCADE, '
        f'PRIMARY KEY ("source_id", "target_id"))')
    conn.execute(f'CREATE INDEX {q(f"ix_{rel.link_table}__target")} ON {q(rel.link_table)} ("target_id")')
    _sync_cardinality(conn, rel)


def _insert_members(conn, cls: AtlasClass) -> None:
    for position, a in enumerate(cls.attributes):
        conn.execute(
            "INSERT INTO atlas_attribute (class_id, key, label, type, position, required, is_unique,"
            " is_title, show, options, lookup, columns) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cls.id, a.key, a.label, a.type, position, int(a.required), int(a.unique), int(a.title),
             int(a.show), json.dumps(a.options, ensure_ascii=False), a.lookup,
             json.dumps(a.columns, ensure_ascii=False)))
    for position, r in enumerate(cls.relationships):
        conn.execute(
            "INSERT INTO atlas_relationship (class_id, key, label, target_class_id, cardinality,"
            " position, required, show, reverse_label) VALUES (?,?,?,?,?,?,?,?,?)",
            (cls.id, r.key, r.label, r.target.id, r.cardinality, position, int(r.required),
             int(r.show), r.reverse_label))


def _count(conn, sql: str, args=()) -> int:
    return conn.execute(sql, args).fetchone()[0]


def match_folders(path: str, schema: Schema, skip: AtlasClass | None = None) -> str:
    """A folder path spelled as the folders that already exist: with "Depo/Hammadde"
    in use, "depo/HAMMADDE/Kimyasal" becomes "Depo/Hammadde/Kimyasal". Folder names
    match without regard to case or Turkish letters, so a typo can't split a folder."""
    if not path:
        return path
    known = {}                   # (folded parents, folded name) -> the spelling in use
    for other in schema.classes.values():
        if other is skip or not other.path:
            continue
        parts = other.path.split("/")
        for i, part in enumerate(parts):
            known.setdefault((tuple(fold(p) for p in parts[:i]), fold(part)), part)
    spelled = []
    for part in path.split("/"):
        spelled.append(known.get((tuple(fold(p) for p in spelled), fold(part)), part))
    return "/".join(spelled)


def create_class(conn, schema: Schema, cls: AtlasClass, user: str) -> None:
    if cls.key in schema.classes:
        raise AtlasError(409, f"A class with the key '{cls.key}' already exists.")
    cls.path = match_folders(cls.path, schema)
    _validate(Schema([*schema.classes.values(), cls]))
    now = utc_now()
    with transaction(conn):
        cls.id = conn.execute(
            "INSERT INTO atlas_class (key, label, path, description, color, icon, roles, created_at,"
            " created_by, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cls.key, cls.label, cls.path, cls.description, cls.color, cls.icon, json.dumps(cls.roles),
             now, user, now, user)).lastrowid
        _insert_members(conn, cls)
        columns = [*_SYSTEM_COLUMNS, *(q(a.key) for a in cls.stored_attributes)]
        conn.execute(f"CREATE TABLE {q(cls.table)} ({', '.join(columns)})")
        for attr in cls.stored_attributes:
            _sync_unique(conn, cls, attr)
        for rel in cls.relationships:
            _create_link(conn, rel)


def update_class(conn, schema: Schema, cls: AtlasClass, user: str, *, force: bool = False) -> None:
    """Replace a class definition, converting data where needed.

    Changes that delete data (removing an attribute or relationship that holds
    values, or a type change some values don't survive) are refused with
    requires_force=True and a list of warnings unless force is set.
    """
    old = schema.get(cls.key)
    cls.id = old.id
    cls.path = match_folders(cls.path, schema, skip=old)
    new_schema = Schema([c for c in schema.classes.values() if c is not old] + [cls])
    _validate(new_schema)

    old_attrs = {a.key: a for a in old.attributes}
    new_attrs = {a.key: a for a in cls.attributes}
    old_rels = {r.key: r for r in old.relationships}
    new_rels = {r.key: r for r in cls.relationships}
    for key in old_rels.keys() & new_rels.keys():
        if old_rels[key].target_key != new_rels[key].target_key:
            raise AtlasError(400, f"The target of '{new_rels[key].label}' can't change. "
                                  f"Remove the relationship and add a new one.")

    table = q(old.table)
    dropped = [k for k, a in old_attrs.items() if a.stored and (k not in new_attrs or not new_attrs[k].stored)]
    added = [k for k, a in new_attrs.items() if a.stored and (k not in old_attrs or not old_attrs[k].stored)]
    warnings, conversions, remapped = [], {}, set()
    for key in dropped:
        n = _count(conn, f"SELECT COUNT(*) FROM {table} WHERE {q(key)} IS NOT NULL")
        if n:
            verb = "Removing" if key not in new_attrs else "Turning into a look-through"
            warnings.append(f"{verb} '{old_attrs[key].label}' deletes its value in {n} object(s).")
    for key, new in new_attrs.items():
        before = old_attrs.get(key)
        if not (new.stored and before is not None and before.stored):
            continue
        if new.type == before.type == "matrix":      # columns added, removed or moved
            changes, warning = _remap_columns(conn, table, before, new)
            if changes:
                conversions[key] = changes
                remapped.add(key)
            if warning:
                warnings.append(warning)
            continue
        if new.type == before.type and (new.type != "choice" or set(new.options) >= set(before.options)):
            continue
        if new.type in FILE_TYPES or before.type in FILE_TYPES:     # uploads don't convert: they go
            ids = [row[0] for row in conn.execute(f'SELECT "_id" FROM {table} WHERE {q(key)} IS NOT NULL')]
            conversions[key] = [(None, object_id) for object_id in ids]
            if ids:
                warnings.append(f"Changing '{new.label}' to {TYPE_NAMES.get(new.type, new.type)} "
                                f"deletes its value in {len(ids)} object(s).")
            continue
        changes, lost = [], 0
        for object_id, value in conn.execute(f'SELECT "_id", {q(key)} FROM {table} WHERE {q(key)} IS NOT NULL'):
            source = conn.users.name(value) if before.type == "user" else value     # a user turns into their name
            try:
                converted = (conn.users.find(source) if new.type == "user"   # a name or username finds the user
                             else coerce(new.type, source, new.options, columns=new.width))
            except ValueError:
                converted, lost = None, lost + 1
            if converted != value or type(converted) is not type(value):
                changes.append((converted, object_id))
        conversions[key] = changes
        if lost:
            warnings.append(f"Changing '{new.label}' to {TYPE_NAMES.get(new.type, new.type)} "
                            f"clears {lost} value(s) that don't fit.")
    for key, rel in old_rels.items():
        if key not in new_rels:
            n = _count(conn, f"SELECT COUNT(*) FROM {q(rel.link_table)}")
            if n:
                warnings.append(f"Removing '{rel.label}' deletes {n} link(s).")
    if warnings and not force:
        raise AtlasError(409, "This change deletes data. Send it again with force=true to apply it.",
                         warnings=warnings, requires_force=True)

    for key, new in new_attrs.items():           # unique needs distinct values (after conversion)
        before = old_attrs.get(key)
        if not (new.stored and new.unique and before is not None and before.stored):
            continue
        values = dict(conn.execute(f'SELECT "_id", {q(key)} FROM {table} WHERE {q(key)} IS NOT NULL').fetchall())
        values.update({object_id: value for value, object_id in conversions.get(key, [])})
        seen = set()
        for value in values.values():
            if value is None:
                continue
            if value in seen:
                raise AtlasError(409, f"'{new.label}' can't be unique: the value '{value}' appears more than once.")
            seen.add(value)
    for key, new in new_rels.items():
        before = old_rels.get(key)
        if before is not None and before.cardinality == "many" and new.cardinality == "one":
            row = conn.execute(f'SELECT "source_id", COUNT(*) FROM {q(before.link_table)} '
                               f'GROUP BY "source_id" HAVING COUNT(*) > 1 LIMIT 1').fetchone()
            if row:
                raise AtlasError(409, f"'{new.label}' can't become 'one': {old.label} #{row[0]} "
                                      f"links to {row[1]} objects.")

    try:
        with transaction(conn):
            conn.execute(
                "UPDATE atlas_class SET label = ?, path = ?, description = ?, color = ?, icon = ?, roles = ?,"
                " updated_at = ?, updated_by = ? WHERE id = ?",
                (cls.label, cls.path, cls.description, cls.color, cls.icon, json.dumps(cls.roles),
                 utc_now(), user, old.id))
            conn.execute("DELETE FROM atlas_attribute WHERE class_id = ?", (old.id,))
            conn.execute("DELETE FROM atlas_relationship WHERE class_id = ?", (old.id,))
            _insert_members(conn, cls)
            for key in dropped:
                conn.execute(f"DROP INDEX IF EXISTS {q(_unique_index(old, key))}")
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {q(key)}")
            for key in added:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {q(key)}")
            stamp = utc_now()
            for key, changes in conversions.items():
                conn.execute(f"DROP INDEX IF EXISTS {q(_unique_index(old, key))}")
                if key in remapped:     # a new version, so a form still showing the old columns can't save its rows
                    conn.executemany(f'UPDATE {table} SET {q(key)} = ?, "_version" = "_version" + 1, '
                                     f'"_updated_at" = ?, "_updated_by" = ? WHERE "_id" = ?',
                                     [(value, stamp, user, object_id) for value, object_id in changes])
                else:
                    conn.executemany(f'UPDATE {table} SET {q(key)} = ? WHERE "_id" = ?', changes)
            for attr in cls.stored_attributes:
                _sync_unique(conn, cls, attr)
            for key, rel in old_rels.items():
                if key not in new_rels:
                    conn.execute(f"DROP TABLE IF EXISTS {q(rel.link_table)}")
            for key, rel in new_rels.items():
                if key in old_rels:
                    _sync_cardinality(conn, rel)
                else:
                    _create_link(conn, rel)
            sweep_files(conn, new_schema)
    except sqlite3.IntegrityError as exc:
        raise AtlasError(409, f"The change conflicts with existing data ({exc}).") from None


def _remap_columns(conn, table: str, before: Attribute, new: Attribute):
    """A matrix whose columns were added, removed or moved: every value rewritten to
    the new columns, matched by key. (changes, a warning when values are lost)."""
    old_keys = [c["key"] for c in before.columns]
    new_keys = [c["key"] for c in new.columns]
    if old_keys == new_keys:
        return [], None
    where = {k: i for i, k in enumerate(old_keys)}
    removed = [i for i, k in enumerate(old_keys) if k not in new_keys]
    changes, lost, emptied = [], 0, set()
    for object_id, value in conn.execute(f'SELECT "_id", {q(new.key)} FROM {table} WHERE {q(new.key)} IS NOT NULL'):
        rows = json.loads(value)
        held = {i for i in removed if any(i < len(row) and row[i] is not None for row in rows)}
        if held:
            lost += 1
            emptied |= held
        out = [[row[where[k]] if k in where and where[k] < len(row) else None for k in new_keys] for row in rows]
        out = [row for row in out if any(v is not None for v in row)]
        changes.append((json.dumps(out, separators=(",", ":")) if out else None, object_id))
    if not lost:
        return changes, None
    names = ", ".join(f"'{before.columns[i]['label']}'" for i in sorted(emptied))
    return changes, (f"Removing the column{'s' if len(emptied) > 1 else ''} {names} of '{new.label}' "
                     f"deletes values in {lost} object(s).")


def delete_class(conn, schema: Schema, key: str, *, force: bool = False) -> None:
    cls = schema.get(key)
    incoming = [r for r in schema.incoming(cls) if r.cls is not cls]
    if incoming:
        names = ", ".join(f"{r.cls.label} › {r.label}" for r in incoming)
        raise AtlasError(409, f"Other classes link to {cls.label} ({names}). Remove those relationships first.")
    n = _count(conn, f"SELECT COUNT(*) FROM {q(cls.table)}")
    if n and not force:
        raise AtlasError(409, f"Deleting {cls.label} deletes its {n} object(s). "
                              f"Send it again with force=true to apply it.",
                         warnings=[f"{cls.label} holds {n} object(s)."], requires_force=True)
    with transaction(conn):
        for rel in cls.relationships:
            conn.execute(f"DROP TABLE IF EXISTS {q(rel.link_table)}")
        conn.execute(f"DROP TABLE IF EXISTS {q(cls.table)}")
        conn.execute("DELETE FROM atlas_class WHERE id = ?", (cls.id,))
        sweep_files(conn, Schema([c for c in schema.classes.values() if c is not cls]))


def sweep_files(conn, schema: Schema) -> None:
    """Delete uploads that no object's image or file column refers to any more."""
    refs = [f"SELECT {q(a.key)} FROM {q(c.table)} WHERE {q(a.key)} IS NOT NULL"
            for c in schema.classes.values() for a in c.stored_attributes if a.type in FILE_TYPES]
    if refs:
        conn.execute(f"DELETE FROM atlas_file WHERE id NOT IN ({' UNION ALL '.join(refs)})")
    else:
        conn.execute("DELETE FROM atlas_file")


# --------------------------------------------------------------------------- #
# API form
# --------------------------------------------------------------------------- #
def attribute_to_api(attr: Attribute, schema: Schema) -> dict:
    out = {"key": attr.key, "label": attr.label, "type": attr.type, "required": attr.required,
           "unique": attr.unique, "title": attr.title, "show": attr.show}
    if attr.type == "choice":
        out["options"] = list(attr.options)
    if attr.type == "matrix":
        out["columns"] = [dict(c) for c in attr.columns]
    if attr.type == "lookthrough":
        out["lookup"] = attr.lookup
        try:
            rels, final = schema.resolve(attr)
            out["value_type"] = final.type
            out["many"] = any(r.cardinality == "many" for r in rels)
            if final.type == "choice":
                out["options"] = list(final.options)
        except AtlasError:
            out["value_type"], out["many"] = "string", False
    return out


def class_to_api(cls: AtlasClass, schema: Schema, *, admin: bool) -> dict:
    out = {
        "key": cls.key,
        "label": cls.label,
        "path": cls.path,
        "description": cls.description,
        "color": cls.color,
        "icon": cls.icon,
        "attributes": [attribute_to_api(a, schema) for a in cls.attributes],
        "relationships": [{
            "key": r.key, "label": r.label, "target": r.target_key,
            "target_label": r.target.label if r.target else r.target_key,
            "target_color": r.target.color if r.target else "",
            "target_icon": r.target.icon if r.target else "",
            "cardinality": r.cardinality, "required": r.required, "show": r.show,
            "reverse_label": r.reverse_label,
        } for r in cls.relationships],
        "incoming": [{
            "class": r.cls.key, "class_label": r.cls.label, "relationship": r.key,
            "label": r.reverse_label or f"{r.cls.label} › {r.label}",
            "show": r.show, "color": r.cls.color, "icon": r.cls.icon,
        } for r in schema.incoming(cls)],
        "created_at": cls.created_at, "created_by": cls.created_by,
        "updated_at": cls.updated_at, "updated_by": cls.updated_by,
    }
    if admin:
        out["roles"] = list(cls.roles)
    return out
