# -*- coding: utf-8 -*-
"""
Add a DIMENSION column to the metal kalem table, using one txt list per project.

The table (a pandas DataFrame) has the columns MATERIAL SPEC, MATERIAL TYPE,
START CONDITION, DIMENSION CODE, ÜRETİCİ, SHEET NAME and EFFECTIVITY (a list of
project names), but no sizes. Every project has one item list exported as a txt
file; each non-empty line is one kalem, a long string that holds its size (and
sometimes some gibberish). Lines of other item kinds simply match no row.

What `add_dimensions` does:
1. reads every txt file and tags its lines with that file's project name,
2. takes the size out of each line (see DIMENSION RULE below),
3. matches each line to the table rows whose MATCH_COLUMNS values all appear in
   the rest of the line (whole values; case, and spaces or punctuation between
   the parts of a value, don't matter: "AMS4461" also matches "ams 4461"),
4. rebuilds the table with one row per original row and size. EFFECTIVITY becomes
   the projects whose lists hold that kalem in that size.

It returns a Report with three tables:
- table: the rebuilt table with the extra DIMENSION column. Rows that no line
  matched are kept as they were, with an empty DIMENSION.
- lines_to_check: lines that matched no row, matched several rows equally well,
  had no size, or had more than one size text. Check these by eye.
- rows_without_lines: the original rows that no line matched.

As a script (edit CONFIG first):  python add_dimensions.py
From code:
    report = add_dimensions(df, {"a.txt": "Project1", "b.txt": "Project2"}, "project_lists")
    save_report(report, "out.xlsx")
"""
from __future__ import annotations

import os
import re
from collections import defaultdict, namedtuple
from functools import lru_cache

import pandas as pd

# =========================================================================== #
# CONFIG (used when the file runs as a script)
# =========================================================================== #
INPUT_XLSX = "some_file.xlsx"                     # the metal kalem table
OUTPUT_XLSX = "some_file_with_dimensions.xlsx"    # rebuilt table + review sheets
LISTS_DIR = "project_lists"                       # folder that holds the txt files
PROJECT_FILES = {                                 # txt file -> project name
    "project1.txt": "Project1",
    "project2.txt": "Project2",
    "project3.txt": "Project3",
    "project4.txt": "Project4",
    "project5.txt": "Project5",
}

# Table columns whose values must all appear in a line for the line to belong to
# that row. The lines don't name the manufacturer, so ÜRETİCİ is not one of them.
MATCH_COLUMNS = ["MATERIAL SPEC", "MATERIAL TYPE", "START CONDITION", "DIMENSION CODE"]
EFFECTIVITY_COL = "EFFECTIVITY"
DIMENSION_COL = "DIMENSION"
AFTER_COL = "DIMENSION CODE"                      # DIMENSION is placed after this column
ENCODINGS = ("utf-8-sig", "cp1254")               # tried in order; cp1254 = Turkish Windows


# =========================================================================== #
# DIMENSION RULE: the place to change if the size format in the lists changes
# =========================================================================== #
# A size is the text inside parentheses that holds at least one digit:
#     "AMS4461 SHEET METALLIC-O-2024-CLAD (a=3.2mm h=4mm) %&x"  ->  "a=3.2mm h=4mm"
# Parentheses without a digit, such as "(CLAD)", are not a size and stay part of
# the line. A line with more than one size text gets them joined with a space,
# and is listed for a check.
DIMENSION_PATTERN = re.compile(r"\(([^()]*\d[^()]*)\)")


def extract_dimension(line: str) -> list[str]:
    """Every size text written in the line, spaces tidied ([] when there is none)."""
    return [" ".join(found.split()) for found in DIMENSION_PATTERN.findall(line)]


def remove_dimension(line: str) -> str:
    """The line without its size text, so a size never matches a table value."""
    return DIMENSION_PATTERN.sub(" ", line)


def dimension_key(dimension: str) -> str:
    """Sizes with the same key are the same kalem. Case and spaces don't count,
    so "a=3.2 mm" in one list and "A=3.2MM" in another share one row."""
    return re.sub(r"\s+", "", dimension).upper()


# =========================================================================== #
# Reading the project lists
# =========================================================================== #
def _decode(raw: bytes, encodings) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):     # UTF-16 with a byte-order mark
        return raw.decode("utf-16")
    for enc in encodings:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")                  # never fails; odd bytes stay visible


