# -*- coding: utf-8 -*-
"""Shared, dashboard-agnostic helpers.

Anything reused across dashboards lives here: paths, generic xlsx loading with an
mtime cache, number formatting, and the common UI atoms (brand header, KPI card,
empty figure, placeholder page). Dashboard-specific logic stays in its own
`pages/dashboardN.py`.
"""
from __future__ import annotations

import os
from datetime import datetime, time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, get_asset_url, get_relative_path

import theme as T
from app.core.config import settings

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = os.path.dirname(os.path.abspath(__file__))              # app/data_panels
DATA_DIR = os.path.join(os.path.dirname(ROOT), "static", "data")  # app/static/data


def data_path(name: str) -> str:
    return os.path.join(DATA_DIR, name)


def resolve_data(settings_key: str, default_name: str) -> str:
    """Data-file path from the portal config (app.core.config.settings).
    Relative paths resolve under app/static/data; absolute paths are used as-is."""
    p = getattr(settings, settings_key, None) or default_name
    return p if os.path.isabs(p) else os.path.join(DATA_DIR, p)


# Display name shown in footers (from the portal config).
COMPANY_NAME = settings.COMPANY_NAME

# Landing-page header + browser-tab title (from the portal config).
APP_TITLE = settings.APP_TITLE


# --------------------------------------------------------------------------- #
# Working calendar + business-hours engine (shared by all dashboards)
# --------------------------------------------------------------------------- #
WORK_START = time(8, 0)
WORK_END = time(17, 0)
WORKDAYS = {0, 1, 2, 3, 4}                  # Mon..Fri
HOLIDAYS: set = set()                       # add date objects to exclude full days
WORKDAY_HOURS = (datetime.combine(datetime.min, WORK_END)
                 - datetime.combine(datetime.min, WORK_START)).total_seconds() / 3600.0
WORKNOTE = (f"Mesai: Hafta içi {WORK_START.strftime('%H:%M')}–"
            f"{WORK_END.strftime('%H:%M')} · {WORKDAY_HOURS:.0f} sa/gün")

_WS_SEC = WORK_START.hour * 3600 + WORK_START.minute * 60
_WE_SEC = WORK_END.hour * 3600 + WORK_END.minute * 60
_WORKDAY_SEC = _WE_SEC - _WS_SEC
_EPOCH = np.datetime64("2000-01-03")        # a Monday before any data


def _cum_work_seconds(ts: pd.Series) -> np.ndarray:
    """Cumulative business-window seconds from _EPOCH to each timestamp."""
    day = ts.dt.floor("D")
    dates = day.values.astype("datetime64[D]")
    hol = np.array(sorted(HOLIDAYS), dtype="datetime64[D]") if HOLIDAYS else []
    bdays = np.busday_count(_EPOCH, dates, holidays=hol)
    is_bday = np.is_busday(dates, holidays=hol)
    tod = (ts.values - day.values) / np.timedelta64(1, "s")
    within = np.where(is_bday, np.clip(tod, _WS_SEC, _WE_SEC) - _WS_SEC, 0.0)
    return bdays.astype("float64") * _WORKDAY_SEC + within


def working_hours_vec(start: pd.Series, end: pd.Series) -> np.ndarray:
    """Vectorized business-hours between two datetime Series (O(n))."""
    return (_cum_work_seconds(end) - _cum_work_seconds(start)) / 3600.0


# --------------------------------------------------------------------------- #
# Cleaning / option helpers
# --------------------------------------------------------------------------- #
EMPTY_LABEL = "(Boş)"
_EMPTY_TOKENS = {"", "nan", "none", "nat", "<na>", "null"}


def clean_code(s: pd.Series) -> pd.Series:
    """Stringify + strip a code/label column; blanks/NaN -> (Boş)."""
    s = s.astype(str).str.strip()
    return s.mask(s.str.lower().isin(_EMPTY_TOKENS), EMPTY_LABEL)


def opts(values):
    """Dropdown options from a column, NaN-safe (sorted(set) chokes on str+NaN)."""
    vals = {(str(v) if pd.notna(v) else EMPTY_LABEL) for v in values}
    return [{"label": v, "value": v} for v in sorted(vals)]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def read_export(path: str, skiprows: int = 2) -> pd.DataFrame:
    """Read a PLM-style export sheet: skip the junk rows, take the header row,
    strip whitespace from column names. (Rows 1-2 junk, row 3 header by default.)
    """
    df = pd.read_excel(path, sheet_name=0, skiprows=skiprows, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def make_loader(path: str, build, skiprows: int = 2):
    """Return a cached `get()` that re-reads `path` only when its mtime changes.

    `build(raw_df) -> df` does the dashboard-specific cleaning/enrichment.
    """
    state = {"mtime": None, "df": None}

    def get() -> pd.DataFrame:
        try:
            mtime = os.path.getmtime(path)
        except OSError as exc:
            raise FileNotFoundError(f"Veri dosyası bulunamadı: {path}") from exc
        if state["df"] is None or state["mtime"] != mtime:
            state["df"] = build(read_export(path, skiprows=skiprows))
            state["mtime"] = mtime
        return state["df"]

    return get


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def fmt(v, dec: int = 1) -> str:
    if v is None or pd.isna(v):
        return "–"
    return f"{v:,.{dec}f}".replace(",", " ")


# --------------------------------------------------------------------------- #
# UI atoms
# --------------------------------------------------------------------------- #
def brand_header(title: str, status=None, back: bool = True) -> html.Header:
    """Top bar: logo + title, with an optional back-link and status content."""
    right = []
    if back:
        right.append(dcc.Link("← Tüm panolar", href=get_relative_path("/"), className="backlink"))
    if status is not None:
        right.append(status)
    return html.Header(className="topbar", children=[
        html.Img(src=get_asset_url("logo_horizontal.png"), className="logo"),
        html.Div(className="titles", children=[html.H1(title)]),
        html.Div(className="status", children=right),
    ])


def kpi_card(idx: str, label: str, accent: str | None = None) -> html.Div:
    """accent: None (blue, default) | 'gold' | 'red' — colours the left bar."""
    cls = "kpi" + (f" acc-{accent}" if accent else "")
    return html.Div(className=cls, children=[
        html.Div(id=f"kpi-{idx}-val", className="kpi-val", children="–"),
        html.Div(className="kpi-lbl", children=label),
    ])


def empty_fig(msg: str = "Veri yok") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(color=T.MUTED, size=14))
    fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False))
    return T.style_fig(fig, height=300)


def placeholder_layout(title: str,
                       desc: str = "Bu pano yapım aşamında. Tasarım daha sonra eklenecek.") -> html.Div:
    """Full page for a not-yet-designed dashboard."""
    return html.Div(className="app", children=[
        brand_header(title),
        html.Section(className="row", children=[
            html.Div(className="card full placeholder", children=[
                html.Img(src=get_asset_url("emblem_dark.png"), className="ph-emblem"),
                html.H2(title),
                html.P(desc),
            ]),
        ]),
        html.Footer(className="foot", children=[COMPANY_NAME]),
    ])
