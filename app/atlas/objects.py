"""Atlas objects: listing, reading, writing and deleting, always as one viewer
whose roles decide what they may see and do.

An object in the API:

    {"_id": 5, "_title": "S-12 · fail",
     "sample": "S-12", "result": "fail",      stored attributes
     "lab_code": "L1",                         look-throughs (a list when the path
                                               passes a 'many' relationship)
     "photo": {"name": "s12.jpg", "type": "image/jpeg", "size": 48213,
               "width": 1600, "height": 1200}, an upload (null when empty); the file
                                               itself is at …/objects/5/files/photo
     "readings": [210, 212.5],                 a list
     "curve": [[0, 0], [0.1, 210]],            a matrix: a row per entry, a value (or
                                               null) per column; with brief=true lists
                                               and matrices are {"count": n} instead
     "technician": 7,                          a portal user's id (GET /atlas/api/users
                                               has their names); writes also take the
                                               username
     "location": 3, "retests": [9, 12],        relationships: an id ('one') or ids
                                               ('many'); {"_id", "_title"} with expand
     "_roles": [], "_created_at": "…Z", "_created_by": "…", "_updated_at": "…Z",
     "_updated_by": "…", "_version": 3, "_can": {"update": true, "delete": false}}

Filters are query parameters named `field` or `field__op`, where op is one of
eq (the default), ne, gt, gte, lt, lte, contains, startswith, in (a comma
list), isnull (true/false) and on (the date part of a date or timestamp).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

from app.atlas import files
from app.atlas.db import connect, db_path, fold, transaction
from app.atlas.errors import AtlasError
from app.atlas.permissions import Viewer, can_create, can_on_row, row_filter
from app.atlas.schema import FILE_TYPES, SERIES_TYPES, SYSTEM_FIELDS, AtlasClass, Relationship, Schema, q, utc_now
from app.atlas.values import (coerce, object_ids, out_value, parse_date, parse_datetime, parse_flag,
                              parse_number, role_ids)

FILTER_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "contains", "startswith", "in", "isnull", "on"})
_COMPARE = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
PAGE_SIZE = 100
PAGE_SIZE_MAX = 1000
_META = ("_roles", "_created_at", "_created_by", "_updated_at", "_updated_by", "_version")
_READ_ONLY = frozenset({"_id", "_title", "_created_at", "_created_by", "_updated_at",
                        "_updated_by", "_version", "_can", "_incoming"})
_ESCAPE = "ESCAPE '\\'"


def _like(text: str, op: str) -> str:
    escaped = fold(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%" if op == "contains" else f"{escaped}%"


def _split(raw) -> list:
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _not_found(cls: AtlasClass, object_id) -> AtlasError:
    return AtlasError(404, f"{cls.label} #{object_id} doesn't exist, or you can't see it.")


# --------------------------------------------------------------------------- #
# Query building
# --------------------------------------------------------------------------- #
class Query:
    """SQL for one class as seen by one viewer; `s` is the object row everywhere."""

    def __init__(self, schema: Schema, cls: AtlasClass, viewer: Viewer):
        self.schema, self.cls, self.viewer = schema, cls, viewer
        self._n = 0
        self._paths = {}
        self._counted = None        # incoming relationships select() counts (None: not asked for)
        self.brief = False          # lists and matrices as {"count": n} (what a grid needs)

    def _alias(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    # -- fields ------------------------------------------------------------
    def path(self, attr):
        if attr not in self._paths:
            self._paths[attr] = self.schema.resolve(attr)
        return self._paths[attr]

    def final_type(self, attr) -> str:
        return attr.type if attr.stored else self.path(attr)[1].type

    def is_many(self, attr) -> bool:
        return not attr.stored and any(r.cardinality == "many" for r in self.path(attr)[0])

    def field_names(self) -> list:
        return [*SYSTEM_FIELDS, *(a.key for a in self.cls.attributes), *(r.key for r in self.cls.relationships)]

    def member(self, name: str):
        if name in SYSTEM_FIELDS:
            return "system", name
        attr = self.cls.attr(name)
        if attr is not None:
            return "attr", attr
        rel = self.cls.rel(name)
        if rel is not None:
            return "rel", rel
        raise AtlasError(400, f"'{name}' is not a field of {self.cls.label}. "
                              f"Fields: {', '.join(self.field_names())}.")

    # -- expressions -------------------------------------------------------
    def _walk(self, rels, final, src: str):
        """FROM and WHERE that reach a look-through's end from row `src`, plus its column."""
        links = [self._alias("l") for _ in rels]
        sql = f"{q(rels[0].link_table)} {links[0]}"
        for prev, rel, cur in zip(links, rels[1:], links[1:]):
            sql += f" JOIN {q(rel.link_table)} {cur} ON {cur}.source_id = {prev}.target_id"
        t = self._alias("t")
        sql += f' JOIN {q(final.cls.table)} {t} ON {t}."_id" = {links[-1]}.target_id'
        return sql, f'{links[0]}.source_id = {src}."_id"', f"{t}.{q(final.key)}"

    def value(self, attr, src: str, mode: str = "api") -> str:
        """An attribute's value on row src. mode: api (a JSON list for 'many'
        look-throughs; for an upload, a JSON object describing it), text
        (comma-joined) or sort (the smallest value). Lists and matrices sort by
        how many entries they have, and give only that count when brief. A user
        is their id for the API, their name for text and sort."""
        if attr.type in SERIES_TYPES:
            column = f"{src}.{q(attr.key)}"
            return f"json_array_length({column})" if mode == "sort" or (mode == "api" and self.brief) else column
        if attr.type in FILE_TYPES:
            if mode != "api":
                return self.file_name(attr, src)
            sizes = ", 'width', f.width, 'height', f.height" if attr.type == "image" else ""
            return (f"(SELECT json_object('name', f.name, 'type', f.type, 'size', f.size{sizes}) "
                    f"FROM atlas_file f WHERE f.id = {src}.{q(attr.key)})")
        if attr.stored:
            column = f"{src}.{q(attr.key)}"
            return f"atlas_user({column})" if attr.type == "user" and mode != "api" else column
        rels, final = self.path(attr)
        frm, where, col = self._walk(rels, final, src)
        if final.type == "user" and mode != "api":
            col = f"atlas_user({col})"
        if mode == "text":
            return f"(SELECT group_concat({col}, ', ') FROM {frm} WHERE {where})"
        if mode == "sort":
            return f"(SELECT min({col}) FROM {frm} WHERE {where})"
        if all(r.cardinality == "one" for r in rels):
            return f"(SELECT {col} FROM {frm} WHERE {where} LIMIT 1)"
        return f"(SELECT json_group_array({col}) FROM {frm} WHERE {where} AND {col} IS NOT NULL)"

    @staticmethod
    def file_name(attr, src: str) -> str:
        """The name of the file an image or file attribute holds on row src."""
        return f"(SELECT f.name FROM atlas_file f WHERE f.id = {src}.{q(attr.key)})"

    def title(self, cls: AtlasClass, src: str) -> str:
        """An object's title: its title attributes joined with ' · ', else '#id'."""
        parts = [self.value(a, src, "text") for a in cls.attributes if a.title]
        fallback = f"'#' || {src}.\"_id\""
        if not parts:
            return f"({fallback})"
        return f"COALESCE(NULLIF(atlas_join(' · ', {', '.join(parts)}), ''), {fallback})"

    def link_ids(self, rel: Relationship, src: str) -> str:
        l = self._alias("l")
        where = f'{l}.source_id = {src}."_id"'
        if rel.cardinality == "one":
            return f"(SELECT {l}.target_id FROM {q(rel.link_table)} {l} WHERE {where} LIMIT 1)"
        return f"(SELECT json_group_array({l}.target_id) FROM {q(rel.link_table)} {l} WHERE {where})"

    def _linked_title_like(self, rel: Relationship, op: str) -> str:
        l, t = self._alias("l"), self._alias("t")
        return (f"EXISTS (SELECT 1 FROM {q(rel.link_table)} {l} JOIN {q(rel.target.table)} {t} "
                f'ON {t}."_id" = {l}.target_id WHERE {l}.source_id = s."_id" '
                f"AND atlas_fold({self.title(rel.target, t)}) LIKE ? {_ESCAPE})")

    # -- filters -----------------------------------------------------------
    @staticmethod
    def _flag(raw, name: str) -> bool:
        try:
            return parse_flag(raw)
        except ValueError:
            raise AtlasError(400, f"Filter '{name}' needs true or false.") from None

    @staticmethod
    def _filter_value(ftype: str, raw, name: str):
        try:
            if ftype in ("int", "float"):
                number = parse_number(raw)
                return int(number) if number.is_integer() else number
            if ftype == "boolean":
                return coerce("boolean", raw)
            if ftype == "date":
                return parse_date(raw)
            if ftype == "datetime":
                text = str(raw).strip()
                return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else parse_datetime(text)
            if ftype == "timestamp":
                return str(raw).strip()
            return str(raw)
        except ValueError as exc:
            raise AtlasError(400, f"Filter '{name}': the value {exc}.") from None

    def _scalar(self, col: str, ftype: str, op: str, raw, name: str):
        if op == "isnull":
            return (f"{col} IS NULL" if self._flag(raw, name) else f"{col} IS NOT NULL"), []
        if ftype == "user":
            return self._user(col, op, raw, name)
        if op in ("contains", "startswith"):
            return f"atlas_fold(CAST({col} AS TEXT)) LIKE ? {_ESCAPE}", [_like(str(raw), op)]
        if op == "on":
            if ftype not in ("date", "datetime", "timestamp"):
                raise AtlasError(400, f"Filter '{name}': 'on' works with dates only.")
            return f"substr({col}, 1, 10) = ?", [self._filter_value("date", raw, name)]
        if op == "in":
            values = [self._filter_value(ftype, part, name) for part in _split(raw)]
            if not values:
                return "0", []
            return f"{col} IN ({', '.join('?' * len(values))})", values
        value = self._filter_value(ftype, raw, name)
        if op == "ne":
            return f"({col} IS NULL OR {col} != ?)", [value]
        return f"{col} {_COMPARE[op]} ?", [value]

    @staticmethod
    def _user(col: str, op: str, raw, name: str):
        """A user filter: eq, ne and in take ids or usernames; contains and startswith match the name."""
        if op in ("contains", "startswith"):
            return f"atlas_fold(atlas_user({col}) || ' ' || atlas_username({col})) LIKE ? {_ESCAPE}", [_like(str(raw), op)]
        if op not in ("eq", "ne", "in"):
            raise AtlasError(400, f"Filter '{name}': users support eq, ne, in, isnull, contains and startswith.")
        parts = _split(raw) if op == "in" else [str(raw).strip()]
        ids = [int(p.lstrip("#")) for p in parts if p.lstrip("#").isdigit()]
        names = [p for p in parts if p and not p.lstrip("#").isdigit()]
        conds, args = [], []
        if ids:
            conds.append(f"{col} IN ({', '.join('?' * len(ids))})")
            args += ids
        if names:
            conds.append(f"atlas_username({col}) IN ({', '.join('?' * len(names))})")
            args += names
        if not conds:
            return ("1" if op == "ne" else "0"), []
        cond = " OR ".join(conds)
        return (f"({col} IS NULL OR NOT ({cond}))" if op == "ne" else f"({cond})"), args

    def _relationship_condition(self, rel: Relationship, op: str, raw, name: str):
        l = self._alias("l")
        linked = f'SELECT 1 FROM {q(rel.link_table)} {l} WHERE {l}.source_id = s."_id"'
        if op == "isnull":
            return (f"NOT EXISTS ({linked})" if self._flag(raw, name) else f"EXISTS ({linked})"), []
        if op in ("eq", "ne", "in"):
            try:
                ids = object_ids(_split(raw) if op == "in" else raw)
            except ValueError:
                raise AtlasError(400, f"Filter '{name}' needs object ids.") from None
            if not ids:
                return ("1" if op == "ne" else "0"), []
            exists = f"EXISTS ({linked} AND {l}.target_id IN ({', '.join('?' * len(ids))}))"
            return (f"NOT {exists}" if op == "ne" else exists), ids
        if op in ("contains", "startswith"):
            return self._linked_title_like(rel, op), [_like(str(raw), op)]
        raise AtlasError(400, f"Filter '{name}': relationships support eq, ne, in, isnull, "
                              f"contains and startswith.")

    def condition(self, name: str, op: str, raw):
        if op not in FILTER_OPS:
            raise AtlasError(400, f"Unknown filter '{name}__{op}'. Operators: {', '.join(sorted(FILTER_OPS))}.")
        kind, member = self.member(name)
        if kind == "system":
            return self._scalar(f"s.{q(name)}", SYSTEM_FIELDS[name], op, raw, name)
        if kind == "rel":
            return self._relationship_condition(member, op, raw, name)
        if member.type in FILE_TYPES:         # uploads filter by whether there is one, or by file name
            if op == "isnull":
                return self._scalar(f"s.{q(member.key)}", "int", op, raw, name)
            if op not in ("eq", "ne", "contains", "startswith"):
                raise AtlasError(400, f"Filter '{name}': images and files support isnull, and eq, ne, "
                                      f"contains and startswith on the file name.")
            return self._scalar(self.file_name(member, "s"), "string", op, raw, name)
        if member.type in SERIES_TYPES:       # lists and matrices: whether there is one
            if op != "isnull":
                raise AtlasError(400, f"Filter '{name}': lists and matrices support isnull only.")
            return self._scalar(f"s.{q(member.key)}", "int", op, raw, name)
        if member.stored:
            return self._scalar(f"s.{q(member.key)}", member.type, op, raw, name)
        rels, final = self.path(member)       # look-through: true when any value matches
        frm, where, col = self._walk(rels, final, "s")

        def exists(cond: str) -> str:
            return f"EXISTS (SELECT 1 FROM {frm} WHERE {where} AND {cond})"

        if op == "isnull":
            present = exists(f"{col} IS NOT NULL")
            return (f"NOT {present}" if self._flag(raw, name) else present), []
        if op == "ne":
            cond, args = self._scalar(col, final.type, "eq", raw, name)
            return f"NOT {exists(cond)}", args
        cond, args = self._scalar(col, final.type, op, raw, name)
        return exists(cond), args

    def search(self, text: str):
        """Every word must appear in some field (or linked title); accents and case don't matter."""
        clauses, args = [], []
        for word in fold(text).split():
            pattern = _like(word, "contains")
            parts = []
            for attr in self.cls.attributes:
                ftype = self.final_type(attr)
                if ftype == "boolean" or attr.type in SERIES_TYPES:
                    continue
                if attr.type in FILE_TYPES:
                    parts.append(f"atlas_fold({self.file_name(attr, 's')}) LIKE ? {_ESCAPE}")
                    continue
                frm = None
                if attr.stored:
                    col = f"s.{q(attr.key)}"
                else:
                    rels, final = self.path(attr)
                    frm, where, col = self._walk(rels, final, "s")
                text = f"atlas_user({col}) || ' ' || atlas_username({col})" if ftype == "user" else f"CAST({col} AS TEXT)"
                match = f"atlas_fold({text}) LIKE ? {_ESCAPE}"
                parts.append(match if frm is None else f"EXISTS (SELECT 1 FROM {frm} WHERE {where} AND {match})")
            for rel in self.cls.relationships:
                parts.append(self._linked_title_like(rel, "contains"))
            for name in ("_created_by", "_updated_by"):
                parts.append(f"atlas_fold(s.{q(name)}) LIKE ? {_ESCAPE}")
            word_args = [pattern] * len(parts)
            if word.isdigit():
                parts.append('s."_id" = ?')
                word_args.append(int(word))
            clauses.append("(" + " OR ".join(parts) + ")")
            args += word_args
        return " AND ".join(clauses), args

    def where(self, filters, text=None):
        cond, cond_args = row_filter(self.viewer, self.cls, "read", "s")
        clauses, args = [cond], list(cond_args)
        for name, raw in filters:
            field, op = name.rsplit("__", 1) if "__" in name else (name, "eq")
            cond, cond_args = self.condition(field, op, raw)
            clauses.append(cond)
            args += cond_args
        if text and text.strip():
            cond, cond_args = self.search(text)
            clauses.append(cond)
            args += cond_args
        return " AND ".join(f"({c})" for c in clauses), args

    def order_by(self, sort) -> str:
        terms = []
        for item in str(sort or "").split(","):
            item = item.strip()
            if not item:
                continue
            name = item.lstrip("+-")
            if name == "_title":
                expr = self.title(self.cls, "s")
            else:
                kind, member = self.member(name)
                if kind == "system":
                    expr = f"s.{q(name)}"
                elif kind == "rel":
                    l, t = self._alias("l"), self._alias("t")
                    expr = (f"(SELECT min({self.title(member.target, t)}) FROM {q(member.link_table)} {l} "
                            f'JOIN {q(member.target.table)} {t} ON {t}."_id" = {l}.target_id '
                            f'WHERE {l}.source_id = s."_id")')
                else:
                    expr = self.value(member, "s", "sort")
            terms.append(f"{expr} {'DESC' if item.startswith('-') else 'ASC'} NULLS LAST")
        terms.append('s."_id" ASC')
        return ", ".join(terms)

    # -- output ------------------------------------------------------------
    def parse_fields(self, spec):
        if not spec:
            return None
        names = {name.strip() for name in str(spec).split(",") if name.strip()}
        unknown = names - {"_title", "_can", *_META, *self.field_names()}
        if unknown:
            raise AtlasError(400, f"Unknown field(s) in fields: {', '.join(sorted(unknown))}.")
        return names | {"_id"}

    def incoming_counts(self):
        """Per incoming relationship, how many objects the viewer can read link to row s."""
        cols, args = [], []
        self._counted = list(self.schema.incoming(self.cls))
        for n, rel in enumerate(self._counted):
            k, i = self._alias("k"), self._alias("i")
            read, read_args = row_filter(self.viewer, rel.cls, "read", i)
            cols.append(f'(SELECT COUNT(*) FROM {q(rel.link_table)} {k} JOIN {q(rel.cls.table)} {i} '
                        f'ON {i}."_id" = {k}.source_id WHERE {k}.target_id = s."_id" AND ({read})) AS "_in_{n}"')
            args += read_args
        return cols, args

    def select(self, fields=None, *, incoming_counts=False, brief=False):
        def want(key):
            return fields is None or key in fields

        self.brief = brief
        cols, args = ['s."_id" AS "_id"'], []
        if want("_title"):
            cols.append(f'{self.title(self.cls, "s")} AS "_title"')
        for attr in self.cls.attributes:
            if want(attr.key):
                cols.append(f"{self.value(attr, 's')} AS {q(attr.key)}")
        for rel in self.cls.relationships:
            if want(rel.key):
                cols.append(f"{self.link_ids(rel, 's')} AS {q(rel.key)}")
        for name in _META:
            if want(name):
                cols.append(f"s.{q(name)} AS {q(name)}")
        if want("_can"):
            for op in ("update", "delete"):
                cond, cond_args = row_filter(self.viewer, self.cls, op, "s")
                cols.append(f'CASE WHEN {cond} THEN 1 ELSE 0 END AS "_can_{op}"')
                args += cond_args
        if incoming_counts:
            count_cols, count_args = self.incoming_counts()
            cols += count_cols
            args += count_args
        return ", ".join(cols), args

    def to_api(self, row) -> dict:
        keys = set(row.keys())
        out = {"_id": row["_id"]}
        if "_title" in keys:
            out["_title"] = row["_title"]
        for attr in self.cls.attributes:
            if attr.key in keys:
                value, ftype = row[attr.key], self.final_type(attr)
                if ftype in SERIES_TYPES and self.brief:
                    out[attr.key] = None if value is None else {"count": value}
                elif ftype in FILE_TYPES:
                    out[attr.key] = json.loads(value) if value else None
                elif self.is_many(attr):
                    out[attr.key] = [out_value(ftype, v) for v in json.loads(value or "[]")]
                else:
                    out[attr.key] = out_value(ftype, value)
        for rel in self.cls.relationships:
            if rel.key in keys:
                value = row[rel.key]
                out[rel.key] = json.loads(value or "[]") if rel.cardinality == "many" else value
        if "_roles" in keys:
            out["_roles"] = json.loads(row["_roles"])
        for name in _META[1:]:
            if name in keys:
                out[name] = row[name]
        if "_can_update" in keys:
            out["_can"] = {"update": bool(row["_can_update"]), "delete": bool(row["_can_delete"])}
        if self._counted is not None:
            out["_incoming_counts"] = {f"{rel.cls.key}.{rel.key}": row[f"_in_{n}"]
                                       for n, rel in enumerate(self._counted)}
        return out

    def titles(self, conn, cls: AtlasClass, ids) -> dict:
        ids, found = sorted(set(ids)), {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            sub = Query(self.schema, cls, self.viewer)
            sql = (f'SELECT s."_id", {sub.title(cls, "s")} FROM {q(cls.table)} s '
                   f'WHERE s."_id" IN ({", ".join("?" * len(chunk))})')
            found.update({row[0]: row[1] for row in conn.execute(sql, chunk)})
        return found

    def expand(self, conn, items: list, spec) -> None:
        """Replace relationship ids by {"_id", "_title"}; spec is '*' or a comma list."""
        if not spec:
            return
        names = [name.strip() for name in str(spec).split(",") if name.strip()]
        if names == ["*"]:
            rels = self.cls.relationships
        else:
            rels = []
            for name in names:
                rel = self.cls.rel(name)
                if rel is None:
                    raise AtlasError(400, f"expand: '{name}' is not a relationship of {self.cls.label}.")
                rels.append(rel)
        for rel in rels:
            present = [item for item in items if rel.key in item]
            ids = [i for item in present
                   for i in (item[rel.key] if rel.cardinality == "many" else [item[rel.key]]) if i is not None]
            titles = self.titles(conn, rel.target, ids)
            for item in present:
                value = item[rel.key]
                if rel.cardinality == "many":
                    item[rel.key] = [{"_id": i, "_title": titles.get(i, f"#{i}")} for i in value]
                elif value is not None:
                    item[rel.key] = {"_id": value, "_title": titles.get(value, f"#{value}")}


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def class_visible(conn, viewer: Viewer, cls: AtlasClass) -> bool:
    """Does the class belong in this viewer's list (can they create or see anything)?"""
    if viewer.is_admin or not cls.roles or can_create(viewer, cls):
        return True
    read, args = row_filter(viewer, cls, "read", "s")
    if read in ("1", "0"):
        return read == "1"
    return conn.execute(f"SELECT 1 FROM {q(cls.table)} s WHERE {read} LIMIT 1", args).fetchone() is not None


def list_objects(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, filters, *, text=None, sort=None,
                 page: int = 1, page_size: int = PAGE_SIZE, expand=None, fields=None,
                 incoming_counts: bool = False, brief: bool = False) -> dict:
    """A page of objects. With incoming_counts, each one also gets _incoming_counts:
    {"class.relationship": how many objects you can read link to it that way}.
    With brief, lists and matrices are {"count": n}: a grid page stays small when
    every object holds a curve of thousands of points."""
    query = Query(schema, cls, viewer)
    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), PAGE_SIZE_MAX)
    wanted = query.parse_fields(fields)
    where, args = query.where(filters, text)
    total = conn.execute(f"SELECT COUNT(*) FROM {q(cls.table)} s WHERE {where}", args).fetchone()[0]
    select_sql, select_args = query.select(wanted, incoming_counts=incoming_counts, brief=brief)
    rows = conn.execute(
        f"SELECT {select_sql} FROM {q(cls.table)} s WHERE {where} "
        f"ORDER BY {query.order_by(sort or '_id')} LIMIT ? OFFSET ?",
        [*select_args, *args, page_size, (page - 1) * page_size]).fetchall()
    items = [query.to_api(row) for row in rows]
    query.expand(conn, items, expand)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


