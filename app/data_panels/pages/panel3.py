# -*- coding: utf-8 -*-
"""
Dashboard 3 — Yardım Masası Performans Panosu.

Same lens as dashboards 1/2, for help-desk tickets: do the coworkers (closers) clear
people's requests, and how fast (turnaround). Export filtered to closed tickets.

KEY QUIRK: 'Başlama Tarihi' is date-only (the export drops the time). We reconstruct
the exact start from 'Kapanma Tarihi' - 'Ortalama Kapanma Süresi' (calendar days),
rounded to the minute, and ignore the provided date-only Başlama Tarihi.

Data: data/dummy_data3.xlsx (env DATA3_PATH). Layout: rows 1-2 junk, header row 3
(skiprows=2). Component IDs are prefixed "d3-".
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dash_table, dcc, html, register_page

import common as C
import theme as T

register_page(__name__, path="/panel3",
              name="Yardım Masası", title="Yardım Masası Panosu", order=3)

DATA_PATH = C.resolve_data("DATA3_PATH", "dummy_data3.xlsx")

# Kapanma Tarihi -> close (logged); start reconstructed = logged - sure(days).
COLUMN_MAP = {
    "Dönem": "donem",
    "Bölüm": "bolum",
    "Kategori": "kategori",
    "Modül İsmi": "modul",
    "Kapanma Tarihi": "logged",
    "Ortalama Kapanma Süresi": "sure",
    "Kapatan Kullanıcı Kimlik": "closer",
}

UNITS = {
    "work_hours": {"col": "work_hours", "label": "Mesai Saati", "suffix": "sa", "dec": 1},
    "work_days":  {"col": "work_days",  "label": "Mesai Günü",  "suffix": "g",  "dec": 2},
    "cal_hours":  {"col": "cal_hours",  "label": "Takvim Saati", "suffix": "sa", "dec": 1},
    "cal_days":   {"col": "cal_days",   "label": "Takvim Günü",  "suffix": "g",  "dec": 2},
}
DEFAULT_UNIT = "work_hours"

BREAKDOWNS = {
    "bolum":    "Bölüm",
    "closer":   "Kapatan Kullanıcı",
    "modul":    "Modül İsmi",
    "kategori": "Kategori",
    "donem":    "Dönem",
}
DEFAULT_BREAKDOWN = "bolum"

_fmt = C.fmt
_empty_fig = C.empty_fig
_opts = C.opts


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(how="all")
    missing = [src for src in COLUMN_MAP if src not in df.columns]
    if missing:
        raise ValueError("Beklenen sütun(lar) bulunamadı: %s\nMevcut: %s"
                         % (missing, list(df.columns)))
    df = df[list(COLUMN_MAP)].rename(columns=COLUMN_MAP).copy()

    df["logged"] = pd.to_datetime(df["logged"], errors="coerce")
    df["sure"] = pd.to_numeric(df["sure"], errors="coerce")
    df = df.dropna(subset=["logged", "sure"]).reset_index(drop=True)

    # reconstruct the exact start (Başlama Tarihi lost its time)
    df["start"] = (df["logged"] - pd.to_timedelta(df["sure"], unit="D")).dt.round("min")

    for c in ("donem", "bolum", "kategori", "modul", "closer"):
        df[c] = C.clean_code(df[c])

    df["cal_days"] = df["sure"]                 # authoritative calendar-day duration
    df["cal_hours"] = df["sure"] * 24.0
    df["work_hours"] = C.working_hours_vec(df["start"], df["logged"])
    df["work_days"] = df["work_hours"] / C.WORKDAY_HOURS
    for c in ("cal_hours", "cal_days", "work_hours", "work_days"):
        df.loc[df[c] < 0, c] = 0.0

    df["period_week"] = df["logged"].dt.to_period("W").dt.start_time
    return df


get_data = C.make_loader(DATA_PATH, _enrich, skiprows=2)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _filter(df, basis, d0, d1, bolums, closers, moduller, kategoriler, donemler):
    dff = df
    datecol = "logged" if basis == "logged" else "start"
    if d0:
        dff = dff[dff[datecol] >= pd.Timestamp(d0)]
    if d1:
        dff = dff[dff[datecol] < pd.Timestamp(d1) + pd.Timedelta(days=1)]
    if bolums:
        dff = dff[dff["bolum"].isin(bolums)]
    if closers:
        dff = dff[dff["closer"].isin(closers)]
    if moduller:
        dff = dff[dff["modul"].isin(moduller)]
    if kategoriler:
        dff = dff[dff["kategori"].isin(kategoriler)]
    if donemler:
        dff = dff[dff["donem"].isin(donemler)]
    return dff


def _aggregate(dff, dim, unit_col):
    if dff.empty:
        return pd.DataFrame(columns=["key", "count", "min", "median", "mean", "p90", "max"])
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
    })
    return out.sort_values("count", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Figures
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
        x=a["key"], y=a["median"], name=f"Medyan süre ({suffix})",
        yaxis="y2", mode="lines+markers",
        line=dict(color=T.RED, width=2.5), marker=dict(size=7, color=T.RED),
        hovertemplate="%{x}<br>Medyan: %{y:.1f} " + suffix + "<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=a["key"], y=a["mean"], name=f"Ortalama süre ({suffix})",
        yaxis="y2", mode="lines+markers", visible="legendonly",
        line=dict(color=T.GOLD, width=2.5, dash="dot"),
        marker=dict(size=7, color=T.GOLD),
        hovertemplate="%{x}<br>Ortalama: %{y:.1f} " + suffix + "<extra></extra>"))
    fig.update_layout(
        title=f"{dim_label} — talep hacmi ve kapanma süresi",
        yaxis=dict(title="Talep sayısı"),
        yaxis2=dict(title=f"Medyan ({suffix})", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero", automargin=True),
        xaxis=dict(tickangle=-30), bargap=0.45, uirevision="keep")
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
    fig.add_bar(x=t["week"], y=t["count"], name="Kapanan talep",
                marker_color=T.BLUE_FAINT, marker_line_width=0,
                hovertemplate="%{x|%d.%m.%Y}<br>Kapanan: %{y}<extra></extra>")
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
        title=f"Zaman içinde kapanma süresi (haftalık, {suffix})",
        yaxis=dict(title="Kapanan talep"),
        yaxis2=dict(title=f"Medyan ({suffix})", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero", automargin=True),
        uirevision="keep")
    return T.style_fig(fig, height=400)


_DONUT_PALETTE = [T.BLUE, T.GOLD, T.BLUE_SOFT, T.NAVY, T.RED, T.GRAY,
                  T.BLUE_FAINT, "#B5862E"]


def fig_donut(dff, col, title):
    if dff.empty:
        return _empty_fig()
    c = dff[col].value_counts()
    labels = [str(x) for x in c.index]
    fig = go.Figure(go.Pie(
        labels=labels, values=list(c.values), hole=0.62,
        marker=dict(colors=[_DONUT_PALETTE[i % len(_DONUT_PALETTE)]
                            for i in range(len(labels))]),
        sort=True, textinfo="percent", textfont=dict(size=11, color=T.WHITE),
        hovertemplate="%{label}<br>%{value} talep (%{percent})<extra></extra>"))
    fig.update_layout(
        title=title,
        annotations=[dict(text=f"<b>{len(dff):,}</b><br>talep".replace(',', ' '),
                          showarrow=False, font=dict(size=14, color=T.INK))],
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.04, xanchor="center",
                    x=0.5, font=dict(size=11)),
        margin=dict(l=16, r=16, t=46, b=66))
    return T.style_fig(fig, height=400)


def fig_hist(dff, unit):
    if dff.empty:
        return _empty_fig()
    col, suffix, label = unit["col"], unit["suffix"], unit["label"]
    s = dff[col].dropna().to_numpy()
    if s.size == 0:
        return _empty_fig()
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
    fig.update_layout(title=f"Kapanma süresi dağılımı ({label})",
                      xaxis=dict(title=label), yaxis=dict(title="Talep sayısı"),
                      bargap=0.03)
    return T.style_fig(fig, height=400)


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def serve_layout():
    df = get_data()
    dmin, dmax = df["logged"].min().date(), df["logged"].max().date()
    worknote = C.WORKNOTE

    return html.Div(className="app", children=[
        C.brand_header("Yardım Masası Performans Panosu", status=html.Div(id="d3-status")),

        html.Section(className="controls", children=[
            html.Div(className="ctrl ctrl-date", children=[
                html.Label("Tarih aralığı"),
                dcc.DatePickerRange(
                    id="d3-date-range", display_format="DD.MM.YYYY",
                    min_date_allowed=dmin, max_date_allowed=dmax,
                    start_date=dmin, end_date=dmax, first_day_of_week=1),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Tarih bazı"),
                dcc.RadioItems(id="d3-date-basis", className="radio",
                               options=[{"label": "Başlama", "value": "start"},
                                        {"label": "Kapanma", "value": "logged"}],
                               value="logged"),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Birim"),
                dcc.Dropdown(id="d3-unit", clearable=False,
                             options=[{"label": v["label"], "value": k}
                                      for k, v in UNITS.items()], value=DEFAULT_UNIT),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Kırılım"),
                dcc.Dropdown(id="d3-breakdown", clearable=False,
                             options=[{"label": v, "value": k}
                                      for k, v in BREAKDOWNS.items()], value=DEFAULT_BREAKDOWN),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Bölüm"),
                dcc.Dropdown(id="d3-f-bolum", multi=True, placeholder="Tümü",
                             options=_opts(df["bolum"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Kapatan kullanıcı"),
                dcc.Dropdown(id="d3-f-closer", multi=True, placeholder="Tümü",
                             options=_opts(df["closer"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Modül"),
                dcc.Dropdown(id="d3-f-modul", multi=True, placeholder="Tümü",
                             options=_opts(df["modul"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Kategori"),
                dcc.Dropdown(id="d3-f-kategori", multi=True, placeholder="Tümü",
                             options=_opts(df["kategori"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Dönem"),
                dcc.Dropdown(id="d3-f-donem", multi=True, placeholder="Tümü",
                             options=_opts(df["donem"])),
            ]),
            html.Div(className="ctrl ctrl-btn", children=[
                html.Button([html.I(className="fa"), " Yenile"],
                            id="d3-reload", n_clicks=0, className="btn"),
                html.Div(worknote, className="worknote"),
            ]),
        ]),

        html.Section(className="kpis", children=[
            C.kpi_card("d3-total", "Toplam talep"),
            C.kpi_card("d3-median", "Medyan süre"),
            C.kpi_card("d3-mean", "Ortalama süre"),
            C.kpi_card("d3-p90", "P90 süre", accent="gold"),
            C.kpi_card("d3-max", "Maks. süre", accent="gold"),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[dcc.Graph(id="d3-g-breakdown")]),
        ]),

        html.Section(className="row grid-3", children=[
            html.Div(className="card span-2", children=[dcc.Graph(id="d3-g-hist")]),
            html.Div(className="card", children=[dcc.Graph(id="d3-g-donut-modul")]),
        ]),

        html.Section(className="row grid-3", children=[
            html.Div(className="card span-2", children=[dcc.Graph(id="d3-g-trend")]),
            html.Div(className="card", children=[dcc.Graph(id="d3-g-donut-kategori")]),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[
                html.Div(className="card-head", children=[
                    html.H3(id="d3-table-title", children="Detay tablosu"),
                    html.Span("Sütun başlığına tıklayarak sıralayın · dışa aktarın →",
                              className="hint"),
                ]),
                dash_table.DataTable(
                    id="d3-detail-table", sort_action="native", page_size=15,
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
            f"{C.COMPANY_NAME} · Yardım Masası Performans Panosu"]),
    ])


layout = serve_layout


# --------------------------------------------------------------------------- #
# Callback
# --------------------------------------------------------------------------- #
@callback(
    Output("kpi-d3-total-val", "children"),
    Output("kpi-d3-median-val", "children"),
    Output("kpi-d3-mean-val", "children"),
    Output("kpi-d3-p90-val", "children"),
    Output("kpi-d3-max-val", "children"),
    Output("d3-g-breakdown", "figure"),
    Output("d3-g-hist", "figure"),
    Output("d3-g-donut-modul", "figure"),
    Output("d3-g-trend", "figure"),
    Output("d3-g-donut-kategori", "figure"),
    Output("d3-detail-table", "data"),
    Output("d3-detail-table", "columns"),
    Output("d3-table-title", "children"),
    Output("d3-status", "children"),
    Output("d3-f-bolum", "options"),
    Output("d3-f-closer", "options"),
    Output("d3-f-modul", "options"),
    Output("d3-f-kategori", "options"),
    Output("d3-f-donem", "options"),
    Output("d3-date-range", "min_date_allowed"),
    Output("d3-date-range", "max_date_allowed"),
    Input("d3-unit", "value"),
    Input("d3-breakdown", "value"),
    Input("d3-date-basis", "value"),
    Input("d3-date-range", "start_date"),
    Input("d3-date-range", "end_date"),
    Input("d3-f-bolum", "value"),
    Input("d3-f-closer", "value"),
    Input("d3-f-modul", "value"),
    Input("d3-f-kategori", "value"),
    Input("d3-f-donem", "value"),
    Input("d3-reload", "n_clicks"),
)
def update(unit_key, breakdown, basis, d0, d1, bolums, closers, moduller,
           kategoriler, donemler, _n):
    df = get_data()
    unit = UNITS[unit_key]
    dim_label = BREAKDOWNS[breakdown]
    dff = _filter(df, basis, d0, d1, bolums, closers, moduller, kategoriler, donemler)

    if dff.empty:
        kpis = ("0", "–", "–", "–", "–")
    else:
        col, suf, dec = unit["col"], unit["suffix"], unit["dec"]
        kpis = (
            f"{len(dff):,}".replace(",", " "),
            f"{_fmt(dff[col].median(), dec)} {suf}",
            f"{_fmt(dff[col].mean(), dec)} {suf}",
            f"{_fmt(dff[col].quantile(0.9), dec)} {suf}",
            f"{_fmt(dff[col].max(), dec)} {suf}",
        )

    agg = _aggregate(dff, breakdown, unit["col"])
    f_break = fig_breakdown(agg, unit, dim_label)
    f_hist = fig_hist(dff, unit)
    f_donut_modul = fig_donut(dff, "modul", "Modül dağılımı")
    f_trend = fig_trend(dff, unit)
    f_donut_kat = fig_donut(dff, "kategori", "Kategori dağılımı")

    suf, dec = unit["suffix"], unit["dec"]
    total = max(len(dff), 1)
    rows = []
    for _, r in agg.iterrows():
        rows.append({
            "key": r["key"], "count": int(r["count"]),
            "share": f"{r['count'] / total * 100:.1f}%",
            "min": _fmt(r["min"], dec), "median": _fmt(r["median"], dec),
            "mean": _fmt(r["mean"], dec), "p90": _fmt(r["p90"], dec),
            "max": _fmt(r["max"], dec),
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
    ]
    table_title = f"Detay — {dim_label} bazında ({unit['label']})"

    status = (f"{len(dff):,}".replace(",", " ") + f" / {len(df)} talep  ·  "
              f"kaynak: {os.path.basename(DATA_PATH)}")
    dmin, dmax = df["logged"].min().date(), df["logged"].max().date()

    return (*kpis, f_break, f_hist, f_donut_modul, f_trend, f_donut_kat,
            rows, columns, table_title, status,
            _opts(df["bolum"]), _opts(df["closer"]), _opts(df["modul"]),
            _opts(df["kategori"]), _opts(df["donem"]), dmin, dmax)
