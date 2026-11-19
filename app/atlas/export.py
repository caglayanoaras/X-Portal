"""Exporting a class's objects as an Excel workbook (.xlsx) or a CSV file.

An export is the grid as it stands: the same filters, search and sort, and the
columns asked for (fields=..., in that order). A linked object is written as
"title (#id)", so a reader can find it in that class's own export, whose ID
column holds the same number. incoming_counts adds, per relationship of another
class pointing here, how many objects link in.

Excel files hold real numbers, dates and yes/no values; Created and Updated are
in the time zone asked for (the browser's). CSV files are for scripts and other
tools: UTF-8 with a byte order mark, commas, ISO dates and true/false.
"""
import csv
import datetime as dt
import io
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.atlas.errors import AtlasError
from app.atlas.objects import count_objects, iter_objects
from app.atlas.schema import AtlasClass, Schema

FORMATS = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
           "csv": "text/csv; charset=utf-8"}
MAX_ROWS = 100_000
CELL_MAX = 32_000                 # Excel holds at most 32,767 characters in a cell
SYSTEM_COLUMNS = {"_id": ("ID", "int"), "_created_at": ("Created", "stamp"), "_created_by": ("Created by", "text"),
                  "_updated_at": ("Updated", "stamp"), "_updated_by": ("Updated by", "text"),
                  "_version": ("Version", "int"), "_roles": ("Roles", "roles")}
DEFAULT_SYSTEM = ("_created_at", "_created_by", "_updated_at", "_updated_by")
KINDS = {"int": "int", "float": "float", "boolean": "bool", "date": "date", "datetime": "datetime",
         "image": "file", "file": "file"}
WIDTHS = {"int": 10, "float": 12, "bool": 8, "date": 12, "datetime": 17, "stamp": 17, "link": 36, "file": 28,
          "count": 14, "roles": 20, "text": 24}


class Column:
    def __init__(self, key: str, header: str, kind: str):
        self.key, self.header, self.kind = key, header, kind
        self.width = min(max(len(header) + 3, WIDTHS[kind]), 60)


def columns(schema: Schema, cls: AtlasClass, fields=None, incoming_counts: bool = False) -> list:
    """The export's columns: `fields` in that order, else everything the grid can show."""
    names = [name.strip() for name in str(fields or "").split(",") if name.strip()]
    if not names:
        names = ["_id", *(a.key for a in cls.attributes), *(r.key for r in cls.relationships), *DEFAULT_SYSTEM]
    out, seen = [], set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        if name in SYSTEM_COLUMNS:
            out.append(Column(name, *SYSTEM_COLUMNS[name]))
        elif cls.attr(name) is not None:
            attr = cls.attr(name)
            final = attr if attr.stored else schema.resolve(attr)[1]
            out.append(Column(name, attr.label, KINDS.get(final.type, "text")))
        elif cls.rel(name) is not None:
            out.append(Column(name, cls.rel(name).label, "link"))
        else:
            raise AtlasError(400, f"'{name}' is not a field of {cls.label}.")
    if incoming_counts:
        for rel in schema.incoming(cls):
            if rel.show:
                label = rel.reverse_label or f"{rel.cls.label} › {rel.label}"
                out.append(Column(f"{rel.cls.key}.{rel.key}", f"{label} (count)", "count"))
    return out


def build(conn, schema: Schema, cls: AtlasClass, viewer, filters, *, fmt: str, text=None, sort=None,
          fields=None, incoming_counts: bool = False, tz=None, role_names=None) -> tuple:
    """(file bytes, media type, file name) for the objects the list would give."""
    if fmt not in FORMATS:
        raise AtlasError(404, "Export as xlsx or csv.")
    zone = _zone(tz)
    cols = columns(schema, cls, fields, incoming_counts)
    total = count_objects(conn, schema, cls, viewer, filters, text=text)
    if total > MAX_ROWS:
        raise AtlasError(400, f"{total:,} objects are more than one export holds ({MAX_ROWS:,}). "
                              f"Narrow them down with filters or the search box.")
    expand = ",".join(c.key for c in cols if c.kind == "link") or None
    chunks = iter_objects(conn, schema, cls, viewer, filters, text=text, sort=sort, expand=expand,
                          incoming_counts=any(c.kind == "count" for c in cols))
    cells = Cells(zone, role_names or {})
    body = _workbook(cls, cols, chunks, cells) if fmt == "xlsx" else _csv(cols, chunks, cells)
    name = f"{cls.label} {dt.datetime.now(zone):%Y-%m-%d %H%M}.{fmt}"
    return body, FORMATS[fmt], name