INCOMING_PREVIEW = 20      # linking objects a single object read lists per relationship


def count_objects(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, filters, *, text=None) -> int:
    where, args = Query(schema, cls, viewer).where(filters, text)
    return conn.execute(f"SELECT COUNT(*) FROM {q(cls.table)} s WHERE {where}", args).fetchone()[0]


def iter_objects(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, filters, *, text=None, sort=None,
                 expand=None, incoming_counts: bool = False, chunk: int = 1000):
    """Every object the list would give (filters, search, sort; no pages), as lists of
    API items of up to `chunk` objects, so an export never holds all rows at once."""
    query = Query(schema, cls, viewer)
    where, args = query.where(filters, text)
    select_sql, select_args = query.select(None, incoming_counts=incoming_counts)
    cursor = conn.execute(f"SELECT {select_sql} FROM {q(cls.table)} s WHERE {where} "
                          f"ORDER BY {query.order_by(sort or '_id')}", [*select_args, *args])
    while True:
        rows = cursor.fetchmany(chunk)
        if not rows:
            return
        items = [query.to_api(row) for row in rows]
        query.expand(conn, items, expand)
        yield items


def _incoming(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, object_id: int) -> list:
    """Objects (that the viewer can read) linking to this one, grouped by relationship:
    each group's count and its first INCOMING_PREVIEW items. A test type can have
    thousands of results; page through them all with the list API and the
    relationship as a filter (GET /classes/<source>/objects?<relationship>=<id>)."""
    groups = []
    for rel in schema.incoming(cls):
        source = rel.cls
        read, read_args = row_filter(viewer, source, "read", "s")
        linking = (f'FROM {q(rel.link_table)} k JOIN {q(source.table)} s ON s."_id" = k.source_id '
                   f'WHERE k.target_id = ? AND ({read})')
        count = conn.execute(f"SELECT COUNT(*) {linking}", [object_id, *read_args]).fetchone()[0]
        if not count:
            continue
        title = Query(schema, source, viewer).title(source, "s")
        rows = conn.execute(f'SELECT s."_id", {title} {linking} ORDER BY s."_id" LIMIT ?',
                            [object_id, *read_args, INCOMING_PREVIEW]).fetchall()
        groups.append({"class": source.key, "class_label": source.label, "relationship": rel.key,
                       "label": rel.reverse_label or f"{source.label} › {rel.label}", "count": count,
                       "items": [{"_id": row[0], "_title": row[1]} for row in rows]})
    return groups