def read_project_lists(project_files: dict, folder: str = ".",
                       encodings=ENCODINGS) -> pd.DataFrame:
    """Every non-empty line of every file, with its PROJECT, FILE, LINE NO, TEXT,
    the DIMENSION found in it and how many size texts it had (SIZE TEXTS)."""
    rows = []
    for name, project in project_files.items():
        with open(os.path.join(folder, name), "rb") as fh:
            text = _decode(fh.read(), encodings)
        for no, line in enumerate(re.split(r"\r\n|\r|\n", text), start=1):
            if line.strip():
                rows.append((project, name, no, line.strip()))
    lines = pd.DataFrame(rows, columns=["PROJECT", "FILE", "LINE NO", "TEXT"])
    sizes = lines["TEXT"].map(extract_dimension)
    lines["DIMENSION"] = sizes.map(" ".join)
    lines["SIZE TEXTS"] = sizes.map(len)
    return lines


# =========================================================================== #
# Matching lines to table rows
# =========================================================================== #
_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "IIISSGGUUOOCC")
_PART = re.compile(r"[^\W\d_]+|\d+")              # a run of letters or a run of digits


def _text(value) -> str:
    """A cell as text: "" when empty, 6061.0 -> "6061"."""
    if not isinstance(value, str) and pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _fold(text: str) -> str:
    return text.translate(_FOLD).upper()


def _parts(value) -> tuple:
    """'2024-CLAD' -> ('2024', 'CLAD'); 'AMS4461' -> ('AMS', '4461');
    'Ti-6Al-4V' and 'TI6AL4V' both -> ('TI', '6', 'AL', '4', 'V')."""
    return tuple(_PART.findall(_fold(_text(value))))


def _pattern(parts: tuple) -> re.Pattern:
    """A table value inside a line: its parts in order with anything but letters
    and digits between them, and not glued to more letters (or more digits) at
    either end. So 'AMS4461' matches 'AMS 4461' and 'AMS-4461' but not
    'AMS44615', and the start condition 'O' matches '-O-' but not 'OX'."""
    def edge(char):
        return r"\d" if char.isdigit() else r"[^\W\d_]"
    body = r"[\W_]*".join(map(re.escape, parts))
    return re.compile(rf"(?<!{edge(parts[0][0])}){body}(?!{edge(parts[-1][-1])})")


def _finder(texts: list[str]):
    """lines_having(parts) -> ids of the lines that contain that value (cached)."""
    folded = [_fold(remove_dimension(t)) for t in texts]

    @lru_cache(maxsize=None)
    def lines_having(parts: tuple) -> frozenset:
        pattern, probe = _pattern(parts), max(parts, key=len)
        return frozenset(i for i, t in enumerate(folded) if probe in t and pattern.search(t))

    return lines_having


def _inside(small: tuple, big: tuple) -> bool:
    n = len(small)
    return any(big[i:i + n] == small for i in range(len(big) - n + 1))


def _refines(key: tuple, other: tuple) -> bool:
    """`key` is a more detailed version of `other`: each value of `other` sits in
    the same column's value of `key` ('2024' in '2024-CLAD', empty in anything)."""
    return key != other and all(_inside(o, k) for o, k in zip(other, key))


def _owners(rows: list, keys: list) -> tuple[list, bool]:
    """The rows a line belongs to, and whether that is clear. A row drops out when
    another matched row refines it; the rest must have the same values (e.g. one
    material listed with two manufacturers), otherwise the line is ambiguous."""
    best = [r for r in rows if not any(_refines(keys[s], keys[r]) for s in rows)]
    return best, len({keys[r] for r in best}) == 1


# =========================================================================== #
# Rebuilding the table
# =========================================================================== #
Report = namedtuple("Report", "table lines_to_check rows_without_lines")


def _as_list(value) -> list:
    """EFFECTIVITY as a list. A list stays a list; text such as "P1; P2" or
    "['P1', 'P2']" is split."""
    if isinstance(value, (list, tuple, set)):
        return list(value)
    text = _text(value).strip().strip("[]")
    return [p.strip(" '\"") for p in re.split(r"[;,]", text) if p.strip(" '\"")]


def _columns(df: pd.DataFrame) -> list:
    cols = [c for c in df.columns if c != DIMENSION_COL]
    at = cols.index(AFTER_COL) + 1 if AFTER_COL in cols else len(cols)
    return cols[:at] + [DIMENSION_COL] + cols[at:]


