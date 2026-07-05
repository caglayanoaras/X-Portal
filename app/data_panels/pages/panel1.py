# -*- coding: utf-8 -*-
"""
Dashboard 1 — Resim Onay Performans Panosu.

For the B-department (approver) manager: which divisions approve how many drawings,
and how long do requests wait on them. Self-contained — owns its data schema,
figures, layout and callback. Shared engine/atoms come from `common` / `theme`.

Data: data/dummy_data1.xlsx (override with env DATA1_PATH). Reloaded automatically
when the file's mtime changes (Yenile button forces a tick).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dash_table, dcc, html, register_page

import common as C
import theme as T

register_page(__name__, path="/panel1",
              name="Resim Onay", title="Resim Onay Panosu", order=1)

# --------------------------------------------------------------------------- #
# Configuration (specific to this dashboard's source schema)
# --------------------------------------------------------------------------- #
DATA_PATH = C.resolve_data("DATA1_PATH", "dummy_data1.xlsx")

COLUMN_MAP = {
    "Proje": "proje",
    "user_id": "user_id",
    "Bölüm Kodu": "bolum",
    "start_date": "start",
    "logged_date": "logged",
    "Bölüm Kodu (Akışı başlatan)": "kaynak",
    "event_type": "event_type",
}

UNITS = {
    "work_hours": {"col": "work_hours", "label": "Mesai Saati", "suffix": "sa", "dec": 1},
    "work_days":  {"col": "work_days",  "label": "Mesai Günü",  "suffix": "g",  "dec": 2},
    "cal_hours":  {"col": "cal_hours",  "label": "Takvim Saati", "suffix": "sa", "dec": 1},
    "cal_days":   {"col": "cal_days",   "label": "Takvim Günü",  "suffix": "g",  "dec": 2},
}
DEFAULT_UNIT = "work_hours"

BREAKDOWNS = {
    "bolum":   "Onaylayan Bölüm",
    "user_id": "Onaylayan (user_id)",
    "proje":   "Proje",
    "kaynak":  "Kaynak Bölüm",
}
DEFAULT_BREAKDOWN = "bolum"
REJECT_EVENT = "__Reject"

_fmt = C.fmt
_empty_fig = C.empty_fig
_opts = C.opts


# --------------------------------------------------------------------------- #
# Data: enrichment + cached loader (working-hours engine lives in common)
# --------------------------------------------------------------------------- #
def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(how="all")                       # drop fully-blank rows
    missing = [src for src in COLUMN_MAP if src not in df.columns]
    if missing:
        raise ValueError("Beklenen sütun(lar) bulunamadı: %s\nMevcut: %s"
                         % (missing, list(df.columns)))
    df = df[list(COLUMN_MAP)].rename(columns=COLUMN_MAP).copy()

    df["start"] = pd.to_datetime(df["start"], errors="coerce")
    df["logged"] = pd.to_datetime(df["logged"], errors="coerce")
    df = df.dropna(subset=["start", "logged"]).reset_index(drop=True)  # need both
    for c in ("proje", "user_id", "bolum", "kaynak", "event_type"):
        df[c] = C.clean_code(df[c])

    elapsed = df["logged"] - df["start"]
    df["cal_hours"] = elapsed.dt.total_seconds() / 3600.0
    df["cal_days"] = df["cal_hours"] / 24.0
    df["work_hours"] = C.working_hours_vec(df["start"], df["logged"])
    df["work_days"] = df["work_hours"] / C.WORKDAY_HOURS
    for c in ("cal_hours", "cal_days", "work_hours", "work_days"):
        df.loc[df[c] < 0, c] = 0.0

    df["is_reject"] = df["event_type"].eq(REJECT_EVENT)
    df["period_week"] = df["logged"].dt.to_period("W").dt.start_time
    return df


get_data = C.make_loader(DATA_PATH, _enrich)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _event_label(v):
    return v[2:] if isinstance(v, str) and v.startswith("__") else v


def _event_opts(values):
    return [{"label": _event_label(v), "value": v} for v in sorted(set(values))]


def _filter(df, basis, d0, d1, projes, bolums, kaynaks, events):
    dff = df
    datecol = "logged" if basis == "logged" else "start"
    if d0:
        dff = dff[dff[datecol] >= pd.Timestamp(d0)]
    if d1:
        dff = dff[dff[datecol] < pd.Timestamp(d1) + pd.Timedelta(days=1)]
    if projes:
        dff = dff[dff["proje"].isin(projes)]
    if bolums:
        dff = dff[dff["bolum"].isin(bolums)]
    if kaynaks:
        dff = dff[dff["kaynak"].isin(kaynaks)]
    if events:
        dff = dff[dff["event_type"].isin(events)]
    return dff


def _aggregate(dff, dim, unit_col):
    if dff.empty:
        return pd.DataFrame(columns=["key", "count", "min", "median", "mean",
                                     "p90", "max", "reject_pct"])
    g = dff.groupby(dim, observed=True)
    col = g[unit_col]
    out = pd.DataFrame({
        "key": g.size().index,
        "count": g.size().values,
        "min": col.min().values,
        "median": col.median().values,
        "mean": col.mean().values,
        "p90": col.quantile(0.9).values,
        "max": col.max().values,
        "reject_pct": (g["is_reject"].mean().values * 100),
    })
    return out.sort_values("count", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Figure builders
# --------------------------------------------------------------------------- #
def fig_breakdown(agg, unit, dim_label):
    if agg.empty:
        return _empty_fig()
    a = agg.head(20).copy()
    suffix = unit["suffix"]
    fig = go.Figure()
    fig.add_bar(x=a["key"], y=a["count"], name="Talep sayısı",
                marker_color=T.BLUE, marker_line_width=0,
                hovertemplate="%{x}<br>Talep: %{y}<extra></extra>")
    fig.add_trace(go.Scatter(
        x=a["key"], y=a["median"], name=f"Medyan bekleme ({suffix})",
        yaxis="y2", mode="lines+markers",
        line=dict(color=T.RED, width=2.5), marker=dict(size=7, color=T.RED),
        hovertemplate="%{x}<br>Medyan: %{y:.1f} " + suffix + "<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=a["key"], y=a["mean"], name=f"Ortalama bekleme ({suffix})",
        yaxis="y2", mode="lines+markers", visible="legendonly",
        line=dict(color=T.GOLD, width=2.5, dash="dot"),
        marker=dict(size=7, color=T.GOLD),
        hovertemplate="%{x}<br>Ortalama: %{y:.1f} " + suffix + "<extra></extra>"))
    fig.update_layout(
        title=f"{dim_label} — onay hacmi ve bekleme süresi",
        yaxis=dict(title="Talep sayısı"),
        yaxis2=dict(title=f"Medyan ({suffix})", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero", automargin=True),
        xaxis=dict(tickangle=-30), bargap=0.45,
        uirevision="keep")  # persist median/mean legend toggle across updates
    return T.style_fig(fig, height=400)


def fig_trend(dff, unit):
    if dff.empty:
        return _empty_fig()
    col, suffix = unit["col"], unit["suffix"]
    g = dff.groupby("period_week")
    t = pd.DataFrame({"week": g.size().index, "count": g.size().values,
                      "median": g[col].median().values,
                      "mean": g[col].mean().values}).sort_values("week")
    fig = go.Figure()
    fig.add_bar(x=t["week"], y=t["count"], name="Sonuçlanan talep",
                marker_color=T.BLUE_FAINT, marker_line_width=0,
                hovertemplate="%{x|%d.%m.%Y}<br>Sonuçlanan: %{y}<extra></extra>")
    fig.add_trace(go.Scatter(
        x=t["week"], y=t["median"], name=f"Medyan ({suffix})", yaxis="y2",
        mode="lines+markers", line=dict(color=T.BLUE, width=2.5),
        marker=dict(size=6, color=T.BLUE),
        hovertemplate="%{x|%d.%m.%Y}<br>Medyan: %{y:.1f} " + suffix + "<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=t["week"], y=t["mean"], name=f"Ortalama ({suffix})", yaxis="y2",
        mode="lines+markers", visible="legendonly",
        line=dict(color=T.GOLD, width=2.5, dash="dot"), marker=dict(size=6, color=T.GOLD),
        hovertemplate="%{x|%d.%m.%Y}<br>Ortalama: %{y:.1f} " + suffix + "<extra></extra>"))
    fig.update_layout(
        title=f"Zaman içinde bekleme süresi (haftalık, {suffix})",
        yaxis=dict(title="Sonuçlanan talep"),
        yaxis2=dict(title=f"Medyan ({suffix})", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero", automargin=True),
        uirevision="keep")  # persist median/mean legend toggle across updates
    return T.style_fig(fig, height=340)


def fig_donut(dff):
    if dff.empty:
        return _empty_fig()
    c = dff["event_type"].value_counts()
    raw = list(c.index)
    disp = [_event_label(l) for l in raw]
    fig = go.Figure(go.Pie(
        labels=disp, values=list(c.values), hole=0.62,
        marker=dict(colors=[T.EVENT_COLORS.get(l, T.GRAY) for l in raw]),
        sort=False, textinfo="percent", textfont=dict(size=12, color=T.WHITE),
        hovertemplate="%{label}<br>%{value} talep (%{percent})<extra></extra>"))
    rej = dff["is_reject"].mean() * 100
    fig.update_layout(
        title="Sonuç dağılımı",
        annotations=[dict(text=f"<b>{_fmt(rej,1)}%</b><br>ret", showarrow=False,
                          font=dict(size=15, color=T.INK))],
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.04, xanchor="center",
                    x=0.5, font=dict(size=11)),
        margin=dict(l=16, r=16, t=46, b=66))
    return T.style_fig(fig, height=340)


def fig_hist(dff, unit):
    if dff.empty:
        return _empty_fig()
    col, suffix, label = unit["col"], unit["suffix"], unit["label"]
    s = dff[col].dropna().to_numpy()
    if s.size == 0:
        return _empty_fig()
    # bin server-side -> send ~30 bars instead of every row's value
    counts, edges = np.histogram(s, bins=30)
    centers = (edges[:-1] + edges[1:]) / 2.0
    width = float(edges[1] - edges[0]) if len(edges) > 1 else 1.0
    fig = go.Figure(go.Bar(
        x=centers, y=counts, width=width,
        marker_color=T.BLUE, marker_line_color=T.WHITE, marker_line_width=0.5,
        hovertemplate="~%{x:.1f} " + suffix + "<br>%{y} talep<extra></extra>"))
    med, mean = float(np.median(s)), float(np.mean(s))
    fig.add_vline(x=med, line=dict(color=T.RED, width=2, dash="dash"),
                  annotation_text=f"medyan {_fmt(med,1)} {suffix}",
                  annotation_position="top left", annotation_font=dict(color=T.RED, size=12))
    fig.add_vline(x=mean, line=dict(color=T.GOLD, width=2, dash="dot"),
                  annotation_text=f"ortalama {_fmt(mean,1)} {suffix}",
                  annotation_position="top right", annotation_font=dict(color=T.GOLD, size=12))
    fig.update_layout(title=f"Bekleme süresi dağılımı ({label})",
                      xaxis=dict(title=label), yaxis=dict(title="Talep sayısı"),
                      bargap=0.03)
    return T.style_fig(fig, height=340)


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def serve_layout():
    df = get_data()
    dmin, dmax = df["logged"].min().date(), df["logged"].max().date()
    worknote = C.WORKNOTE

    return html.Div(className="app", children=[
        C.brand_header("Resim Onay Performans Panosu",
                       status=html.Div(id="status")),

        html.Section(className="controls", children=[
            html.Div(className="ctrl ctrl-date", children=[
                html.Label("Tarih aralığı"),
                dcc.DatePickerRange(
                    id="date-range", display_format="DD.MM.YYYY",
                    min_date_allowed=dmin, max_date_allowed=dmax,
                    start_date=dmin, end_date=dmax, first_day_of_week=1),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Tarih bazı"),
                dcc.RadioItems(id="date-basis", className="radio",
                               options=[{"label": "Talep", "value": "start"},
                                        {"label": "Onay", "value": "logged"}],
                               value="logged"),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Birim"),
                dcc.Dropdown(id="unit", clearable=False,
                             options=[{"label": v["label"], "value": k}
                                      for k, v in UNITS.items()],
                             value=DEFAULT_UNIT),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Kırılım"),
                dcc.Dropdown(id="breakdown", clearable=False,
                             options=[{"label": v, "value": k}
                                      for k, v in BREAKDOWNS.items()],
                             value=DEFAULT_BREAKDOWN),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Proje"),
                dcc.Dropdown(id="f-proje", multi=True, placeholder="Tümü",
                             options=_opts(df["proje"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Onaylayan bölüm"),
                dcc.Dropdown(id="f-bolum", multi=True, placeholder="Tümü",
                             options=_opts(df["bolum"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Kaynak bölüm"),
                dcc.Dropdown(id="f-kaynak", multi=True, placeholder="Tümü",
                             options=_opts(df["kaynak"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Sonuç"),
                dcc.Dropdown(id="f-event", multi=True, placeholder="Tümü",
                             options=_event_opts(df["event_type"])),
            ]),
            html.Div(className="ctrl ctrl-btn", children=[
                html.Button([html.I(className="fa"), " Yenile"],
                            id="reload", n_clicks=0, className="btn"),
                html.Div(worknote, className="worknote"),
            ]),
        ]),

        html.Section(className="kpis", children=[
            C.kpi_card("total", "Toplam talep"),
            C.kpi_card("median", "Medyan bekleme"),
            C.kpi_card("mean", "Ortalama bekleme"),
            C.kpi_card("p90", "P90 bekleme", accent="gold"),
            C.kpi_card("max", "Maks. bekleme", accent="gold"),
            C.kpi_card("reject", "Ret oranı", accent="red"),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[dcc.Graph(id="g-breakdown")]),
        ]),

        html.Section(className="row grid-3", children=[
            html.Div(className="card span-2", children=[dcc.Graph(id="g-hist")]),
            html.Div(className="card", children=[dcc.Graph(id="g-donut")]),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[dcc.Graph(id="g-trend")]),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[
                html.Div(className="card-head", children=[
                    html.H3(id="table-title", children="Detay tablosu"),
                    html.Span("Sütun başlığına tıklayarak sıralayın · dışa aktarın →",
                              className="hint"),
                ]),
                dash_table.DataTable(
                    id="detail-table", sort_action="native", page_size=15,
                    export_format="xlsx", export_headers="display",
                    style_as_list_view=True, style_table={"overflowX": "auto"},
                    style_header={"backgroundColor": T.LIGHT, "fontWeight": "700",
                                  "color": T.INK, "border": "none",
                                  "borderBottom": f"2px solid {T.HAIRLINE}",
                                  "fontFamily": T.FONT_FAMILY},
                    style_cell={"fontFamily": T.FONT_FAMILY, "fontSize": "13px",
                                "padding": "9px 12px", "border": "none",
                                "borderBottom": f"1px solid {T.HAIRLINE}",
                                "color": T.INK, "textAlign": "right"},
                    style_cell_conditional=[
                        {"if": {"column_id": "key"}, "textAlign": "left",
                         "fontWeight": "600"}],
                ),
            ]),
        ]),

        html.Footer(className="foot", children=[
            f"{C.COMPANY_NAME} · Resim Onay Performans Panosu"]),
    ])


layout = serve_layout  # callable -> data refreshes on each navigation


# --------------------------------------------------------------------------- #
# Callback
# --------------------------------------------------------------------------- #
@callback(
    Output("kpi-total-val", "children"),
    Output("kpi-median-val", "children"),
    Output("kpi-mean-val", "children"),
    Output("kpi-p90-val", "children"),
    Output("kpi-max-val", "children"),
    Output("kpi-reject-val", "children"),
    Output("g-breakdown", "figure"),
    Output("g-hist", "figure"),
    Output("g-donut", "figure"),
    Output("g-trend", "figure"),
    Output("detail-table", "data"),
    Output("detail-table", "columns"),
    Output("table-title", "children"),
    Output("status", "children"),
    Output("f-proje", "options"),
    Output("f-bolum", "options"),
    Output("f-kaynak", "options"),
    Output("f-event", "options"),
    Output("date-range", "min_date_allowed"),
    Output("date-range", "max_date_allowed"),
    Input("unit", "value"),
    Input("breakdown", "value"),
    Input("date-basis", "value"),
    Input("date-range", "start_date"),
    Input("date-range", "end_date"),
    Input("f-proje", "value"),
    Input("f-bolum", "value"),
    Input("f-kaynak", "value"),
    Input("f-event", "value"),
    Input("reload", "n_clicks"),
)
def update(unit_key, breakdown, basis, d0, d1, projes, bolums, kaynaks, events, _n):
    df = get_data()
    unit = UNITS[unit_key]
    dim_label = BREAKDOWNS[breakdown]
    dff = _filter(df, basis, d0, d1, projes, bolums, kaynaks, events)

    if dff.empty:
        kpis = ("0", "–", "–", "–", "–", "–")
    else:
        col, suf, dec = unit["col"], unit["suffix"], unit["dec"]
        kpis = (
            f"{len(dff):,}".replace(",", " "),
            f"{_fmt(dff[col].median(), dec)} {suf}",
            f"{_fmt(dff[col].mean(), dec)} {suf}",
            f"{_fmt(dff[col].quantile(0.9), dec)} {suf}",
            f"{_fmt(dff[col].max(), dec)} {suf}",
            f"{_fmt(dff['is_reject'].mean() * 100, 1)} %",
        )

    agg = _aggregate(dff, breakdown, unit["col"])
    f_break = fig_breakdown(agg, unit, dim_label)
    f_hist = fig_hist(dff, unit)
    f_donut = fig_donut(dff)
    f_trend = fig_trend(dff, unit)

    suf, dec = unit["suffix"], unit["dec"]
    total = max(len(dff), 1)
    rows = []
    for _, r in agg.iterrows():
        rows.append({
            "key": r["key"], "count": int(r["count"]),
            "share": f"{r['count'] / total * 100:.1f}%",
            "min": _fmt(r["min"], dec),
            "median": _fmt(r["median"], dec), "mean": _fmt(r["mean"], dec),
            "p90": _fmt(r["p90"], dec), "max": _fmt(r["max"], dec),
            "reject": f"{_fmt(r['reject_pct'], 1)}%",
        })
    columns = [
        {"name": dim_label, "id": "key"},
        {"name": "Talep", "id": "count"},
        {"name": "Pay", "id": "share"},
        {"name": f"Min ({suf})", "id": "min"},
        {"name": f"Medyan ({suf})", "id": "median"},
        {"name": f"Ortalama ({suf})", "id": "mean"},
        {"name": f"P90 ({suf})", "id": "p90"},
        {"name": f"Max ({suf})", "id": "max"},
        {"name": "Ret %", "id": "reject"},
    ]
    table_title = f"Detay — {dim_label} bazında ({unit['label']})"

    status = (f"{len(dff):,}".replace(",", " ") + f" / {len(df)} kayıt  ·  "
              f"kaynak: {os.path.basename(DATA_PATH)}")
    dmin, dmax = df["logged"].min().date(), df["logged"].max().date()

    return (*kpis, f_break, f_hist, f_donut, f_trend, rows, columns, table_title,
            status, _opts(df["proje"]), _opts(df["bolum"]), _opts(df["kaynak"]),
            _event_opts(df["event_type"]), dmin, dmax)