def get_object(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, object_id: int, *,
               expand=None, incoming: bool = True) -> dict:
    query = Query(schema, cls, viewer)
    read, read_args = row_filter(viewer, cls, "read", "s")
    select_sql, select_args = query.select()
    row = conn.execute(f'SELECT {select_sql} FROM {q(cls.table)} s WHERE s."_id" = ? AND ({read})',
                       [*select_args, object_id, *read_args]).fetchone()
    if row is None:
        raise _not_found(cls, object_id)
    item = query.to_api(row)
    query.expand(conn, [item], expand)
    if incoming:
        item["_incoming"] = _incoming(conn, schema, cls, viewer, object_id)
    return item


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def _parse_write(conn, cls: AtlasClass, payload, viewer: Viewer, current_roles: list):
    """(values, links, roles) from a POST/PATCH body; roles is None when not sent."""
    if not isinstance(payload, dict):
        raise AtlasError(400, "Send the object as a JSON object.")
    values, links, roles, errors = {}, {}, None, []
    for key, value in payload.items():
        if key in _READ_ONLY:
            continue
        if key == "_roles":
            try:
                roles = role_ids(value)
            except ValueError as exc:
                errors.append(f"_roles {exc}.")
            continue
        attr = cls.attr(key)
        if attr is not None:
            if attr.type in FILE_TYPES:           # uploads go through PUT …/files/<key>
                if value is None:
                    values[key] = None            # null clears it
                elif not isinstance(value, dict):  # a dict is what GET returned: left as it is
                    errors.append(f"{attr.label}: upload it with PUT …/objects/<id>/files/{key}.")
            elif attr.type in SERIES_TYPES and isinstance(value, dict) and set(value) == {"count"}:
                continue                          # what a brief list returned: left as it is
            elif attr.type == "user":             # an id or a username, of a user the portal has
                try:
                    values[key] = conn.users.find(value)
                except ValueError as exc:
                    errors.append(f"{attr.label}: {exc}.")
            elif attr.stored:                     # look-throughs are computed: ignored
                try:
                    values[key] = coerce(attr.type, value, attr.options, columns=attr.width)
                except ValueError as exc:
                    errors.append(f"{attr.label} {exc}.")
            continue
        rel = cls.rel(key)
        if rel is not None:
            try:
                ids = object_ids(value)
            except ValueError:
                errors.append(f"{rel.label} must be object ids.")
                continue
            if rel.cardinality == "one" and len(ids) > 1:
                errors.append(f"{rel.label} links to one object only.")
                continue
            links[key] = ids
            continue
        errors.append(f"'{key}' is not a field of {cls.label}.")
    if errors:
        raise AtlasError(400, " ".join(errors))
    if roles is not None and set(roles) != set(current_roles) and not viewer.is_admin:
        raise AtlasError(403, "Only Data Admins and Superadmins can change roles.")
    return values, links, roles