def add_dimensions(df: pd.DataFrame, project_files: dict = PROJECT_FILES,
                   folder: str = LISTS_DIR, match_columns=MATCH_COLUMNS) -> Report:
    missing = [c for c in [*match_columns, EFFECTIVITY_COL] if c not in df.columns]
    if missing:
        raise KeyError(f"Columns not in the table: {missing}")
    lines = read_project_lists(project_files, folder)
    order = list(dict.fromkeys(project_files.values()))     # projects in config order
    keys = [tuple(_parts(v) for v in row)                   # by row position
            for row in df[match_columns].itertuples(index=False)]
    lines_having = _finder(lines["TEXT"].tolist())

    # 1. a line is a candidate for every row whose non-empty values it all contains
    candidates = defaultdict(list)                  # line id -> row positions
    for pos, key in enumerate(keys):
        needed = [p for p in key if p]
        if needed:                                  # a row without values matches nothing
            for i in frozenset.intersection(*map(lines_having, needed)):
                candidates[i].append(pos)

    # 2. settle each line's rows and note what needs a look
    owned = defaultdict(list)                       # row position -> line ids
    check = []                                      # (line id, reason, row labels)

    def labels(positions):
        return ", ".join(str(df.index[p]) for p in positions)

    for i in range(len(lines)):
        if not candidates[i]:
            check.append((i, "no matching row", ""))
            continue
        rows, clear = _owners(candidates[i], keys)
        if not clear:
            check.append((i, "matches several rows", labels(rows)))
            continue
        for pos in rows:
            owned[pos].append(i)
        if lines.at[i, "SIZE TEXTS"] == 0:
            check.append((i, "no dimension found", labels(rows)))
        elif lines.at[i, "SIZE TEXTS"] > 1:
            check.append((i, "several dimension texts, joined", labels(rows)))

    # 3. one table row per original row and size
    records, index = [], []
    for pos, (label, row) in enumerate(df.iterrows()):
        rec = row.to_dict()
        if pos not in owned:                        # no line: kept as it was
            records.append({**rec, DIMENSION_COL: "",
                            EFFECTIVITY_COL: _as_list(rec[EFFECTIVITY_COL])})
            index.append(label)
            continue
        sizes = {}                                  # dimension key -> (shown text, projects)
        for i in owned[pos]:
            dim = lines.at[i, "DIMENSION"]
            sizes.setdefault(dimension_key(dim), (dim, set()))[1].add(lines.at[i, "PROJECT"])
        for dim, projects in sizes.values():
            records.append({**rec, DIMENSION_COL: dim,
                            EFFECTIVITY_COL: [p for p in order if p in projects]})
            index.append(label)

    table = pd.DataFrame(records, index=pd.Index(index, name=df.index.name),
                         columns=_columns(df))
    ids = [i for i, _, _ in check]
    lines_to_check = (lines.loc[ids, ["PROJECT", "FILE", "LINE NO", "TEXT", "DIMENSION"]]
                      .assign(REASON=[r for _, r, _ in check], ROWS=[x for _, _, x in check])
                      .reset_index(drop=True))
    rows_without_lines = df.iloc[[p for p in range(len(df)) if p not in owned]]
    return Report(table, lines_to_check, rows_without_lines)


# =========================================================================== #
# Output
# =========================================================================== #
def save_report(report: Report, path: str) -> None:
    """One workbook: the rebuilt table and the two review sheets. EFFECTIVITY
    lists are written as "P1; P2". ROW is the original table's index."""
    def flat(d):
        return d.assign(**{EFFECTIVITY_COL: d[EFFECTIVITY_COL].map(
            lambda v: "; ".join(_as_list(v)))})

    with pd.ExcelWriter(path) as xl:
        flat(report.table).to_excel(xl, sheet_name="table", index_label="ROW")
        report.lines_to_check.to_excel(xl, sheet_name="lines to check", index=False)
        flat(report.rows_without_lines).to_excel(xl, sheet_name="rows without lines",
                                                 index_label="ROW")


def summary(report: Report) -> str:
    table, check, without = report
    reasons = "".join(f"\n  {reason}: {n}" for reason, n in check["REASON"].value_counts().items())
    return (f"table: {len(table)} rows from {table.index.nunique()} original rows\n"
            f"lines to check: {len(check)}{reasons}\n"
            f"rows without lines: {len(without)}")


if __name__ == "__main__":
    result = add_dimensions(pd.read_excel(INPUT_XLSX))
    save_report(result, OUTPUT_XLSX)
    print(summary(result))
    print(f"Saved {OUTPUT_XLSX}")
