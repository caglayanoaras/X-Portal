"""Validating and converting attribute values.

coerce() turns what a client sends (or a value stored under an older type) into
the stored form of an attribute type. It raises ValueError with a short reason
("must be an integer"), which callers prefix with the attribute's label.

Stored forms:
    string, text, choice  str
    int                   int
    float                 float
    boolean               0 / 1
    date                  "YYYY-MM-DD"
    datetime              "YYYY-MM-DDTHH:MM:SS" (local wall-clock time)
    list                  JSON text of numbers: "[210,212.5,208]"
    matrix                JSON text of rows, one number (or null) per column:
                          "[[0,0],[0.1,210],[0.2,null]]"
"""
import datetime
import json
import math
import re

from app.atlas.db import fold

TRUE_WORDS = frozenset({"1", "true", "t", "yes", "y", "on", "evet", "var", "x"})
FALSE_WORDS = frozenset({"0", "false", "f", "no", "n", "off", "hayir", "yok"})

SERIES_MAX = 100_000            # items in a list, rows in a matrix
SERIES_VALUES_MAX = 1_000_000   # numbers in one matrix
_SAFE_INT = 2 ** 53             # integers JavaScript reads exactly


def coerce(atype: str, value, options=None, *, columns: int = 0):
    """The stored form of `value` for an attribute of type `atype`; None when blank.
    `columns` is how many columns a matrix has."""
    if atype in ("list", "matrix"):
        return _series(atype, value, columns)
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    if isinstance(value, (list, dict)):
        raise ValueError("must be a single value")

    if atype in ("string", "text"):
        return value if isinstance(value, str) else str(value)
    if atype == "int":
        if isinstance(value, bool):
            raise ValueError("must be an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.replace(" ", "")):
            return int(value.replace(" ", ""))
        number = _number(value, "must be an integer")
        if not number.is_integer():
            raise ValueError("must be an integer")
        return int(number)
    if atype == "float":
        if isinstance(value, bool):
            raise ValueError("must be a float")
        return _number(value, "must be a float")
    if atype == "boolean":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)) and value in (0, 1):
            return int(value)
        word = fold(value).strip()
        if word in TRUE_WORDS:
            return 1
        if word in FALSE_WORDS:
            return 0
        raise ValueError("must be yes or no")
    if atype == "date":
        return parse_date(value)
    if atype == "datetime":
        return parse_datetime(value)
    if atype == "choice":
        text = str(value)
        if options and text not in options:
            raise ValueError("must be one of: " + ", ".join(options))
        return text
    if atype in ("image", "file"):
        raise ValueError("can only be uploaded (PUT …/files/<attribute>)")
    if atype == "user":            # a portal user's id; writes find it with users.Users.find()
        if isinstance(value, bool):
            raise ValueError("must be a user id")
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.lstrip("#").strip().isdigit():
            return int(value.lstrip("#").strip())
        raise ValueError("must be a user id")
    raise ValueError(f"has an unknown type '{atype}'")


def _number(value, reason: str) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).replace(" ", "")
        if "," in text and "." not in text:          # "1,5" -> 1.5
            text = text.replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            raise ValueError(reason) from None
    if not math.isfinite(number):
        raise ValueError(reason)
    return number


# Lists and matrices come as JSON (from the API) or as text: what is typed, pasted from
# Excel (tabs between cells, a line per row) or written in a CSV or Excel cell. On a
# line, values are separated by tabs, semicolons or ", " (else by spaces); a comma with
# no space after it is a decimal comma ("0,5"). A list is every value on every line; a
# matrix has a row per line, or rows separated by "|". (atlas-plot.js reads them alike.)
_ROW_SEPARATORS = re.compile(r"\s*(?:[\r\n]+|\|)\s*")   # between the rows of a matrix
_CELL_SEPARATORS = re.compile(r"\t|;|,\s+")              # between the values on a line