def _check_roles(roles, valid_roles) -> None:
    unknown = [role_id for role_id in roles if role_id not in valid_roles]
    if unknown:
        raise AtlasError(400, "Unknown role id(s): " + ", ".join(map(str, unknown)) + ".")


def _require(cls: AtlasClass, values: dict, links: dict, *, uploads: bool = True) -> None:
    """Required attributes and relationships need a value. A new object can't carry
    its uploads yet (they are sent once it exists), so creating skips those."""
    missing = [a.label for a in cls.stored_attributes
               if a.required and values.get(a.key) is None and (uploads or a.type not in FILE_TYPES)]
    missing += [r.label for r in cls.relationships if r.required and not links.get(r.key)]
    if missing:
        raise AtlasError(400, "Required: " + ", ".join(missing) + ".")


def _check_targets(conn, viewer: Viewer, rel: Relationship, ids, already=()) -> None:
    """New link targets must exist and be readable by the viewer."""
    new = [i for i in ids if i not in set(already)]
    if not new:
        return
    read, read_args = row_filter(viewer, rel.target, "read", "t")
    found = {row[0] for row in conn.execute(
        f'SELECT t."_id" FROM {q(rel.target.table)} t WHERE t."_id" IN ({", ".join("?" * len(new))}) '
        f"AND ({read})", [*new, *read_args])}
    missing = [i for i in new if i not in found]
    if missing:
        raise AtlasError(400, f"{rel.label}: {rel.target.label} " + ", ".join(f"#{i}" for i in missing)
                              + " doesn't exist or you can't see it.")


