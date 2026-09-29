# -*- coding: utf-8 -*-
"""
Dashboard 2 — NCR Performans Panosu.

Same lens as dashboard 1, for NCR requests: how many NCRs and how long they take
to turn around (RECEIVING -> SEND). Export is already filtered to closed NCRs.

Data: data/dummy_data2.xlsx (env DATA2_PATH). NCR export layout: row 1 empty,
row 2 = headers, data from row 3  -> skiprows=1.
Component IDs are prefixed "d2-" to stay distinct from dashboard 1's callback.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dash_table, dcc, html, register_page

import common as C
import theme as T

register_page(__name__, path="/panel2",
              name="NCR", title="NCR Panosu", order=2)

DATA_PATH = C.resolve_data("DATA2_PATH", "dummy_data2.xlsx")

# RECEIVING -> opened (start), SEND -> closed (logged); turnaround = logged - start
COLUMN_MAP = {
    "PROJE KODU": "proje",
    "NCR TYPE": "ncr_type",
    "REV": "rev",
    "RECEIVING DATE": "start",
    "SEND DATE": "logged",
    "YAPILAN İŞLEM": "islem",
    "GÜNCEL BÖLÜM": "bolum",
    "İŞLEM YAPAN PERSONEL": "personel",
}

UNITS = {
    "work_hours": {"col": "work_hours", "label": "Mesai Saati", "suffix": "sa", "dec": 1},
    "work_days":  {"col": "work_days",  "label": "Mesai Günü",  "suffix": "g",  "dec": 2},
    "cal_hours":  {"col": "cal_hours",  "label": "Takvim Saati", "suffix": "sa", "dec": 1},
    "cal_days":   {"col": "cal_days",   "label": "Takvim Günü",  "suffix": "g",  "dec": 2},
}
DEFAULT_UNIT = "work_hours"

BREAKDOWNS = {
    "bolum":    "Güncel Bölüm",
    "personel": "İşlem Yapan Personel",
    "proje":    "Proje Kodu",
    "ncr_type": "NCR Type",
    "islem":    "Yapılan İşlem",
    "rev":      "Revizyon",
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

    df["start"] = pd.to_datetime(df["start"], errors="coerce")
    df["logged"] = pd.to_datetime(df["logged"], errors="coerce")
    df = df.dropna(subset=["start", "logged"]).reset_index(drop=True)

    df["rev"] = pd.to_numeric(df["rev"], errors="coerce").astype("Int64")
    for c in ("proje", "ncr_type", "islem", "bolum", "personel", "rev"):
        df[c] = C.clean_code(df[c])

    elapsed = df["logged"] - df["start"]
    df["cal_hours"] = elapsed.dt.total_seconds() / 3600.0
    df["cal_days"] = df["cal_hours"] / 24.0
    df["work_hours"] = C.working_hours_vec(df["start"], df["logged"])
    df["work_days"] = df["work_hours"] / C.WORKDAY_HOURS
    for c in ("cal_hours", "cal_days", "work_hours", "work_days"):
        df.loc[df[c] < 0, c] = 0.0

    df["period_week"] = df["logged"].dt.to_period("W").dt.start_time
    return df


get_data = C.make_loader(DATA_PATH, _enrich, skiprows=1)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _filter(df, basis, d0, d1, projes, types, revs, bolums, personeller, islemler):
    dff = df
    datecol = "logged" if basis == "logged" else "start"
    if d0:
        dff = dff[dff[datecol] >= pd.Timestamp(d0)]
    if d1:
        dff = dff[dff[datecol] < pd.Timestamp(d1) + pd.Timedelta(days=1)]
    if projes:
        dff = dff[dff["proje"].isin(projes)]
    if types:
        dff = dff[dff["ncr_type"].isin(types)]
    if revs:
        dff = dff[dff["rev"].isin(revs)]
    if bolums:
        dff = dff[dff["bolum"].isin(bolums)]
    if personeller:
        dff = dff[dff["personel"].isin(personeller)]
    if islemler:
        dff = dff[dff["islem"].isin(islemler)]
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
    fig.add_bar(x=a["key"], y=a["count"], name="NCR sayısı",
                marker_color=T.BLUE, marker_line_width=0,
                hovertemplate="%{x}<br>NCR: %{y}<extra></extra>")
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
        title=f"{dim_label} — NCR hacmi ve işlem süresi",
        yaxis=dict(title="NCR sayısı"),
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
    fig.add_bar(x=t["week"], y=t["count"], name="Kapanan NCR",
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
        title=f"Zaman içinde işlem süresi (haftalık, {suffix})",
        yaxis=dict(title="Kapanan NCR"),
        yaxis2=dict(title=f"Medyan ({suffix})", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero", automargin=True),
        uirevision="keep")
    return T.style_fig(fig, height=340)


_DONUT_PALETTE = [T.BLUE, T.GOLD, T.BLUE_SOFT, T.NAVY, T.RED, T.GRAY,
                  T.BLUE_FAINT, "#B5862E"]


def fig_donut(dff, col, title):
    """Generic donut of a categorical column, with total NCR in the centre."""
    if dff.empty:
        return _empty_fig()
    c = dff[col].value_counts()
    labels = [str(x) for x in c.index]
    fig = go.Figure(go.Pie(
        labels=labels, values=list(c.values), hole=0.62,
        marker=dict(colors=[_DONUT_PALETTE[i % len(_DONUT_PALETTE)]
                            for i in range(len(labels))]),
        sort=True, textinfo="percent", textfont=dict(size=11, color=T.WHITE),
        hovertemplate="%{label}<br>%{value} NCR (%{percent})<extra></extra>"))
    fig.update_layout(
        title=title,
        annotations=[dict(text=f"<b>{len(dff):,}</b><br>NCR".replace(',', ' '),
                          showarrow=False, font=dict(size=14, color=T.INK))],
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
    counts, edges = np.histogram(s, bins=30)
    centers = (edges[:-1] + edges[1:]) / 2.0
    width = float(edges[1] - edges[0]) if len(edges) > 1 else 1.0
    fig = go.Figure(go.Bar(
        x=centers, y=counts, width=width,
        marker_color=T.BLUE, marker_line_color=T.WHITE, marker_line_width=0.5,
        hovertemplate="~%{x:.1f} " + suffix + "<br>%{y} NCR<extra></extra>"))
    med, mean = float(np.median(s)), float(np.mean(s))
    fig.add_vline(x=med, line=dict(color=T.RED, width=2, dash="dash"),
                  annotation_text=f"medyan {_fmt(med,1)} {suffix}",
                  annotation_position="top left", annotation_font=dict(color=T.RED, size=12))
    fig.add_vline(x=mean, line=dict(color=T.GOLD, width=2, dash="dot"),
                  annotation_text=f"ortalama {_fmt(mean,1)} {suffix}",
                  annotation_position="top right", annotation_font=dict(color=T.GOLD, size=12))
    fig.update_layout(title=f"İşlem süresi dağılımı ({label})",
                      xaxis=dict(title=label), yaxis=dict(title="NCR sayısı"),
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
        C.brand_header("NCR Performans Panosu", status=html.Div(id="d2-status")),

        html.Section(className="controls", children=[
            html.Div(className="ctrl ctrl-date", children=[
                html.Label("Tarih aralığı"),
                dcc.DatePickerRange(
                    id="d2-date-range", display_format="DD.MM.YYYY",
                    min_date_allowed=dmin, max_date_allowed=dmax,
                    start_date=dmin, end_date=dmax, first_day_of_week=1),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Tarih bazı"),
                dcc.RadioItems(id="d2-date-basis", className="radio",
                               options=[{"label": "Geliş", "value": "start"},
                                        {"label": "Gönderim", "value": "logged"}],
                               value="logged"),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Birim"),
                dcc.Dropdown(id="d2-unit", clearable=False,
                             options=[{"label": v["label"], "value": k}
                                      for k, v in UNITS.items()], value=DEFAULT_UNIT),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Kırılım"),
                dcc.Dropdown(id="d2-breakdown", clearable=False,
                             options=[{"label": v, "value": k}
                                      for k, v in BREAKDOWNS.items()], value=DEFAULT_BREAKDOWN),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Proje kodu"),
                dcc.Dropdown(id="d2-f-proje", multi=True, placeholder="Tümü",
                             options=_opts(df["proje"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("NCR type"),
                dcc.Dropdown(id="d2-f-type", multi=True, placeholder="Tümü",
                             options=_opts(df["ncr_type"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Revizyon"),
                dcc.Dropdown(id="d2-f-rev", multi=True, placeholder="Tümü",
                             options=_opts(df["rev"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Güncel bölüm"),
                dcc.Dropdown(id="d2-f-bolum", multi=True, placeholder="Tümü",
                             options=_opts(df["bolum"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Personel"),
                dcc.Dropdown(id="d2-f-personel", multi=True, placeholder="Tümü",
                             options=_opts(df["personel"])),
            ]),
            html.Div(className="ctrl", children=[
                html.Label("Yapılan işlem"),
                dcc.Dropdown(id="d2-f-islem", multi=True, placeholder="Tümü",
                             options=_opts(df["islem"])),
            ]),
            html.Div(className="ctrl ctrl-btn", children=[
                html.Div(worknote, className="worknote"),
            ]),
        ]),

        html.Section(className="kpis", children=[
            C.kpi_card("d2-total", "Toplam NCR"),
            C.kpi_card("d2-median", "Medyan süre"),
            C.kpi_card("d2-mean", "Ortalama süre"),
            C.kpi_card("d2-p90", "P90 süre", accent="gold"),
            C.kpi_card("d2-max", "Maks. süre", accent="gold"),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[dcc.Graph(id="d2-g-breakdown")]),
        ]),

        html.Section(className="row grid-3", children=[
            html.Div(className="card", children=[dcc.Graph(id="d2-g-donut-islem")]),
            html.Div(className="card", children=[dcc.Graph(id="d2-g-donut-type")]),
            html.Div(className="card", children=[dcc.Graph(id="d2-g-donut-rev")]),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[dcc.Graph(id="d2-g-hist")]),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[dcc.Graph(id="d2-g-trend")]),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[
                html.Div(className="card-head", children=[
                    html.H3(id="d2-table-title", children="Detay tablosu"),
                    html.Span("Sütun başlığına tıklayarak sıralayın · dışa aktarın →",
                              className="hint"),
                ]),
                dash_table.DataTable(
                    id="d2-detail-table", sort_action="native", page_size=15,
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
                dcc.Store(id=TABLE_SORT),
            ]),
        ]),

        html.Footer(className="foot", children=[
            f"{C.COMPANY_NAME} · NCR Performans Panosu"]),
    ])


layout = serve_layout
TABLE_SORT = C.two_way_sort("d2-detail-table")  # header clicks: ascending <-> descending


# --------------------------------------------------------------------------- #
# Callback
# --------------------------------------------------------------------------- #
@callback(
    Output("kpi-d2-total-val", "children"),
    Output("kpi-d2-median-val", "children"),
    Output("kpi-d2-mean-val", "children"),
    Output("kpi-d2-p90-val", "children"),
    Output("kpi-d2-max-val", "children"),
    Output("d2-g-breakdown", "figure"),
    Output("d2-g-donut-islem", "figure"),
    Output("d2-g-donut-type", "figure"),
    Output("d2-g-donut-rev", "figure"),
    Output("d2-g-hist", "figure"),
    Output("d2-g-trend", "figure"),
    Output("d2-detail-table", "data"),
    Output("d2-detail-table", "columns"),
    Output("d2-table-title", "children"),
    Output("d2-status", "children"),
    Output("d2-f-proje", "options"),
    Output("d2-f-type", "options"),
    Output("d2-f-rev", "options"),
    Output("d2-f-bolum", "options"),
    Output("d2-f-personel", "options"),
    Output("d2-f-islem", "options"),
    Output("d2-date-range", "min_date_allowed"),
    Output("d2-date-range", "max_date_allowed"),
    Input("d2-unit", "value"),
    Input("d2-breakdown", "value"),
    Input("d2-date-basis", "value"),
    Input("d2-date-range", "start_date"),
    Input("d2-date-range", "end_date"),
    Input("d2-f-proje", "value"),
    Input("d2-f-type", "value"),
    Input("d2-f-rev", "value"),
    Input("d2-f-bolum", "value"),
    Input("d2-f-personel", "value"),
    Input("d2-f-islem", "value"),
)
def update(unit_key, breakdown, basis, d0, d1, projes, types, revs,
           bolums, personeller, islemler):
    df = get_data()
    unit = UNITS[unit_key]
    dim_label = BREAKDOWNS[breakdown]
    dff = _filter(df, basis, d0, d1, projes, types, revs, bolums, personeller, islemler)

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
    f_donut_islem = fig_donut(dff, "islem", "Yapılan işlem dağılımı")
    f_donut_type = fig_donut(dff, "ncr_type", "NCR type dağılımı")
    f_donut_rev = fig_donut(dff, "rev", "Revizyon dağılımı")
    f_hist = fig_hist(dff, unit)
    f_trend = fig_trend(dff, unit)

    suf, dec = unit["suffix"], unit["dec"]
    total = max(len(dff), 1)
    rows = []
    for _, r in agg.iterrows():
        rows.append({
            "key": r["key"], "count": int(r["count"]),
            "share": C.num(r["count"] / total * 100, 1),
            "min": C.num(r["min"], dec), "median": C.num(r["median"], dec),
            "mean": C.num(r["mean"], dec), "p90": C.num(r["p90"], dec),
            "max": C.num(r["max"], dec),
        })
    columns = [
        {"name": dim_label, "id": "key"},
        C.num_col("NCR", "count"),
        C.num_col("Pay", "share", 1, "%"),
        C.num_col(f"Min ({suf})", "min", dec),
        C.num_col(f"Medyan ({suf})", "median", dec),
        C.num_col(f"Ortalama ({suf})", "mean", dec),
        C.num_col(f"P90 ({suf})", "p90", dec),
        C.num_col(f"Max ({suf})", "max", dec),
    ]
    table_title = f"Detay — {dim_label} bazında ({unit['label']})"

    status = (f"{len(dff):,}".replace(",", " ") + f" / {len(df)} NCR  ·  "
              f"kaynak: {os.path.basename(DATA_PATH)}")
    dmin, dmax = df["logged"].min().date(), df["logged"].max().date()

    return (*kpis, f_break, f_donut_islem, f_donut_type, f_donut_rev, f_hist, f_trend,
            rows, columns, table_title, status,
            _opts(df["proje"]), _opts(df["ncr_type"]), _opts(df["rev"]),
            _opts(df["bolum"]), _opts(df["personel"]), _opts(df["islem"]), dmin, dmax)