def _series(atype: str, value, columns: int):
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("["):
            try:
                value = json.loads(text)
            except ValueError:
                raise ValueError("isn't valid JSON (write [1, 2] or 1; 2)") from None
        elif atype == "list":
            value = [part for line in text.splitlines() for part in _cells(line) if part]
        else:
            value = [_cells(row) for row in _ROW_SEPARATORS.split(text) if row.strip()]
    if value is None:
        return None
    if atype == "list":
        items = value if isinstance(value, list) else [value]
        if len(items) > SERIES_MAX:
            raise ValueError(f"has more than {SERIES_MAX:,} values")
        out = []
        for n, item in enumerate(items, start=1):
            if isinstance(item, list) and len(item) == 1:   # a one-column matrix row
                item = item[0]
            out.append(_series_number(item, f"value {n}", blank_ok=False))
    else:
        if columns < 1:
            raise ValueError("has no columns")
        rows = value if isinstance(value, list) else [value]
        if len(rows) > SERIES_MAX:
            raise ValueError(f"has more than {SERIES_MAX:,} rows")
        if len(rows) * columns > SERIES_VALUES_MAX:
            raise ValueError(f"has more than {SERIES_VALUES_MAX:,} values")
        out = []
        for n, row in enumerate(rows, start=1):
            cells = list(row) if isinstance(row, list) else [row]
            if not isinstance(row, list) and columns != 1:
                raise ValueError(f"row {n} must be a list of {columns} values")
            while len(cells) > columns and _blank(cells[-1]):   # a trailing tab from a paste
                cells.pop()
            if len(cells) > columns:
                raise ValueError(f"row {n} has {len(cells)} values but there {_are(columns)}")
            cells = [_series_number(cell, f"row {n}", blank_ok=True) for cell in cells]
            if any(cell is not None for cell in cells):     # an empty row is no row
                out.append(cells + [None] * (columns - len(cells)))
    return json.dumps(out, separators=(",", ":")) if out else None


def _cells(row: str) -> list:
    parts = _CELL_SEPARATORS.split(row) if _CELL_SEPARATORS.search(row) else row.split()
    return [part.strip() for part in parts]


def _are(columns: int) -> str:
    return "is 1 column" if columns == 1 else f"are {columns} columns"


def _blank(item) -> bool:
    return item is None or (isinstance(item, str) and not item.strip())


def _series_number(item, where: str, *, blank_ok: bool):
    """One number of a list or matrix: an int when it is whole, else a float."""
    if _blank(item):
        if blank_ok:
            return None
        raise ValueError(f"must be numbers ({where} is empty)")
    if isinstance(item, (bool, list, dict)):
        raise ValueError(f"must be numbers ({where} is {json.dumps(item)})")
    try:
        number = _number(item, "")
    except ValueError:
        raise ValueError(f"must be numbers ({where} is '{str(item).strip()[:40]}')") from None
    if isinstance(item, int) or (number.is_integer() and abs(number) < _SAFE_INT):
        return int(number) if abs(number) < _SAFE_INT else number
    return number


def parse_number(value) -> float:
    """Any number, for filters on integer and float fields alike ("1,5" reads as 1.5)."""
    if isinstance(value, bool):
        raise ValueError("must be a number")
    if isinstance(value, str):
        value = value.strip()
    if value is None or value == "":
        raise ValueError("is missing")
    return _number(value, "must be a number")


def parse_date(value) -> str:
    """'YYYY-MM-DD' from a date, an ISO date(-time) or 'DD.MM.YYYY'."""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    text = str(value).strip()
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", text)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
        if not match:
            raise ValueError("must be a date (YYYY-MM-DD)")
        day, month, year = map(int, match.groups())
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        raise ValueError("is not a real date") from None


def parse_datetime(value) -> str:
    """'YYYY-MM-DDTHH:MM:SS' (local time) from ISO text, 'DD.MM.YYYY HH:MM' or a datetime."""
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, datetime.date):
        moment = datetime.datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
        try:
            if match:
                day, month, year, hour, minute, second = match.groups()
                moment = datetime.datetime(int(year), int(month), int(day),
                                           int(hour or 0), int(minute or 0), int(second or 0))
            else:
                moment = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("must be a date and time (YYYY-MM-DDTHH:MM)") from None
    if moment.tzinfo is not None:                     # an explicit offset -> local time
        moment = moment.astimezone().replace(tzinfo=None)
    return moment.replace(microsecond=0).isoformat(timespec="seconds")


def parse_flag(value) -> bool:
    """A yes/no query parameter (isnull=true)."""
    return coerce("boolean", value) == 1


def out_value(atype: str, value):
    """The API form of a stored value."""
    if value is None:
        return None
    if atype == "boolean":
        return bool(value)
    if atype in ("list", "matrix"):
        return json.loads(value)
    return value


def role_ids(value) -> list:
    """A list of role ids from JSON (ints or numeric strings), without repeats."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("must be a list of role ids")
    out = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError("must be a list of role ids")
        try:
            role_id = int(item)
        except (TypeError, ValueError):
            raise ValueError("must be a list of role ids") from None
        if role_id not in out:
            out.append(role_id)
    return out


def object_ids(value) -> list:
    """Linked object ids from JSON: null, an id, a list of ids, or {"_id": …} objects."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("_id")
        if isinstance(item, bool) or item is None or (isinstance(item, float) and not item.is_integer()):
            raise ValueError("must be object ids")
        try:
            object_id = int(item)
        except (TypeError, ValueError):
            raise ValueError("must be object ids") from None
        if object_id not in out:
            out.append(object_id)
    return out