def _linked_ids(conn, rel: Relationship, object_id: int) -> list:
    return [row[0] for row in conn.execute(
        f"SELECT target_id FROM {q(rel.link_table)} WHERE source_id = ? ORDER BY rowid", (object_id,))]


def _write_links(conn, rel: Relationship, object_id: int, ids) -> None:
    conn.execute(f"DELETE FROM {q(rel.link_table)} WHERE source_id = ?", (object_id,))
    conn.executemany(f"INSERT INTO {q(rel.link_table)} (source_id, target_id) VALUES (?, ?)",
                     [(object_id, i) for i in ids])


def _unique_error(conn, cls: AtlasClass, values: dict, exc: sqlite3.IntegrityError) -> AtlasError:
    match = re.search(r"UNIQUE constraint failed: [^.\s]+\.(\w+)", str(exc))
    attr = cls.attr(match.group(1)) if match else None
    if attr is not None:
        value = values.get(attr.key)
        shown = conn.users.name(value) if attr.type == "user" else value
        return AtlasError(409, f"{attr.label} must be unique: '{shown}' is already used.")
    return AtlasError(409, f"This conflicts with an existing object ({exc}).")


def _changed(cls: AtlasClass, row) -> AtlasError:
    return AtlasError(409, f"{cls.label} #{row['_id']} was changed by {row['_updated_by']} "
                           f"(now version {row['_version']}). Reload it and try again.",
                      version=row["_version"])


