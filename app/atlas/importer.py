"""Importing objects from an Excel workbook (.xlsx, .xlsm) or a CSV file. Imports are
for Data Admins and Superadmins (the API checks that); everyone else adds objects
in the form.

The first row of the sheet that holds anything names the columns. Each column
goes into one field of the class (an attribute, a relationship, the ID or the
Version) or is skipped. Columns are matched to fields by label or key, without
regard to case or Turkish letters, so an export imports back as it is; the
caller can map them differently.

A row with an ID changes that object. A row without one creates an object; with
match=<a unique attribute>, it changes the object that already holds the same
value, when there is one. Only fields that have a column change, and an empty
cell clears its field. Cells read as the API reads values (dates as 2026-11-25,
25.11.2026 or Excel dates; yes/no; lists as 210; 212; …). A linked object is its
title or #id ("Çekme", "#12", or "Çekme (#12)" as an export writes it), several
separated by semicolons. Roles are written the same way, by name or #id. A
portal user is their username or name ("ayse", "Ayşe Yılmaz", or "Ayşe Yılmaz
(ayse)" as an export writes it).

Every row goes through the checks the API makes (required fields, unique values,
link targets and the rest), all in one transaction: a dry run rolls it back
and reports what would happen, and a real import commits only when no row has a
problem, so a file goes in whole or not at all. A row can link by title to an
object a row above it made. A cell showing an Excel error (#N/A, #REF! …) is a
problem, not text; so is a CSV row with more cells than the header, whose values
have probably shifted. The ' a CSV export puts before text starting with = + - @
(so Excel doesn't run it as a formula) comes off again.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
import sqlite3

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.atlas.db import fold
from app.atlas.errors import AtlasError
from app.atlas.export import FORMULA_START, _sheet_title, system_header
from app.atlas.objects import Query, create_object, update_object
from app.atlas.permissions import Viewer, row_filter
from app.atlas.schema import FILE_TYPES, SERIES_TYPES, TYPE_NAMES, AtlasClass, Schema, q
from app.atlas.values import coerce

MAX_ROWS = 50_000
ERROR_MESSAGES_MAX = 100        # different problems listed; more rows with them are only counted
ROWS_PER_ERROR = 20             # row numbers listed per problem
EXAMPLES = 3                    # example values shown per column
SYSTEM_HEADERS = frozenset({"created", "created by", "updated", "updated by"})
_ID_TOKEN = re.compile(r"#\s*(\d+)")              # "#12"
_TITLED_ID = re.compile(r"\(#(\d+)\)\s*$")       # "Çekme (#12)", as exports write links
_TOO_LONG = re.compile(r"\(\d[\d,.]*\s+(?:rows|values): too long for an Excel cell")   # export.TOO_LONG
EXCEL_ERRORS = frozenset({"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA",
                          "#SPILL!", "#CALC!", "#FIELD!", "#BLOCKED!", "#CONNECT!", "#BUSY!", "#UNKNOWN!"})
WIDE_ROW = ("This row has more cells than the header row has columns: a value probably holds a comma or "
            "semicolon without quotes around it, so the cells after it have moved.")


def _norm(text) -> str:
    return " ".join(fold(text).split())


def _letter(index: int) -> str:
    return get_column_letter(index + 1)


# --------------------------------------------------------------------------- #
# Reading the file
# --------------------------------------------------------------------------- #
class Sheet:
    def __init__(self, name: str, headers: list, rows: list, header_row: int, wide=frozenset()):
        self.name, self.headers, self.rows, self.header_row = name, headers, rows, header_row
        self.wide = wide            # numbers of the rows with more cells than the header


def _table(name: str, raw_rows, *, strict: bool = False) -> Sheet:
    """The header (the first row with anything in it) and the data rows below it,
    each as (its row number in the file, its cells). Empty rows are left out.
    strict (CSV): a row with something past the header's last column is noted in
    `wide`; in Excel such a cell has no column name and is left out."""
    headers, header_row, rows, wide = None, 0, [], set()
    for number, cells in enumerate(raw_rows, start=1):
        cells = list(cells)
        if headers is None:
            if any(not _blank(c) for c in cells):
                while cells and _blank(cells[-1]):
                    cells.pop()
                headers = [_header(c, i) for i, c in enumerate(cells)]
                header_row = number
            continue
        extra = strict and any(not _blank(c) for c in cells[len(headers):])
        cells = cells[:len(headers)]
        if extra or any(not _blank(c) for c in cells):
            if len(rows) == MAX_ROWS:
                raise AtlasError(400, f"'{name}' has more than {MAX_ROWS:,} rows. Import it in parts.")
            if extra:
                wide.add(number)
            rows.append((number, cells + [None] * (len(headers) - len(cells))))
    return Sheet(name, headers or [], rows, header_row, wide)


def _blank(cell) -> bool:
    return cell is None or (isinstance(cell, str) and not cell.strip())


def _tokens(cell) -> list:
    """The items of a cell that lists links or roles: separated by semicolons or line breaks."""
    if _blank(cell):
        return []
    text = cell if isinstance(cell, str) else _example(cell)        # 12.0 -> "12", a date -> "2026-11-25"
    return [token for token in (t.strip() for t in re.split(r"[;\r\n]+", text)) if token]


def _header(cell, index: int) -> str:
    text = "" if cell is None else " ".join(str(cell).split())
    return text or f"(column {_letter(index)})"


class Book:
    """The sheets of an uploaded workbook, or the one table of a CSV file."""

    def __init__(self, filename: str, data: bytes):
        self.filename = filename or "import"
        ext = os.path.splitext(self.filename.lower())[1]
        if data[:4] == b"\xd0\xcf\x11\xe0":
            raise AtlasError(400, "This is an Excel 97-2003 file (.xls). Save it as .xlsx and import that.")
        self.workbook = None
        if data[:4] == b"PK\x03\x04" or ext in (".xlsx", ".xlsm"):        # a zip file, as workbooks are
            try:
                self.workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            except Exception:                              # zip, XML or openpyxl errors
                raise AtlasError(400, f"'{self.filename}' can't be read as an Excel workbook (.xlsx).") from None
            self.names = [ws.title for ws in self.workbook.worksheets]     # not chart sheets
            if not self.names:
                self.workbook.close()
                raise AtlasError(400, f"'{self.filename}' has no sheet with cells in it.")
        elif ext in (".csv", ".txt", ".tsv"):
            self.csv = _csv_rows(data)
            self.names = [os.path.splitext(os.path.basename(self.filename))[0] or "CSV"]
        else:
            raise AtlasError(400, "Import an Excel workbook (.xlsx) or a CSV file.")

    def _rows(self, name: str):
        if self.workbook is None:
            return iter(self.csv)
        ws = self.workbook[name]
        ws.reset_dimensions()                              # some tools write wrong sizes
        return ws.iter_rows(values_only=True)

    def headers(self, name: str) -> list:
        for cells in self._rows(name):
            if any(not _blank(c) for c in cells):
                cells = list(cells)
                while cells and _blank(cells[-1]):
                    cells.pop()
                return [_header(c, i) for i, c in enumerate(cells)]
        return []

    def sheet(self, name: str) -> Sheet:
        if name not in self.names:
            raise AtlasError(400, f"The file has no sheet '{name}'. Sheets: {', '.join(self.names)}.")
        return _table(name, self._rows(name), strict=self.workbook is None)

    def close(self) -> None:
        if self.workbook is not None:
            self.workbook.close()


def _csv_rows(data: bytes) -> list:
    """CSV text in UTF-8 (as Atlas exports it), UTF-16 (Excel's "Unicode text") or the
    Turkish Windows code page (as Excel saves CSV there); the separator is whichever of
    , ; or tab the header uses most outside quotes."""
    utf16 = data[:2] in (b"\xff\xfe", b"\xfe\xff")            # its byte order mark
    for encoding in ("utf-16",) if utf16 else ("utf-8-sig", "cp1254"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("latin-1")
    first = next((line for line in text.splitlines() if line.strip()), "")
    first = re.sub(r'"[^"]*"', "", first)                      # "Stress, MPa" holds no separator
    delimiter = max(",;\t", key=first.count) if first else ","
    csv.field_size_limit(max(csv.field_size_limit(), len(text)))   # a long list or matrix is one cell
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as exc:
        raise AtlasError(400, f"The CSV file can't be read ({exc}).") from None


# --------------------------------------------------------------------------- #
# Columns and fields
# --------------------------------------------------------------------------- #
def fields(cls: AtlasClass) -> dict:
    """What a column can go into: field key -> label, in the order the class has them,
    then the object's roles. ID, Version and Roles are named as an export names them:
    "ID (Atlas)" when an attribute is called ID."""
    out = {"_id": system_header(cls, "_id"), "_version": system_header(cls, "_version")}
    for attr in cls.attributes:
        if attr.stored and attr.type not in FILE_TYPES:
            out[attr.key] = attr.label
    for rel in cls.relationships:
        out[rel.key] = rel.label
    out["_roles"] = system_header(cls, "_roles")
    return out


def guess(cls: AtlasClass, headers: list) -> list:
    """The field each column goes into ('' to skip it), by label or key."""
    names = {"_id": "_id", "#": "_id", "_version": "_version"}
    for key, label in fields(cls).items():
        names.setdefault(_norm(label), key)
        names.setdefault(key, key)
    out, used = [], set()
    for header in headers:
        key = names.get(_norm(header), "")
        if key in used:                                   # the first column of that name wins
            key = ""
        used.add(key)
        out.append(key)
    return out


def _note(cls: AtlasClass, header: str) -> str:
    """Why a column that matches no field is skipped, when it is something Atlas knows."""
    name = _norm(header)
    if name in SYSTEM_HEADERS or name.removesuffix(" (atlas)") in SYSTEM_HEADERS:
        return "Atlas fills this in"
    for attr in cls.attributes:
        if name in (_norm(attr.label), attr.key):
            if not attr.stored:
                return "A look-through: it comes from the links"
            if attr.type in FILE_TYPES:
                return f"{TYPE_NAMES[attr.type].capitalize()}s are uploaded in the form"
    if name.endswith("(count)"):
        return "A count of links: Atlas works it out"
    return ""


def _check_mapping(cls: AtlasClass, headers: list, mapping) -> list:
    if mapping is None:
        return guess(cls, headers)
    if not isinstance(mapping, list) or len(mapping) != len(headers) or not all(isinstance(m, str) for m in mapping):
        raise AtlasError(400, f"mapping must be a list of {len(headers)} field keys ('' skips a column).")
    allowed, seen = fields(cls), {}
    for index, key in enumerate(mapping):
        if not key:
            continue
        if key not in allowed:
            raise AtlasError(400, f"Column {_letter(index)}: '{key}' isn't a field of {cls.label} that can be imported.")
        if key in seen:
            raise AtlasError(400, f"Columns {seen[key]} and {_letter(index)} both go into {allowed[key]}.")
        seen[key] = _letter(index)
    return mapping


def _example(cell) -> str:
    if isinstance(cell, dt.datetime):
        return f"{cell:%Y-%m-%d %H:%M}" if cell.time() != dt.time() else f"{cell:%Y-%m-%d}"
    if isinstance(cell, dt.date):
        return cell.isoformat()
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    text = " ".join(str(cell).split())
    return text if len(text) <= 60 else text[:59] + "…"


# --------------------------------------------------------------------------- #
# Running it
# --------------------------------------------------------------------------- #
class Importer:
    def __init__(self, conn, schema: Schema, cls: AtlasClass, viewer: Viewer, mapping: list, match,
                 role_names: dict):
        self.conn, self.schema, self.cls, self.viewer = conn, schema, cls, viewer
        self.labels = fields(cls)
        self.columns = [(index, key) for index, key in enumerate(mapping) if key]
        self.match = match
        self.role_names = role_names                      # portal role id -> name
        self.role_by_name, self.role_by_folded = {}, {}   # name -> id; name as compared -> [ids]
        for role_id, name in role_names.items():
            self.role_by_name[name] = role_id
            self.role_by_folded.setdefault(_norm(name), []).append(role_id)
        self.titles = {}            # class key -> {title as compared: [ids]}
        self.title_sql = {}         # class key -> the SQL of its objects' titles
        self.title_of = {}          # id -> title as compared, for the class being imported
        self.problems = {}          # message -> row numbers
        self.failed = 0
        self.seen_ids, self.seen_values, self.matched = {}, {}, {}

    def fail(self, number: int, message: str) -> None:
        self.failed += 1
        rows = self.problems.get(message)
        if rows is None:
            if len(self.problems) >= ERROR_MESSAGES_MAX:
                return
            rows = self.problems[message] = []
        rows.append(number)

    def run(self, sheet: Sheet, dry_run: bool) -> dict:
        created = updated = unchanged = 0
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            raise AtlasError(409, "Atlas is busy saving something else. Try again in a moment.") from None
        keep = False
        try:
            for number, cells in sheet.rows:
                try:
                    if number in sheet.wide:
                        raise AtlasError(400, WIDE_ROW)
                    target, payload = self.row(number, cells)
                    if target is None:
                        self.retitle(create_object(self.conn, self.schema, self.cls, self.viewer, payload,
                                                   set(self.role_names)))
                        created += 1
                    elif update_object(self.conn, self.schema, self.cls, self.viewer, target, payload,
                                       set(self.role_names)):
                        self.retitle(target)
                        updated += 1
                    else:
                        unchanged += 1
                except AtlasError as exc:
                    self.fail(number, exc.detail)
            keep = not dry_run and not self.failed
        finally:
            self.conn.execute("COMMIT" if keep else "ROLLBACK")
        problems = sorted(self.problems.items(), key=lambda item: item[1][0])
        return {"rows": len(sheet.rows), "created": created, "updated": updated, "unchanged": unchanged,
                "failed": self.failed, "imported": keep,
                "errors": [{"message": m, "count": len(rows), "rows": rows[:ROWS_PER_ERROR]} for m, rows in problems]}

    def row(self, number: int, cells: list):
        """(the id of the object the row changes, or None for a new one; the API payload)."""
        target, payload, kept = None, {}, []
        for index, key in self.columns:
            cell = cells[index]
            if isinstance(cell, str):
                if cell[:1] == "'" and cell[1:2] in FORMULA_START:     # a CSV export's guard against formulas
                    cell = cell[1:]
                if cell.strip() in EXCEL_ERRORS:
                    raise AtlasError(400, f"{self.labels[key]}: the cell shows the Excel error {cell.strip()}. "
                                          f"Fix its formula, or type the value in.")
            attr = self.cls.attr(key)
            if key == "_id":
                target = self.whole(cell, "ID")
            elif key == "_version":
                version = self.whole(cell, "Version")
                if version is not None:
                    payload["_version"] = version
            elif key == "_roles":
                payload["_roles"] = self.roles(cell)
            elif attr is not None and attr.type in SERIES_TYPES and isinstance(cell, str) and _TOO_LONG.match(cell.strip()):
                kept.append(attr.label)                  # an export's note: the value stays as it is
            elif attr is not None:
                payload[key] = self.cell(attr, cell)
            else:
                rel = self.cls.rel(key)
                ids = self.links(rel, cell)
                payload[key] = ids if rel.cardinality == "many" or len(ids) > 1 else (ids[0] if ids else None)
        if target is not None:
            if target in self.seen_ids:
                raise AtlasError(400, f"ID {target} is on row {self.seen_ids[target]} too.")
            if target in self.matched:
                raise AtlasError(400, f"Row {self.matched[target]} changes the same object (#{target}).")
            self.seen_ids[target] = number
        elif self.match is not None:
            target = self.matching(number, payload.get(self.match.key))
            if target is not None:
                if target in self.seen_ids:
                    raise AtlasError(400, f"Row {self.seen_ids[target]} changes the same object (#{target}).")
                self.matched[target] = number
        if target is None:
            if kept:
                raise AtlasError(400, f"{kept[0]}: the cell holds an export's note, not the values. "
                                      f"A new object needs the values themselves.")
            payload.pop("_version", None)                 # a new object has no version to check
        return target, payload

    @staticmethod
    def whole(cell, what: str):
        if _blank(cell):
            return None
        text = str(int(cell)) if isinstance(cell, float) and cell.is_integer() else str(cell).strip().lstrip("#")
        if isinstance(cell, bool) or not text.isdigit():
            raise AtlasError(400, f"{what} must be a whole number, not '{_example(cell)}'.")
        return int(text)

    @staticmethod
    def cell(attr, cell):
        """A cell as the API value for the attribute (coerce() does the checking)."""
        if cell is None:
            return None
        if isinstance(cell, str):
            cell = cell.strip()
            if attr.type == "choice" and cell:            # "pass" picks the option "Pass"
                cell = next((o for o in attr.options if _norm(o) == _norm(cell)), cell)
            return cell or None
        if attr.type in ("string", "text", "choice"):
            if isinstance(cell, (dt.date, dt.time)) or isinstance(cell, float):
                return _example(cell)
        if isinstance(cell, dt.time):
            return cell.isoformat()
        return cell

    def links(self, rel, cell) -> list:
        ids = []
        for token in _tokens(cell):
            if token.startswith(("… and ", "... and ")):
                raise AtlasError(400, f"{rel.label}: the cell was cut short ('{token}'), so it doesn't list every link.")
            found = _ID_TOKEN.fullmatch(token) or _TITLED_ID.search(token)
            if found:
                ids.append(int(found.group(1)))
                continue
            matches = self.title_ids(rel.target).get(_norm(token), [])
            if not matches:
                raise AtlasError(400, f"{rel.label}: no {rel.target.label} object is titled '{token}' "
                                      f"(or you can't see it). Write an ID as #12.")
            if len(matches) > 1:
                shown = ", ".join(f"#{i}" for i in matches[:5])
                raise AtlasError(400, f"{rel.label}: {len(matches)} {rel.target.label} objects are titled "
                                      f"'{token}' ({shown}). Write the one you mean as #id.")
            ids.append(matches[0])
        return ids

    def roles(self, cell) -> list:
        """Portal role ids, written as links are: names or #ids, separated by semicolons."""
        ids = []
        for token in _tokens(cell):
            names = [token]
            if self.role(token) is None and "," in token:      # "lab_a, quality", as the grid shows them
                names = [name.strip() for name in token.split(",") if name.strip()]
            for name in names:
                role_id = self.role(name)
                if role_id is None:
                    raise AtlasError(400, f"Roles: there is no role '{name}'.")
                ids.append(role_id)
        return ids

    def role(self, name: str):
        """A role's id from its name (in any case) or #id; None when there is none."""
        found = _ID_TOKEN.fullmatch(name)
        if found:
            role_id = int(found.group(1))
            return role_id if role_id in self.role_names else None
        if name in self.role_by_name:
            return self.role_by_name[name]
        matches = self.role_by_folded.get(_norm(name), [])
        if len(matches) > 1:
            raise AtlasError(400, f"Roles: '{name}' could be " + " or ".join(self.role_names[i] for i in matches)
                                  + ". Write the name exactly.")
        return matches[0] if matches else None

    def title_ids(self, target: AtlasClass) -> dict:
        """Titles of the objects of a class the viewer can read, for links written by title."""
        if target.key not in self.titles:
            read, args = row_filter(self.viewer, target, "read", "s")
            title = self.title_sql[target.key] = Query(self.schema, target, self.viewer).title(target, "s")
            names, keys = {}, {}
            for object_id, text in self.conn.execute(
                    f'SELECT s."_id", {title} FROM {q(target.table)} s WHERE {read} ORDER BY s."_id"', args):
                keys[object_id] = _norm(text)
                names.setdefault(keys[object_id], []).append(object_id)
            self.titles[target.key] = names
            if target.key == self.cls.key:
                self.title_of = keys
        return self.titles[target.key]

    def retitle(self, object_id: int) -> None:
        """After a row made or changed an object: its title as it now is, so a row below
        can link to it by title."""
        names = self.titles.get(self.cls.key)
        if names is None:                                 # not read yet: it is read as it stands then
            return
        old = self.title_of.pop(object_id, None)
        if old is not None:
            names[old].remove(object_id)
        text, = self.conn.execute(f'SELECT {self.title_sql[self.cls.key]} FROM {q(self.cls.table)} s '
                                  f'WHERE s."_id" = ?', [object_id]).fetchone()
        self.title_of[object_id] = _norm(text)
        names.setdefault(self.title_of[object_id], []).append(object_id)

    def matching(self, number: int, value):
        """The object (that the viewer can read) holding the row's value of the match attribute."""
        attr = self.match
        try:
            stored = self.conn.users.find(value) if attr.type == "user" else coerce(attr.type, value, attr.options)
        except ValueError:
            return None                                   # the create reports the bad value
        if stored is None:
            return None
        if stored in self.seen_values:
            raise AtlasError(400, f"{attr.label} '{value}' is on row {self.seen_values[stored]} too.")
        self.seen_values[stored] = number
        read, args = row_filter(self.viewer, self.cls, "read", "s")
        row = self.conn.execute(f'SELECT s."_id" FROM {q(self.cls.table)} s WHERE s.{q(attr.key)} = ? AND ({read})',
                                [stored, *args]).fetchone()
        return row[0] if row else None


def run(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, filename: str, data: bytes, *,
        sheet=None, mapping=None, match=None, dry_run: bool = True, role_names=None) -> dict:
    """Check (dry_run) or import a file; what it found and did, column by column and row by row.
    role_names: the portal's roles (id -> name), for a Roles column."""
    book = Book(filename, data)
    try:
        if sheet is None:                                 # the first sheet with a column Atlas knows
            sheet = next((n for n in book.names if any(guess(cls, book.headers(n)))), book.names[0])
        table = book.sheet(sheet)
    finally:
        book.close()
    if not table.headers:
        raise AtlasError(400, f"'{table.name}' is empty.")
    mapping = _check_mapping(cls, table.headers, mapping)
    match_attr = None
    if match:
        match_attr = cls.attr(match)
        if match_attr is None or not match_attr.stored or not match_attr.unique:
            raise AtlasError(400, f"match must be a unique attribute of {cls.label}.")
        if match not in mapping:
            raise AtlasError(400, f"To match rows by {match_attr.label}, a column has to go into it.")
    importer = Importer(conn, schema, cls, viewer, mapping, match_attr, role_names or {})
    report = importer.run(table, dry_run) if table.rows else {
        "rows": 0, "created": 0, "updated": 0, "unchanged": 0, "failed": 0, "imported": False, "errors": []}
    examples = [[] for _ in table.headers]
    for _, cells in table.rows:
        for index, cell in enumerate(cells):
            if len(examples[index]) < EXAMPLES and not _blank(cell):
                examples[index].append(_example(cell))
        if all(len(e) == EXAMPLES for e in examples):
            break
    return {
        "file": filename, "sheet": table.name, "sheets": book.names, "header_row": table.header_row,
        "columns": [{"letter": _letter(i), "header": h, "field": mapping[i],
                     "note": "" if mapping[i] else _note(cls, h), "examples": examples[i]}
                    for i, h in enumerate(table.headers)],
        "fields": fields(cls),
        "match": match or "",
        "matchable": [{"key": a.key, "label": a.label} for a in cls.attributes if a.stored and a.unique],
        "dry_run": dry_run, **report,
    }


# --------------------------------------------------------------------------- #
# The template
# --------------------------------------------------------------------------- #
GUIDE = "Read me first"
YELLOW = PatternFill(fill_type="solid", fgColor="FFF2CC")       # the columns every new object needs
RULE = Border(bottom=Side(style="thin", color="999999"))
TOP = Alignment(wrap_text=True, vertical="top")
DATE_FORMATS = {"date": "yyyy-mm-dd", "datetime": "yyyy-mm-dd hh:mm"}
WIDTHS = {"text": 40, "matrix": 30, "list": 24, "link": 30, "roles": 20, "user": 22, "datetime": 17, "date": 12}


def _cut(text: str, size: int) -> str:
    return text if len(text) <= size else text[:size - 1] + "…"


def _listable(choices) -> bool:
    """Whether Excel can offer the choices as a drop-down written into the rule itself."""
    return bool(choices) and sum(len(c) + 1 for c in choices) <= 250 and not any("," in c or '"' in c for c in choices)


def _examples(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, username: str, role_names: dict) -> dict:
    """field key -> an example for the template, taken from what Atlas holds where it can, so it
    looks like the real thing. A relationship's is None when there is nothing to link to yet."""
    today = dt.date.today().isoformat()
    read, args = row_filter(viewer, cls, "read", "s")
    out = {}
    for attr in cls.attributes:
        if not attr.stored or attr.type in FILE_TYPES:
            continue
        held = None
        if attr.type in ("string", "text", "int", "float", "date", "datetime", "choice"):
            column = f"s.{q(attr.key)}"
            row = conn.execute(f"SELECT {column} FROM {q(cls.table)} s WHERE {column} IS NOT NULL AND {column} != '' "
                               f'AND ({read}) ORDER BY s."_id" DESC LIMIT 1', args).fetchone()
            held = row[0] if row else None
        if attr.type in ("string", "text"):
            lines = str(held or "").strip().splitlines()
            out[attr.key] = _cut(lines[0], 40) if lines else None
        elif attr.type in ("int", "float"):
            out[attr.key] = _example(held) if held is not None else "12" if attr.type == "int" else "1,5"
        elif attr.type == "date":
            out[attr.key] = str(held) if held else today
        elif attr.type == "datetime":
            out[attr.key] = str(held).replace("T", " ")[:16] if held else f"{today} 09:30"
        elif attr.type == "choice":
            options = attr.options or []
            out[attr.key] = held if held in options else (options[0] if options else None)
        elif attr.type == "boolean":
            out[attr.key] = "Yes"
        elif attr.type == "user":
            out[attr.key] = username or None
        elif attr.type == "list":
            out[attr.key] = "210; 212,5; 208"
        elif attr.type == "matrix":
            width = max(len(attr.columns), 1)
            out[attr.key] = " | ".join("; ".join(str(r * width + n) for n in range(1, width + 1)) for r in range(2))
    for rel in cls.relationships:
        out[rel.key] = None
        if rel.target is not None:
            read, args = row_filter(viewer, rel.target, "read", "s")
            title = Query(schema, rel.target, viewer).title(rel.target, "s")
            row = conn.execute(f'SELECT {title} FROM {q(rel.target.table)} s WHERE {read} '
                               f'ORDER BY s."_id" DESC LIMIT 1', args).fetchone()
            out[rel.key] = row[0] if row else None
    if role_names:
        out["_roles"] = sorted(role_names.values(), key=fold)[0]
    return out


def _guide(cls: AtlasClass, key: str, role_names: dict, example) -> tuple:
    """(what to write in a column, in a sentence or two, for the guide sheet; the same in
    short, for the note Excel shows on its cells)."""
    if key == "_roles":
        always = ", ".join(role_names.get(role_id, f"#{role_id}") for role_id in cls.roles)
        if always:
            what = (f"Usually empty: then the class's roles ({always}) can work with the object. "
                    f"To let more roles in, write their names, separated by semicolons.")
            short = "Usually empty. Role names, separated by semicolons, to let more roles in."
        else:
            what = ("Usually empty: then everyone can work with the object. To let only some roles see and "
                    "change it, write their names, separated by semicolons.")
            short = "Usually empty. Role names, separated by semicolons, to let only those roles in."
        if role_names:
            what += " Roles: " + "; ".join(sorted(role_names.values(), key=fold)) + "."
        return what, short
    attr = cls.attr(key)
    if attr is not None:
        options = ", ".join(attr.options or [])
        listed = _listable(attr.options)
        what, short = {
            "string": ("Any text.", "Any text."),
            "text": ("Any text. It can run over several lines: press Alt+Enter for a new line in the cell.",
                     "Any text. Alt+Enter starts a new line in the cell."),
            "int": ("A whole number.", "A whole number."),
            "float": ("A number. A comma or a point both work for the decimals: 1,5 or 1.5.",
                      "A number. 1,5 and 1.5 both work."),
            "boolean": ("Yes or No. Pick it from the list that opens at the right of the cell.",
                        "Pick Yes or No from the list."),
            "date": ("A date. If Excel doesn't take it, write it year-month-day, as 2026-11-25.", "A date."),
            "datetime": ("A date and a time, as 2026-11-25 14:30.", "A date and a time."),
            "choice": ((f"One of these, picked from the list in the cell: {options}.", "Pick one from the list.")
                       if listed else (f"One of these, written exactly as here: {options}.", f"One of: {options}.")),
            "user": ("A person: the username they sign in with, or their full name if nobody else has the same one.",
                     "A person's username, or their full name."),
            "list": ("Numbers, separated by semicolons.", "Numbers, separated by semicolons."),
        }.get(attr.type, ("", ""))
        if attr.type == "matrix":
            order = "; ".join(c["label"] for c in attr.columns)
            what = (f"A table of numbers: each row as {order}, separated by semicolons, and | between the rows. "
                    f"Long tables are easier to paste into the object's form.")
            short = f"Rows separated by |, each row as {order}, separated by semicolons."
        if attr.unique:
            what += " Every object needs a different one."
            short += " Every object needs a different one."
        return what, short
    rel = cls.rel(key)
    target = rel.target.label if rel.target else rel.target_key
    if rel.cardinality == "one":
        what = (f"The title of one {target} object, as Atlas shows it. If two {target} objects have that title, "
                f"write the ID instead, as #12.")
        short = f"The title of a {target} object, or its ID as #12."
    else:
        what = (f"Titles of {target} objects as Atlas shows them, separated by semicolons. If two have the same "
                f"title, write the ID instead, as #12.")
        short = f"Titles of {target} objects, separated by semicolons, or IDs as #12."
    if rel.target is not None and rel.target.key == cls.key:
        what += " It can be an object from a row above in this file."
    elif example is None:
        what += f" There are no {target} objects yet: add those first."
    return what, short


def template(conn, schema: Schema, cls: AtlasClass, viewer: Viewer, *, role_names=None, username: str = "",
             max_mb=None) -> tuple:
    """(xlsx bytes, file name) of a file to add objects with. Its first sheet says step by
    step how to fill it in and import it, and what goes in each column, with an example
    from Atlas. The second is the one to fill in: row 1 names the columns (yellow: every
    new object needs it), and each column is set up for its values. Text stays as typed
    (007, 1-2), choices and yes/no are drop-downs, dates must be dates, and a cell shows
    a note on what goes in it. role_names: the portal's roles (id -> name)."""
    role_names = role_names or {}
    labels = fields(cls)
    keys = [key for key in labels if key not in ("_id", "_version")]      # new objects have neither
    examples = _examples(conn, schema, cls, viewer, username, role_names)
    book = Workbook()
    guide = book.active
    guide.title = GUIDE
    ws = book.create_sheet(_sheet_title(cls.label))
    bold = Font(bold=True)

    table = []                                       # (column, must fill in, what to write, example)
    for n, key in enumerate(keys, start=1):
        letter, label, attr = get_column_letter(n), labels[key], cls.attr(key)
        member = attr or cls.rel(key)
        required = bool(member is not None and member.required)
        kind = attr.type if attr is not None else "roles" if key == "_roles" else "link"
        example = examples.get(key)
        what, short = _guide(cls, key, role_names, example)
        table.append((label, "Yes" if required else "No", what, "" if example is None else str(example)))

        cell = ws.cell(row=1, column=n, value=label)
        cell.font = bold
        if required:
            cell.fill = YELLOW
        column = ws.column_dimensions[letter]
        column.width = min(max(len(label) + 4, WIDTHS.get(kind, 16)), 50)
        column.number_format = DATE_FORMATS.get(kind, "@")          # Text: Excel keeps 007 and 1-2 as typed
        note = ("Must be filled in. " if required else "") + short + ("" if example is None else f" Example: {example}")
        rule = {"allow_blank": True, "showInputMessage": True, "promptTitle": _cut(label, 32), "prompt": _cut(note, 255)}
        choices = attr.options if kind == "choice" else ["Yes", "No"] if kind == "boolean" else None
        if _listable(choices):
            rule.update(type="list", formula1='"' + ",".join(choices) + '"', showErrorMessage=True,
                        errorTitle="Not in the list",
                        error="Pick a value from the list: click the cell, then the arrow at its right.")
        elif kind in DATE_FORMATS:
            rule.update(type="date", operator="between", formula1="1", formula2="2958465", showErrorMessage=True,
                        errorTitle="Not a date",
                        error="Excel doesn't see this as a date. Write it year-month-day, as 2026-11-25.")
        validation = DataValidation(**rule)
        validation.add(f"{letter}2:{letter}{MAX_ROWS + 1}")
        ws.add_data_validation(validation)
    ws.freeze_panes = "A2"

    sheet, title, heading = ws.title, Font(bold=True, size=14), Font(bold=True)

    def line(text: str = "", font=None) -> None:
        guide.append([text] if text else [])
        if text and font is not None:
            guide.cell(row=guide.max_row, column=1).font = font

    line("How to fill in this file", title)
    line(f"It adds objects to {cls.label} in Atlas. Each row you fill in on the sheet '{sheet}' becomes one object.")
    line()
    line("Step by step", heading)
    for text in (f"1. Click the tab '{sheet}' at the bottom of the window. That is the sheet you fill in.",
                 "2. Leave row 1 as it is: Atlas finds each column by its name. Delete columns you don't need, "
                 "but keep the yellow ones.",
                 "3. Write one object per row, starting in row 2. Click a cell to see a note on what goes in it.",
                 "4. Fill in every column with a yellow name. The other columns can stay empty.",
                 "5. Save the file, as an Excel workbook (.xlsx).",
                 f"6. In Atlas, open {cls.label}, click Import and drop this file into the window that opens.",
                 "7. Atlas checks every row and lists any problems with their row numbers. Nothing is saved yet.",
                 "    Fix those rows here, save, and choose the file again.",
                 "8. When Atlas says every row is fine, click Import. All the rows go in together, or none of them do."):
        line(text)
    line()
    line("What goes in each column", heading)
    guide.append(["Column", "Must fill in?", "What to write", "Example"])
    for cell in guide[guide.max_row]:
        cell.font, cell.border = bold, RULE
    for row in table:
        guide.append(list(row))
        for cell in guide[guide.max_row]:
            cell.alignment = TOP
    line()
    line("Not in this file", heading)
    uploads = [a.label for a in cls.attributes if a.type in FILE_TYPES]
    looks = [a.label for a in cls.attributes if not a.stored]
    if uploads:
        line(f"{', '.join(uploads)}: add these in each object's form in Atlas, after the import.")
    if looks:
        line(f"{', '.join(looks)}: Atlas fills these in from the links.")
    line("ID, Created and Updated (and who did them): Atlas fills these in.")
    line()
    line("Good to know", heading)
    for text in ("Several items in one cell: separate them with a semicolon (A; B), or put each on its own line "
                 "with Alt+Enter.",
                 "The columns are set up so Excel keeps what you type: 007 stays 007, and 1-2 or 1.5 don't turn "
                 "into dates.",
                 "Numbers in the Text columns get a small green triangle in Excel. That's fine.",
                 "Copying in from another file? Use Paste Special › Values: a normal paste removes the lists "
                 "and notes from the cells.",
                 "Hidden and filtered-out rows are imported too. Don't merge cells, and don't add a title row "
                 "above row 1.",
                 f"A file can hold up to {MAX_ROWS:,} rows" + (f" and {max_mb} MB." if max_mb else ".")):
        line(text)
    line()
    line(f"To change {cls.label} objects that are already in Atlas", heading)
    line("Export the table from Atlas (Export › Excel), change the cells, and import that file. "
         "Its ID column tells Atlas which object each row is.")
    line("An empty cell empties its field. To leave a column as it is, delete the whole column before you import.")
    unique = [a.label for a in cls.attributes if a.stored and a.unique]
    if unique:
        line(f"This file can change objects too: in the import window, choose to change the object with the same "
             f"{' or '.join(unique)}.")
    for column, width in zip("ABCD", (30, 14, 70, 28)):
        guide.column_dimensions[column].width = width
    guide.sheet_view.showGridLines = False

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue(), f"{cls.label} import template.xlsx"