def _zone(tz):
    try:
        return ZoneInfo(tz) if tz else dt.timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return dt.timezone.utc


class Cells:
    """Turns API values into cell values (Excel) or text (CSV)."""

    def __init__(self, zone, role_names: dict):
        self.zone, self.role_names = zone, role_names

    @staticmethod
    def value(col: Column, item: dict):
        if col.kind == "count":
            return (item.get("_incoming_counts") or {}).get(col.key, 0)
        return item.get(col.key)

    def local(self, stamp: str) -> dt.datetime:
        """A system timestamp ("…Z", UTC) as wall-clock time in the export's zone."""
        moment = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        return moment.astimezone(self.zone).replace(tzinfo=None)

    @staticmethod
    def links(value) -> str:
        """Linked objects as "S-12 (#5); S-13 (#9)", cut short (with how many more) to fit a cell."""
        items = value if isinstance(value, list) else [value]
        parts, size = [], 0
        for n, item in enumerate(items):
            if isinstance(item, dict):
                part = f"#{item['_id']}" if item["_title"] == f"#{item['_id']}" else f"{item['_title']} (#{item['_id']})"
            else:
                part = f"#{item}"
            if size + len(part) > CELL_MAX - 40:
                parts.append(f"… and {len(items) - n} more")
                break
            parts.append(part)
            size += len(part) + 2
        return "; ".join(parts)

    def text(self, col: Column, value) -> str:
        """The text form of a value: what the CSV holds, and Excel cells that are text."""
        if col.kind == "link":
            return self.links(value)
        if col.kind == "file":
            return value["name"]
        if col.kind == "roles":
            return ", ".join(self.role_names.get(role, f"#{role}") for role in value)
        if isinstance(value, list):                 # a look-through through a 'many' relationship
            return "; ".join(self.text(col, v) for v in value if v is not None)
        if col.kind == "bool":
            return "true" if value else "false"
        if col.kind == "datetime":
            return str(value).replace("T", " ")
        if col.kind == "stamp":
            return f"{self.local(value):%Y-%m-%d %H:%M:%S}"
        return str(value)

    def excel(self, ws, col: Column, value):
        if value is None or value == []:
            return None
        if col.kind in ("int", "float", "count") and not isinstance(value, list):
            return value
        if col.kind == "bool" and not isinstance(value, list):
            return bool(value)
        if col.kind in ("date", "datetime", "stamp") and not isinstance(value, list):
            moment = (dt.date.fromisoformat(value) if col.kind == "date" else
                      dt.datetime.fromisoformat(value) if col.kind == "datetime" else self.local(value))
            cell = WriteOnlyCell(ws, value=moment)
            cell.number_format = "yyyy-mm-dd" if col.kind == "date" else "yyyy-mm-dd hh:mm"
            return cell
        text = self.text(col, value)[:CELL_MAX]
        if text.startswith("="):                     # text, never a formula
            cell = WriteOnlyCell(ws, value=text)
            cell.data_type = "s"
            return cell
        return text

    def csv(self, col: Column, value) -> str:
        if value is None or value == []:
            return ""
        text = self.text(col, value)
        if col.kind not in ("int", "float", "count", "bool") and text[:1] in ("=", "+", "-", "@", "\t", "\r"):
            try:
                float(text)                          # "-5" is a number, not a formula
            except ValueError:
                text = "'" + text                    # Excel would run it as a formula
        return text


def _sheet_title(label: str) -> str:
    return re.sub(r"[\[\]:*?/\\]", " ", label).strip()[:31] or "Atlas"


def _workbook(cls: AtlasClass, cols: list, chunks, cells: Cells) -> bytes:
    book = Workbook(write_only=True)
    ws = book.create_sheet(_sheet_title(cls.label))
    for n, col in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(n)].width = col.width
    ws.freeze_panes = "A2"
    bold = Font(bold=True)
    header = []
    for col in cols:
        cell = WriteOnlyCell(ws, value=col.header)
        cell.font = bold
        header.append(cell)
    ws.append(header)
    rows = 0
    for items in chunks:
        for item in items:
            ws.append([cells.excel(ws, col, cells.value(col, item)) for col in cols])
            rows += 1
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{rows + 1}"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _csv(cols: list, chunks, cells: Cells) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([col.header for col in cols])
    for items in chunks:
        for item in items:
            writer.writerow([cells.csv(col, cells.value(col, item)) for col in cols])
    return buffer.getvalue().encode("utf-8-sig")