def create_object(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, payload, valid_roles) -> int:
    if not can_create(viewer, cls):
        raise AtlasError(403, f"You can't create objects in {cls.label}.")
    values, links, roles = _parse_write(conn, cls, payload, viewer, [])
    roles = roles or []
    _check_roles(roles, valid_roles)
    _require(cls, values, links, uploads=False)
    now = utc_now()
    columns = [*_META, *values]
    with transaction(conn):
        for key, ids in links.items():
            _check_targets(conn, viewer, cls.rel(key), ids)
        try:
            object_id = conn.execute(
                f"INSERT INTO {q(cls.table)} ({', '.join(q(c) for c in columns)}) "
                f"VALUES ({', '.join('?' * len(columns))})",
                [json.dumps(roles), now, viewer.username, now, viewer.username, 1, *values.values()]).lastrowid
        except sqlite3.IntegrityError as exc:
            raise _unique_error(conn, cls, values, exc) from None
        for key, ids in links.items():
            _write_links(conn, cls.rel(key), object_id, ids)
    return object_id


def update_object(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, object_id: int, payload,
                  valid_roles) -> bool:
    """Apply a partial update; False when it changes nothing (no new version).
    Sending "_version" makes it fail with 409 if the object changed since that
    version was read."""
    if not isinstance(payload, dict):
        raise AtlasError(400, "Send the object as a JSON object.")
    row = conn.execute(f'SELECT * FROM {q(cls.table)} WHERE "_id" = ?', (object_id,)).fetchone()
    current_roles = json.loads(row["_roles"]) if row else []
    if row is None or not can_on_row(viewer, cls, "read", current_roles):
        raise _not_found(cls, object_id)
    if not can_on_row(viewer, cls, "update", current_roles):
        raise AtlasError(403, f"You can't change {cls.label} #{object_id}.")
    if payload.get("_version") is not None:
        try:
            expected = int(payload["_version"])
        except (TypeError, ValueError):
            raise AtlasError(400, "_version must be a number.") from None
        if expected != row["_version"]:
            raise _changed(cls, row)
    values, links, roles = _parse_write(conn, cls, payload, viewer, current_roles)
    if roles is not None:
        _check_roles(roles, valid_roles)
    current_links = {rel.key: _linked_ids(conn, rel, object_id) for rel in cls.relationships}
    merged = {a.key: values[a.key] if a.key in values else row[a.key] for a in cls.stored_attributes}
    _require(cls, merged, {key: links.get(key, ids) for key, ids in current_links.items()})

    changed = {k: v for k, v in values.items() if v != row[k] or type(v) is not type(row[k])}
    relinked = {k: ids for k, ids in links.items() if set(ids) != set(current_links[k])}
    roles_changed = roles is not None and set(roles) != set(current_roles)
    if not (changed or relinked or roles_changed):
        return False                             # nothing to save: no new version
    sets = [f"{q(k)} = ?" for k in changed] + ['"_updated_at" = ?', '"_updated_by" = ?',
                                               '"_version" = "_version" + 1']
    args = [*changed.values(), utc_now(), viewer.username]
    if roles_changed:
        sets.append('"_roles" = ?')
        args.append(json.dumps(roles))
    with transaction(conn):
        for key, ids in relinked.items():
            _check_targets(conn, viewer, cls.rel(key), ids, current_links[key])
        try:
            cursor = conn.execute(f'UPDATE {q(cls.table)} SET {", ".join(sets)} WHERE "_id" = ? AND "_version" = ?',
                                  [*args, object_id, row["_version"]])
        except sqlite3.IntegrityError as exc:
            raise _unique_error(conn, cls, changed, exc) from None
        if cursor.rowcount == 0:
            raise AtlasError(409, f"{cls.label} #{object_id} was changed meanwhile. Reload it and try again.")
        for key, ids in relinked.items():
            _write_links(conn, cls.rel(key), object_id, ids)
        files.delete(conn, [row[k] for k, v in changed.items() if v is None and cls.attr(k).type in FILE_TYPES])
    return True


def delete_object(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, object_id: int) -> None:
    uploads = [a.key for a in cls.stored_attributes if a.type in FILE_TYPES]
    row = conn.execute(f'SELECT {", ".join(q(c) for c in ["_roles", *uploads])} FROM {q(cls.table)} '
                       f'WHERE "_id" = ?', (object_id,)).fetchone()
    current_roles = json.loads(row["_roles"]) if row else []
    if row is None or not can_on_row(viewer, cls, "read", current_roles):
        raise _not_found(cls, object_id)
    if not can_on_row(viewer, cls, "delete", current_roles):
        raise AtlasError(403, f"You can't delete {cls.label} #{object_id}.")
    for rel in schema.incoming(cls):
        if not rel.required:
            continue
        stuck = [row[0] for row in conn.execute(
            f"SELECT k.source_id FROM {q(rel.link_table)} k WHERE k.target_id = ? AND NOT EXISTS "
            f"(SELECT 1 FROM {q(rel.link_table)} o WHERE o.source_id = k.source_id AND o.target_id != ?) "
            f"LIMIT 5", (object_id, object_id))]
        if stuck:
            raise AtlasError(409, f"{cls.label} #{object_id} is the only '{rel.label}' of {rel.cls.label} "
                                  + ", ".join(f"#{i}" for i in stuck) + ". Link those to something else first.")
    with transaction(conn):
        conn.execute(f'DELETE FROM {q(cls.table)} WHERE "_id" = ?', (object_id,))
        files.delete(conn, [row[key] for key in uploads])


# --------------------------------------------------------------------------- #
# Uploads (image and file attributes)
# --------------------------------------------------------------------------- #
def upload_attribute(cls: AtlasClass, key: str):
    attr = cls.attr(key)
    if attr is None or attr.type not in FILE_TYPES:
        raise AtlasError(404, f"{cls.label} has no image or file attribute '{key}'.")
    return attr


def set_file(conn, cls: AtlasClass, viewer: Viewer, object_id: int, key: str, stored, version=None) -> None:
    """Put an upload (as files.prepare() returns it) into an image or file attribute,
    or clear it (stored=None); the file it replaces is deleted. With a version, it
    fails with 409 if the object changed since that version was read."""
    attr = upload_attribute(cls, key)
    row = conn.execute(f'SELECT "_id", "_roles", "_version", "_updated_by", {q(key)} AS current '
                       f'FROM {q(cls.table)} WHERE "_id" = ?', (object_id,)).fetchone()
    roles = json.loads(row["_roles"]) if row else []
    if row is None or not can_on_row(viewer, cls, "read", roles):
        raise _not_found(cls, object_id)
    if not can_on_row(viewer, cls, "update", roles):
        raise AtlasError(403, f"You can't change {cls.label} #{object_id}.")
    if version is not None and int(version) != row["_version"]:
        raise _changed(cls, row)
    if stored is None and attr.required:
        raise AtlasError(400, f"{attr.label} is required: upload another one instead.")
    if stored is None and row["current"] is None:
        return                                   # nothing to clear: no new version
    now = utc_now()
    with transaction(conn):
        new_id = files.insert(conn, stored, viewer.username, now) if stored is not None else None
        cursor = conn.execute(
            f'UPDATE {q(cls.table)} SET {q(key)} = ?, "_updated_at" = ?, "_updated_by" = ?, '
            f'"_version" = "_version" + 1 WHERE "_id" = ? AND "_version" = ?',
            (new_id, now, viewer.username, object_id, row["_version"]))
        if cursor.rowcount == 0:
            raise AtlasError(409, f"{cls.label} #{object_id} was changed meanwhile. Reload it and try again.")
        files.delete(conn, [row["current"]])


def get_file(conn, cls: AtlasClass, viewer: Viewer, object_id: int, key: str, view: str = "original") -> dict:
    """An object's upload as files.load() returns it, when the viewer may read the object."""
    attr = upload_attribute(cls, key)
    read, read_args = row_filter(viewer, cls, "read", "s")
    row = conn.execute(f'SELECT s.{q(key)} FROM {q(cls.table)} s WHERE s."_id" = ? AND ({read})',
                       [object_id, *read_args]).fetchone()
    if row is None:
        raise _not_found(cls, object_id)
    if row[0] is None:
        raise AtlasError(404, f"{cls.label} #{object_id} has no {attr.label}.")
    return files.load(conn, row[0], view)


# --------------------------------------------------------------------------- #
# Portal roles
# --------------------------------------------------------------------------- #
_DROP_ROLE = ('UPDATE {table} SET {col} = (SELECT json_group_array(value) FROM json_each({table}.{col}) '
              'WHERE value != ?) WHERE EXISTS (SELECT 1 FROM json_each({table}.{col}) WHERE value = ?)')


def purge_role(role_id: int) -> None:
    """Remove a deleted portal role from every class and object in Atlas."""
    if not os.path.exists(db_path()):
        return
    with connect() as conn, transaction(conn):
        conn.execute(_DROP_ROLE.format(table="atlas_class", col="roles"), (role_id, role_id))
        for (key,) in conn.execute("SELECT key FROM atlas_class").fetchall():
            conn.execute(_DROP_ROLE.format(table=q(f"obj_{key}"), col='"_roles"'), (role_id, role_id))
