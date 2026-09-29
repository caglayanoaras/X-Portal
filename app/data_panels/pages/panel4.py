# -*- coding: utf-8 -*-
"""
Dashboard 4 — Ortak Malzemeler.

For the GM: how many standard parts / materials does a project use, and how many
of them are shared with other projects (e.g. MMU vs HÜRJET, or MMU vs HÜRJET,
GÖKBEY and TLUH together)? Every row of the export is one item ("kalem"); every
yes/no column is a project. Project columns are detected from the data, so a new
project in the export needs no code change. One focus project is compared with
one or more projects; the focus project's items are bucketed by how many of the
compared projects also use them, and the combinations chart shows every exact
project combination. Self-contained like dashboards 1-3; shared atoms come from
`common` / `theme`.

Data: data/dummy_data4.xlsx (env DATA4_PATH). Header on row 1 -> skiprows=0.
Photos: drop `assets/projects/<slug>.jpg|jpeg|png|webp`, where slug is the
lower-case ASCII project code (GÖKBEY -> gokbey.jpg, HURKUS-2 -> hurkus-2.jpg).
Projects without a photo show assets/projects/_placeholder.svg.
Component IDs are prefixed "d4-".
"""
from __future__ import annotations

import os
import re
from collections import Counter, namedtuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import (ALL, Input, Output, State, callback, ctx, dash_table, dcc,
                  get_asset_url, html, no_update, register_page)
from plotly.subplots import make_subplots

import common as C
import theme as T

register_page(__name__, path="/panel4",
              name="Ortak Malzemeler", title="Ortak Malzemeler", order=4)

TITLE = "Ortak Malzemeler"

# --------------------------------------------------------------------------- #
# Configuration (specific to this dashboard's source schema)
# --------------------------------------------------------------------------- #
DATA_PATH = C.resolve_data("DATA4_PATH", "dummy_data4.xlsx")

COLUMN_MAP = {
    "TYPE": "tip",
    "BRANCH": "brans",
    "SUB-BRANCH": "alt_brans",
    "SPECIFICATION": "spec",
    "MANUFACTURER": "uretici",
    "DESIGNATION": "tanim",
}
TEXT_COLS = list(COLUMN_MAP.values())
COLUMN_LABELS = {"tip": "Tip", "brans": "Branş", "alt_brans": "Alt branş",
                 "spec": "Spesifikasyon", "uretici": "Üretici", "tanim": "Tanım"}

# Display names where the export header differs from the project's name;
# every other project column is shown as-is.
PROJECT_LABELS = {"HURJET": "HÜRJET", "HURKUS-2": "HÜRKUŞ-2", "IHA": "İHA"}
DEFAULT_FOCUS = "MMU"
DEFAULT_COMPARE = "HURJET"

BREAKDOWNS = {k: COLUMN_LABELS[k] for k in ("brans", "alt_brans", "tip", "spec", "uretici")}
DEFAULT_BREAKDOWN = "brans"
TOP_GROUPS = 12          # bars in the breakdown chart
TOP_COMBOS = 12          # bars in the combinations chart
TOP_WORDS = 12           # bars in the keyword chart
TABLE_WIDTHS = {"tip": 120, "brans": 110, "alt_brans": 170,   # min px per text column
                "spec": 125, "uretici": 150, "tanim": 280}

# Focus-project items by how many of the compared projects also use them
# (key, colour, text class inside the answer bar). "bazi" exists only when
# more than one project is compared.
BUCKETS = (
    ("hepsi", T.SHARE_ALL, ""),
    ("bazi", T.SHARE_SOME, "ink"),
    ("hic", T.SHARE_NONE, "ink"),
)

# Turkish search words -> the English terms used in DESIGNATION (folded form).
SYNONYMS = {
    "titanyum": ("titanium",),
    "celik": ("steel",),
    "paslanmaz": ("cres", "stainless"),
    "aluminyum": ("aluminum", "aluminium"),
    "alasim": ("alloy",),
    "vida": ("screw",),
    "civata": ("bolt",),
    "somun": ("nut",),
    "percin": ("rivet",),
    "pul": ("washer",),
    "havsa": ("countersunk",),
    "sac": ("sheet",),
    "levha": ("sheet",),
    "plaka": ("plate",),
}
STOPWORDS = {"OR", "AND", "WITH", "FOR", "OF", "THE", "TO", "IN", "VE", "ILE"}

PHOTO_DIR = os.path.join(C.ROOT, "assets", "projects")
PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")

_empty_fig = C.empty_fig
_opts = C.opts

_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "iiissgguuoocc")


def _norm(v) -> str:
    """Case- and Turkish-accent-insensitive form used for search and matching."""
    return str(v).translate(_FOLD).lower()


def _n(v) -> str:
    return f"{int(v):,}".replace(",", " ")


def _pct(part, whole) -> str:
    return f"%{part / whole * 100:.0f}" if whole else "–"


def _label(code) -> str:
    return PROJECT_LABELS.get(code, code)


def _names(codes) -> str:
    """'HÜRJET' · 'HÜRJET ve GÖKBEY' · 'HÜRJET, GÖKBEY ve TLUH'."""
    labels = [_label(c) for c in codes]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " ve " + labels[-1]


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _tip(text: str, cls: str = "") -> dict:
    """Props for an element that explains itself on hover (see styles.css)."""
    return {"className": cls, "data-tip": text}


# --------------------------------------------------------------------------- #
# Data: one row per item, text columns + one bool column per project
# --------------------------------------------------------------------------- #
# items: text columns + "search", "n_proj" and one bool column per project;
# projects: project codes in export order. (No dataclass: Dash executes page
# modules without registering them in sys.modules, which dataclasses need.)
Catalog = namedtuple("Catalog", "items projects")


_TRUE = {"true", "1", "1.0", "x", "evet", "yes", "dogru", "var"}
_FALSE = {"", "false", "0", "0.0", "hayir", "no", "yanlis", "yok"}


def _as_bool(s: pd.Series) -> pd.Series | None:
    """A yes/no project column as bool; None when the column isn't yes/no."""
    txt = s.map(lambda v: "" if pd.isna(v) else _norm(v).strip())
    if not txt.isin(_TRUE | _FALSE).all():
        return None
    return txt.isin(_TRUE)


def _as_text(v) -> str:
    if pd.isna(v):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))                      # 6061.0 -> "6061"
    return str(v)


def _build(df: pd.DataFrame) -> Catalog:
    df = df.dropna(how="all").reset_index(drop=True)
    missing = [src for src in COLUMN_MAP if src not in df.columns]
    if missing:
        raise ValueError("Beklenen sütun(lar) bulunamadı: %s\nMevcut: %s"
                         % (missing, list(df.columns)))

    items = pd.DataFrame({dst: df[src].map(_as_text) for src, dst in COLUMN_MAP.items()})
    hay = items[TEXT_COLS[0]]
    for c in TEXT_COLS[1:]:
        hay = hay + " " + items[c]
    items["search"] = hay.str.translate(_FOLD).str.lower()
    for c in TEXT_COLS:
        items[c] = C.clean_code(items[c])       # blanks -> (Boş)

    projects = []
    for col in df.columns:
        if col in COLUMN_MAP or col.startswith("Unnamed") or df[col].isna().all():
            continue
        flags = _as_bool(df[col])
        if flags is not None:
            items[col] = flags.to_numpy()
            projects.append(col)
    if len(projects) < 2:
        raise ValueError("En az iki proje sütunu (doğru/yanlış) bekleniyor; bulunan: %s"
                         % projects)
    items["n_proj"] = items[projects].sum(axis=1).astype(int)
    return Catalog(items, projects)


get_data = C.make_loader(DATA_PATH, _build, skiprows=0)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _terms(query) -> list[str]:
    return [t for t in re.split(r"[\s,;]+", _norm(query or "")) if t]


def _filter(items, tips, branslar, alt_branslar, ureticiler, query):
    mask = pd.Series(True, index=items.index)
    for col, sel in (("tip", tips), ("brans", branslar),
                     ("alt_brans", alt_branslar), ("uretici", ureticiler)):
        if sel:
            mask &= items[col].isin(sel)
    for term in _terms(query):                  # every word must match
        hit = pd.Series(False, index=items.index)
        for alt in (term, *SYNONYMS.get(term, ())):
            hit |= items["search"].str.contains(alt, regex=False)
        mask &= hit
    return items[mask]


def _pick(projects, focus, compare):
    """A valid focus project and a non-empty list of other projects to compare with."""
    if focus not in projects:
        focus = DEFAULT_FOCUS if DEFAULT_FOCUS in projects else projects[0]
    if isinstance(compare, str):
        compare = [compare]
    compare = [p for p in dict.fromkeys(compare or []) if p in projects and p != focus]
    if not compare:
        compare = [next(p for p in (DEFAULT_FOCUS, DEFAULT_COMPARE, *projects)
                        if p in projects and p != focus)]
    return focus, compare


def _masks(dff, focus, compare):
    """Row masks: focus items bucketed by how many compared projects also use them."""
    f = dff[focus]
    k = dff[compare].sum(axis=1)
    n = len(compare)
    return {
        "focus": f,
        "hepsi": f & k.eq(n),
        "bazi": f & k.gt(0) & k.lt(n),
        "hic": f & k.eq(0),
        "unique": f & dff["n_proj"].eq(1),       # in no other project at all
    }


def _bucket_labels(compare) -> dict:
    if len(compare) == 1:
        cl = _label(compare[0])
        return {"hepsi": f"{cl} ile ortak", "hic": f"{cl} projesinde yok"}
    return {"hepsi": "Hepsinde ortak", "bazi": "Bazılarında var", "hic": "Hiçbirinde yok"}


def _bucket_tips(focus, compare) -> dict:
    fl, names = _label(focus), _names(compare)
    if len(compare) == 1:
        return {"hepsi": f"Hem {fl} hem {names} projesinde kullanılan kalemler.",
                "hic": f"{fl} projesinde kullanılan ama {names} projesinde "
                       f"kullanılmayan kalemler."}
    return {"hepsi": f"{fl} projesinde kullanılan ve {names} projelerinin hepsinde de "
                     f"bulunan kalemler.",
            "bazi": f"{fl} projesinde kullanılan; {names} projelerinin bazılarında bulunan "
                    f"ama hepsinde bulunmayan kalemler.",
            "hic": f"{fl} projesinde kullanılan ama {names} projelerinin hiçbirinde "
                   f"bulunmayan kalemler."}


def _filters_note(tips, branslar, alt_branslar, ureticiler, query) -> str:
    parts = [f"{label}: {', '.join(sel)}"
             for label, sel in (("Tip", tips), ("Branş", branslar),
                                ("Alt branş", alt_branslar), ("Üretici", ureticiler))
             if sel]
    if query and query.strip():
        parts.append(f"Arama: “{query.strip()}”")
    return "Filtre: " + " · ".join(parts) if parts else "Filtre yok, tüm kalemler sayılıyor."


def _slug(code) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm(code)).strip("-")


def _photo(code) -> str:
    """URL of the project's photo, or of the placeholder when there is none."""
    slug = _slug(code)
    for ext in PHOTO_EXTS:
        if os.path.exists(os.path.join(PHOTO_DIR, slug + ext)):
            return get_asset_url(f"projects/{slug}{ext}")
    return get_asset_url("projects/_placeholder.svg")


def _words(text):
    """Keywords of a designation: 'SHEET METALLIC-T6-7075' -> SHEET, METALLIC, T6, 7075;
    alloy codes such as A-286 stay whole."""
    for token in re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü][0-9A-Za-zÇĞİÖŞÜçğıöşü\-.]*", text):
        token = token.strip("-.").upper()
        parts = [token] if re.fullmatch(r"[A-Z]{1,2}-\d+", token) else token.split("-")
        for w in parts:
            w = w.strip(".")
            if len(w) >= 2 and w not in STOPWORDS:
                yield w


def _int_axis(max_value) -> dict:
    """Whole-number ticks on a 1-2-5 step (counts never get 0.5 ticks)."""
    raw = max(1.0, float(max_value) / 5)
    mag = 10 ** np.floor(np.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    return dict(tick0=0, dtick=step, tickformat=",d", rangemode="tozero")


def _title(text, sub=None) -> dict:
    title = dict(text=text)
    if sub:
        title["subtitle"] = dict(text=sub, font=dict(size=12, color=T.MUTED))
    return title


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def fig_combos(dff, focus, compare, height):
    """UpSet-style chart: items per exact combination of the selected projects."""
    sel = [focus, *compare]
    flags = dff[sel].to_numpy(dtype=bool)
    flags = flags[flags.any(axis=1)]
    if not len(flags):
        return _empty_fig("Seçilen projelerde filtreye uyan kalem yok")
    codes = flags.astype(np.int64) @ (1 << np.arange(len(sel)))     # bit i = sel[i]
    counts = pd.Series(codes).value_counts()
    full = (1 << len(sel)) - 1

    def rank(code):
        return -int(counts.get(code, 0)), -bin(code).count("1")

    shown = sorted(counts.index, key=rank)[:TOP_COMBOS]
    if full not in shown:                       # the all-selected combination always shows
        shown = sorted(shown[:TOP_COMBOS - 1] + [full], key=rank)
    values = [int(counts.get(c, 0)) for c in shown]
    members = [[i for i in range(len(sel)) if c >> i & 1] for c in shown]

    def colour(code):                           # same meaning as the answer bar
        if code == full:
            return T.SHARE_ALL
        if not code & 1:                        # focus project not in the combination
            return T.NEUTRAL
        return T.SHARE_NONE if code == 1 else T.SHARE_SOME

    labels = [_label(p) for p in sel]
    xs = list(range(len(shown)))
    bar_h, dots_h = 190, 26 * len(sel) + 10
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[bar_h / (bar_h + dots_h), dots_h / (bar_h + dots_h)])
    fig.add_bar(
        x=xs, y=values, width=min(0.6, 24 * len(xs) / 800),   # <= ~24px on a ~800px plot
        marker=dict(color=[colour(c) for c in shown], line=dict(width=0)),
        hovertext=[f"<b>{_n(v)}</b> kalem<br>{' + '.join(labels[i] for i in mem)}"
                   f"<br>(seçilenler arasında yalnız bu projelerde)"
                   for v, mem in zip(values, members)],
        hovertemplate="%{hovertext}<extra></extra>", row=1, col=1)
    fi = shown.index(full)
    fig.add_annotation(x=fi, y=values[fi], text=f"<b>{_n(values[fi])}</b>", yshift=11,
                       showarrow=False, font=dict(size=12, color=T.INK), row=1, col=1)
    for j, mem in enumerate(members):           # connector through the member dots
        if len(mem) > 1:
            fig.add_scatter(x=[j, j], y=[sel[mem[0]], sel[mem[-1]]], mode="lines",
                            line=dict(color=T.INK, width=2), hoverinfo="skip",
                            showlegend=False, row=2, col=1)
    fig.add_scatter(
        x=[j for j in xs for _ in sel], y=[p for _ in xs for p in sel], mode="markers",
        marker=dict(size=11, color=[T.INK if c >> i & 1 else T.HAIRLINE
                                    for c in shown for i in range(len(sel))]),
        hoverinfo="skip", showlegend=False, row=2, col=1)
    fig.update_xaxes(showticklabels=False, showgrid=False, zeroline=False, ticks="",
                     showline=False, range=[-0.6, len(xs) - 0.4])
    fig.update_yaxes(title="Kalem sayısı", showgrid=True, gridcolor=T.HAIRLINE,
                     **_int_axis(max(values)), row=1, col=1)
    fig.update_yaxes(type="category", categoryorder="array", categoryarray=sel,
                     autorange="reversed", showgrid=False, ticks="", tickvals=sel,
                     ticktext=[f"<b>{s}</b>" if i == 0 else s for i, s in enumerate(labels)],
                     row=2, col=1)
    fig.update_layout(
        title=_title("Seçilen projelerin kombinasyonları",
                     "Çubuk: seçilenler arasında yalnız işaretli projelerde kullanılan kalemler"),
        showlegend=False, barcornerradius=4, separators=". ",
        margin=dict(l=10, r=16, t=76, b=16))
    return T.style_fig(fig, height=height)


def fig_breakdown(dff, dim, focus, compare, height):
    fl = _label(focus)
    m = _masks(dff, focus, compare)
    names = _bucket_labels(compare)
    keys = [k for k, *_ in BUCKETS if k in names]
    parts = pd.DataFrame({"key": dff[dim], **{k: m[k] for k in keys}})[m["focus"]]
    if parts.empty:
        return _empty_fig(f"{fl} projesinde filtreye uyan kalem yok")
    g = parts.groupby("key")[keys].sum()
    g["total"] = g.sum(axis=1)
    g = g.sort_values(["total", "hepsi"], ascending=False)
    shown = g.head(TOP_GROUPS).iloc[::-1]       # largest bar on top
    plot_h = height - 100 - 44                  # figure minus top/bottom margins
    bar_w = min(0.62, 22 * len(shown) / max(plot_h, 1))   # bars stay <= ~22px thick

    fig = go.Figure()
    for key, color, _ in BUCKETS:
        if key not in names:
            continue
        fig.add_bar(
            y=shown.index, x=shown[key], name=names[key], orientation="h", width=bar_w,
            marker=dict(color=color, line=dict(color=T.WHITE, width=2)),
            text=shown[key] if key == "hepsi" else None,
            textposition="inside", insidetextanchor="middle", textangle=0,
            textfont=dict(color=T.WHITE, size=12),
            hovertemplate="<b>%{x}</b> kalem · " + names[key] + "<br>%{y}<extra></extra>")
    sub = f"Karşılaştırılan: {_names(compare)}"
    if len(g) > TOP_GROUPS:
        sub += f" · en çok kalem içeren ilk {TOP_GROUPS} grup"
    fig.update_layout(
        title=_title(f"{BREAKDOWNS[dim]} bazında {fl} kalemleri", sub),
        barmode="stack", barcornerradius=4,
        # legend on its own line under the subtitle, so a long subtitle never runs into it
        legend=dict(traceorder="normal", x=0, xanchor="left", y=1.0, yanchor="bottom"),
        xaxis=dict(title="Kalem sayısı", showgrid=True, gridcolor=T.HAIRLINE, ticks="",
                   **_int_axis(shown["total"].max())),
        yaxis=dict(type="category", showgrid=False, ticks=""),
        uniformtext=dict(minsize=10, mode="hide"),
        separators=". ", margin=dict(l=10, r=24, t=100, b=44), uirevision="keep")
    return T.style_fig(fig, height=height)


def fig_sharing(dff, focus, n_projects, height):
    fl = _label(focus)
    shared = dff.loc[dff[focus], "n_proj"]
    if shared.empty:
        return _empty_fig(f"{fl} projesinde filtreye uyan kalem yok")
    counts = shared.value_counts().reindex(range(1, n_projects + 1), fill_value=0)
    fig = go.Figure(go.Bar(
        x=[str(k) for k in counts.index], y=counts.values,
        marker=dict(color=T.SHARE_ALL, line=dict(width=0)),
        hovertemplate="<b>%{y}</b> kalem<br>%{x} projede kullanılıyor<extra></extra>"))
    fig.update_layout(
        title=_title(f"{fl} kalemleri kaç projede kullanılıyor?",
                     f"1 = yalnız {fl} projesinde"),
        bargap=0.45, barcornerradius=4, showlegend=False,
        xaxis=dict(title="Kullanıldığı proje sayısı", type="category"),
        yaxis=dict(title="Kalem sayısı", **_int_axis(counts.max())),
        separators=". ", margin=dict(l=10, r=16, t=76, b=44))
    return T.style_fig(fig, height=height)


def fig_matrix(dff, projects, focus, compare, height):
    m = dff[projects].to_numpy(dtype=np.int64)
    inter = m.T @ m                             # [i, j] = items used by both i and j
    totals = np.diag(inter).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = np.where(totals[:, None] > 0, inter / totals[:, None] * 100.0, np.nan)
    np.fill_diagonal(pct, np.nan)

    # Columns run in reverse, so a project's own (empty) cell runs from the
    # bottom-left corner to the top-right one, next to its axis labels.
    cols = projects[::-1]
    order = [projects.index(p) for p in cols]
    pct, inter = pct[:, order], inter[:, order]
    text = [["" if np.isnan(v) else f"%{v:.0f}" for v in row] for row in pct]
    hover = [[f"<b>{_n(inter[i, j])}</b> ortak kalem<br>{_label(r)} → {_label(c)}"
              f"<br>{_label(r)} içindeki payı: {text[i][j] or '–'}"
              for j, c in enumerate(cols)] for i, r in enumerate(projects)]
    selected = {focus, *compare}

    def tick(p, s):
        return f"<b>{s}</b>" if p in selected else s

    fig = go.Figure(go.Heatmap(
        z=pct, x=cols, y=projects, text=text, texttemplate="%{text}",
        textfont=dict(size=12), hovertext=hover, hovertemplate="%{hovertext}<extra></extra>",
        hoverongaps=False, zmin=0, zmax=100, xgap=2, ygap=2,
        colorscale=[[0, T.BLUE_FAINT], [1, T.BLUE]],
        colorbar=dict(thickness=10, len=0.9, outlinewidth=0, tickvals=[0, 50, 100],
                      ticktext=["%0", "%50", "%100"], tickfont=dict(size=11))))
    fig.update_layout(
        xaxis=dict(type="category", tickvals=cols, showgrid=False, ticks="",
                   ticktext=[tick(p, _label(p)) for p in cols]),
        yaxis=dict(type="category", tickvals=projects, showgrid=False, ticks="",
                   autorange="reversed",
                   ticktext=[tick(p, f"{_label(p)} · {_n(n)}") for p, n in zip(projects, totals)]),
        margin=dict(l=10, r=10, t=12, b=40))
    fi = projects.index(focus)
    for c in compare:                           # outline focus row x each compared column
        ci = cols.index(c)
        fig.add_shape(type="rect", x0=ci - 0.5, x1=ci + 0.5, y0=fi - 0.5, y1=fi + 0.5,
                      line=dict(color=T.INK, width=2))
    return T.style_fig(fig, height=height)


def fig_words(dff, height):
    counts = Counter()
    for text in dff.loc[dff["tanim"] != C.EMPTY_LABEL, "tanim"]:
        counts.update(set(_words(text)))
    top = counts.most_common(TOP_WORDS)
    if not top:
        return _empty_fig("Tanım bulunamadı")
    words, values = zip(*top[::-1])            # most frequent on top
    fig = go.Figure(go.Bar(
        x=values, y=words, orientation="h",
        marker=dict(color=T.SHARE_ALL, line=dict(width=0)),
        hovertemplate="<b>%{x}</b> kalem<br>%{y}<extra></extra>"))
    fig.update_layout(
        title=_title("Tanımda sık geçen kelimeler", "Tıklanan kelime aramaya eklenir"),
        bargap=0.4, barcornerradius=4, showlegend=False,
        xaxis=dict(title="Kalem sayısı", showgrid=True, gridcolor=T.HAIRLINE, ticks="",
                   **_int_axis(max(values))),
        yaxis=dict(type="category", showgrid=False, ticks=""),
        separators=". ", margin=dict(l=10, r=16, t=76, b=44))
    return T.style_fig(fig, height=height)


# --------------------------------------------------------------------------- #
# HTML blocks (project cards, answer, KPIs, table)
# --------------------------------------------------------------------------- #
def _project_cards(dff, projects, focus, compare):
    f = dff[focus]
    nf, fl = int(f.sum()), _label(focus)
    cards = []
    for i, p in enumerate(projects):
        role = "focus" if p == focus else "compare" if p in compare else ""
        pl = _label(p)
        edge = " tip-end" if i >= len(projects) - 2 else ""   # keep tooltips on screen
        count = html.Span([html.B(_n(dff[p].sum())), " kalem"], **_tip(
            f"{pl} projesinde kullanılan, filtrelere uyan kalem sayısı.",
            "pcard-count tip-up" + edge))
        if p == focus:
            note = [html.Span("Odak proje", className="pcard-note")]
        else:
            both = int((f & dff[p]).sum())
            share = both / nf * 100 if nf else 0
            note = [
                html.Span(className="meter", children=html.Span(
                    className="meter-fill", style={"width": f"{share:.0f}%"})),
                html.Span([f"{fl} ile ortak: ", html.B(_n(both)), f" ({_pct(both, nf)})"],
                          **_tip(f"{fl} kalemlerinden {both} tanesi {pl} projesinde de "
                                 f"kullanılıyor. Parantezdeki yüzde, bu sayının {fl} "
                                 f"kalemleri içindeki payıdır.", "pcard-note tip-up" + edge)),
            ]
        photo = [html.Span(pl, className="pcard-name")]
        badge = {"focus": "ODAK", "compare": "KARŞILAŞTIRMA"}.get(role)
        if badge:
            photo.insert(0, html.Span(badge, className="pcard-badge"))
        if role == "compare":
            button, cls, off = (("Çıkar", "pcard-cmp remove", False) if len(compare) > 1
                                else ("Karşılaştırılıyor", "pcard-cmp", True))
        else:
            button, cls, off = "+ Karşılaştır", "pcard-cmp", role == "focus"

        # Pattern IDs use the ASCII slug: Dash can't map a non-ASCII id (GÖKBEY)
        # back to its value in ctx.triggered, so clicks on it would be lost.
        cards.append(html.Div(className=f"pcard {role}".strip(), children=[
            html.Button(id={"type": "d4-card", "index": _slug(p)}, n_clicks=0,
                        className="pcard-main", children=[
                html.Span(className="pcard-photo", children=photo,
                          style={"backgroundImage": f"url('{_photo(p)}')"}),
                html.Span(className="pcard-body", children=[count, *note]),
            ]),
            html.Button(button, id={"type": "d4-cmp", "index": _slug(p)}, n_clicks=0,
                        className=cls, disabled=off),
        ]))
    return cards


def _answer(dff, focus, compare, note):
    fl, names, many = _label(focus), _names(compare), len(compare) > 1
    n = {k: int(v.sum()) for k, v in _masks(dff, focus, compare).items()}
    in_any = n["hepsi"] + n["bazi"]
    if n["focus"] == 0:
        text = [html.B(fl), " projesinde filtreye uyan kalem yok."]
    else:
        text = [html.B(fl), " projesinde ", html.B(_n(n["focus"])), " kalem kullanılıyor. "]
        where = [html.B(names), " projelerinin" if many else " projesinde"]
        if n["hepsi"]:
            text += ["Bunlardan ", html.B(_n(n["hepsi"])), " tanesi (",
                     html.B(_pct(n["hepsi"], n["focus"])), ") ", *where,
                     " hepsinde de kullanılıyor" if many else " de kullanılıyor"]
            if many and in_any > n["hepsi"]:
                text += [", ", html.B(_n(in_any)), " tanesi (", html.B(_pct(in_any, n["focus"])),
                         ") ise en az birinde"]
            text.append(".")
        elif many and in_any:
            text += ["Bunların hiçbiri ", *where, " hepsinde birden kullanılmıyor; ",
                     html.B(_n(in_any)), " tanesi (", html.B(_pct(in_any, n["focus"])),
                     ") en az birinde kullanılıyor."]
        else:
            text += ["Bunların hiçbiri ", html.B(names),
                     " projelerinde" if many else " projesinde", " kullanılmıyor."]

    labels, tips = _bucket_labels(compare), _bucket_tips(focus, compare)
    buckets = [(k, color, ink) for k, color, ink in BUCKETS if k in labels]
    total = sum(n[k] for k, *_ in buckets)
    children = [html.P(text, className="answer-text")]
    if total:
        children.append(html.Div(className="ovl-bar", children=[
            html.Button(_n(n[k]) if n[k] / total >= 0.14 else "",
                        id={"type": "d4-seg", "index": k}, n_clicks=0,
                        className=f"ovl-seg {ink}".strip(),
                        style={"flexGrow": n[k], "background": color})
            for k, color, ink in buckets if n[k]
        ]))
    children.append(html.Div(className="ovl-legend", children=[
        html.Span(**_tip(tips[k], "ovl-key"), children=[
            html.I(style={"background": color}), labels[k], html.B(_n(n[k]))])
        for k, color, _ in buckets
    ]))
    children.append(html.Div(note, className="filters-note"))
    return children


def _kpi(value, label, sub, tip, cls=""):
    return html.Div(**_tip(tip, f"kpi-wrap {cls}".strip()), children=html.Div(
        className="kpi", children=[
            html.Div(value, className="kpi-val"),
            html.Div(label, className="kpi-lbl"),
            html.Div(sub, className="kpi-sub"),
        ]))


def _kpis(dff, focus, compare, n_projects):
    fl, names = _label(focus), _names(compare)
    in_focus = dff[dff[focus]]
    unique = int(in_focus["n_proj"].eq(1).sum())
    avg = C.fmt(in_focus["n_proj"].mean(), 1) if len(in_focus) else "–"
    in_compare = int(dff[compare].any(axis=1).sum())
    specs = in_focus.loc[in_focus["spec"] != C.EMPTY_LABEL, "spec"].nunique()   # blanks aren't a spec
    avg_tip = (f"{fl} kalemlerinin ortalama kaç projede kullanıldığı ({fl} dahil)."
               + (f" {avg}, bir {fl} kaleminin ortalamada {avg} projede kullanıldığı "
                  f"anlamına gelir; sayı büyüdükçe ortaklık artar." if len(in_focus) else ""))
    if len(compare) == 1:
        cmp_kpi = _kpi(_n(in_compare), f"{names} projesinde", "kalem · karşılaştırılan",
                       f"Karşılaştırılan {names} projesinde kullanılan, filtrelere uyan "
                       f"kalem sayısı.")
    else:
        cmp_kpi = _kpi(_n(in_compare), "Karşılaştırılan projelerde",
                       f"en az birinde · {len(compare)} proje",
                       f"{names} projelerinden en az birinde kullanılan, filtrelere uyan "
                       f"kalem sayısı.")
    return [
        _kpi(_n(len(in_focus)), f"{fl} projesinde",
             f"kalem · {_n(specs)} spesifikasyon",
             f"Filtrelere uyan ve {fl} projesinde kullanılan kalem sayısı. Alt satırdaki "
             f"sayı, bu kalemlerin kaç farklı spesifikasyona ait olduğunu gösterir."),
        _kpi(_n(unique), f"Yalnız {fl} projesinde", "başka hiçbir projede yok",
             f"{fl} projesinde kullanılan ama başka hiçbir projede kullanılmayan kalem "
             f"sayısı; yani yalnız {fl} projesine özgü kalemler."),
        _kpi(avg, "Ortalama paylaşım", f"proje / {fl} kalemi", avg_tip),
        cmp_kpi,
        _kpi(_n(len(dff)), "Toplam kalem", f"filtreye uyan · {n_projects} proje",
             f"Filtrelere uyan tüm kalemlerin sayısı; hangi projede kullanıldığına "
             f"bakılmaksızın. Dosyada {n_projects} proje sütunu var.", "tip-end"),
    ]


def _scope_options(focus, compare):
    fl, labels = _label(focus), _bucket_labels(compare)
    return ([{"label": "Tümü", "value": "all"},
             {"label": f"{fl} projesinde", "value": "focus"}]
            + [{"label": labels[k], "value": k} for k, *_ in BUCKETS if k in labels]
            + [{"label": f"Yalnız {fl} projesinde", "value": "unique"}])


def _table_styles(focus, compare):
    tints = [(focus, _rgba(T.SHARE_ALL, 0.10))] + [(c, _rgba(T.GOLD, 0.16)) for c in compare]
    header = [{"if": {"column_id": p}, "backgroundColor": tint} for p, tint in tints]
    data = [{"if": {"column_id": p}, "backgroundColor": tint} for p, tint in tints]
    return header, data


def _ctrl(label, control, cls="ctrl"):
    return html.Div(className=cls, children=[html.Label(label), control])


def _dropdown(**props):
    return dcc.Dropdown(labels=C.DROPDOWN_LABELS, **props)


def _graph(graph_id):
    """A chart that re-fits its card whenever the page or iframe resizes. In
    Dash's responsive mode the height comes from `style` (set by `update`),
    not from the figure; without it charts keep their first-render width."""
    return dcc.Graph(id=graph_id, responsive=True, style={"height": "400px"})


def _height(px) -> dict:
    return {"height": f"{int(px)}px"}


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def serve_layout():
    try:
        cat = get_data()
    except (FileNotFoundError, ValueError) as exc:
        return C.placeholder_layout(TITLE, desc=str(exc))
    items, projects = cat.items, cat.projects
    focus, compare = _pick(projects, None, None)
    project_opts = [{"label": _label(p), "value": p} for p in projects]

    return html.Div(className="app d4", lang="tr", children=[
        C.brand_header(TITLE, status=html.Div(id="d4-status")),

        html.Section(className="controls", children=[
            _ctrl("Odak proje", _dropdown(id="d4-focus", clearable=False,
                                          options=project_opts, value=focus)),
            _ctrl("Karşılaştırılan projeler", _dropdown(
                id="d4-compare", multi=True, clearable=False, options=project_opts,
                value=compare)),
            _ctrl("Tip", _dropdown(id="d4-f-tip", multi=True, placeholder="Tümü",
                                   options=_opts(items["tip"]))),
            _ctrl("Branş", _dropdown(id="d4-f-brans", multi=True, placeholder="Tümü",
                                     options=_opts(items["brans"]))),
            _ctrl("Alt branş", _dropdown(id="d4-f-altbrans", multi=True, placeholder="Tümü",
                                         options=_opts(items["alt_brans"]))),
            _ctrl("Üretici", _dropdown(id="d4-f-uretici", multi=True, placeholder="Tümü",
                                       options=_opts(items["uretici"]))),
            _ctrl("Kırılım", _dropdown(id="d4-breakdown", clearable=False,
                                       options=[{"label": v, "value": k}
                                                for k, v in BREAKDOWNS.items()],
                                       value=DEFAULT_BREAKDOWN)),
            _ctrl("Arama", dcc.Input(id="d4-search", type="search", value="", debounce=0.4,
                                     placeholder="Örn. titanium, A-286, AMS4027 …",
                                     className="search"),
                  cls="ctrl ctrl-wide"),
            html.Div(className="ctrl ctrl-btn", children=[
                html.Button("Temizle", id="d4-clear", n_clicks=0, className="btn ghost"),
            ]),
        ]),

        html.Div("Projeler", className="section-label"),
        html.Section(id="d4-projects", className="pcards"),

        html.Section(className="row", children=[
            html.Div(id="d4-answer", className="card full answer"),
        ]),

        html.Section(id="d4-kpis", className="kpis"),

        html.Section(className="row grid-3", children=[
            html.Div(className="card span-2", children=[_graph("d4-g-combos")]),
            html.Div(className="card", children=[_graph("d4-g-sharing")]),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[_graph("d4-g-breakdown")]),
        ]),

        html.Section(className="row grid-3", children=[
            html.Div(className="card span-2", children=[
                html.Div(className="card-head", children=[
                    html.H3("Projeler arası ortaklık"),
                    html.Span("Satırdaki projenin kalemlerinden sütundaki projede de "
                              "kullanılanların oranı", className="hint"),
                ]),
                _graph("d4-g-matrix"),
            ]),
            html.Div(className="card", children=[_graph("d4-g-words")]),
        ]),

        html.Section(className="row", children=[
            html.Div(className="card full", children=[
                html.Div(className="card-head", children=[
                    html.H3(id="d4-table-title", children="Kalem listesi"),
                    html.Span("Sütun başlığına tıklayarak sıralayın · filtre satırına "
                              "yazın · dışa aktarın →", className="hint"),
                ]),
                dcc.RadioItems(id="d4-scope", className="scope", value="all",
                               options=_scope_options(focus, compare)),
                dash_table.DataTable(
                    id="d4-table", sort_action="native", filter_action="native",
                    filter_options={"case": "insensitive", "placeholder_text": "filtrele…"},
                    page_size=15,
                    export_format="xlsx", export_headers="display",
                    style_as_list_view=True, style_table={"overflowX": "auto"},
                    style_header={"backgroundColor": T.LIGHT, "fontWeight": "700",
                                  "color": T.INK, "border": "none",
                                  "borderBottom": f"2px solid {T.HAIRLINE}",
                                  "fontFamily": T.FONT_FAMILY, "textAlign": "center"},
                    style_filter={"fontFamily": T.FONT_FAMILY, "fontSize": "12px"},
                    style_cell={"fontFamily": T.FONT_FAMILY, "fontSize": "13px",
                                "padding": "9px 12px", "border": "none",
                                "borderBottom": f"1px solid {T.HAIRLINE}",
                                "color": T.INK, "textAlign": "center", "minWidth": "60px"},
                    style_cell_conditional=[
                        {"if": {"column_id": c}, "textAlign": "left",
                         "minWidth": f"{w}px", "maxWidth": f"{w + 60}px",
                         "whiteSpace": "nowrap" if c in ("tip", "spec") else "normal"}
                        for c, w in TABLE_WIDTHS.items()
                    ],
                ),
                dcc.Store(id=TABLE_SORT),
            ]),
        ]),

        html.Footer(className="foot", children=[f"{C.COMPANY_NAME} · {TITLE}"]),
    ])


layout = serve_layout  # callable -> data refreshes on each navigation
TABLE_SORT = C.two_way_sort("d4-table")  # header clicks: ascending <-> descending


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
FILTER_INPUTS = (
    Input("d4-f-tip", "value"),
    Input("d4-f-brans", "value"),
    Input("d4-f-altbrans", "value"),
    Input("d4-f-uretici", "value"),
    Input("d4-search", "value"),
)


@callback(
    Output("d4-status", "children"),
    Output("d4-projects", "children"),
    Output("d4-answer", "children"),
    Output("d4-kpis", "children"),
    Output("d4-g-combos", "figure"),
    Output("d4-g-sharing", "figure"),
    Output("d4-g-breakdown", "figure"),
    Output("d4-g-matrix", "figure"),
    Output("d4-g-words", "figure"),
    Output("d4-g-combos", "style"),
    Output("d4-g-sharing", "style"),
    Output("d4-g-breakdown", "style"),
    Output("d4-g-matrix", "style"),
    Output("d4-g-words", "style"),
    Output("d4-focus", "options"),
    Output("d4-compare", "options"),
    Output("d4-f-tip", "options"),
    Output("d4-f-brans", "options"),
    Output("d4-f-altbrans", "options"),
    Output("d4-f-uretici", "options"),
    Input("d4-focus", "value"),
    Input("d4-compare", "value"),
    *FILTER_INPUTS,
    Input("d4-breakdown", "value"),
)
def update(focus, compare, tips, branslar, alt_branslar, ureticiler, query, breakdown):
    cat = get_data()
    items, projects = cat.items, cat.projects
    focus, compare = _pick(projects, focus, compare)
    dff = _filter(items, tips, branslar, alt_branslar, ureticiler, query)
    note = _filters_note(tips, branslar, alt_branslar, ureticiler, query)

    combos_h = 76 + 190 + 26 * (1 + len(compare)) + 50
    n_groups = min(TOP_GROUPS, dff.loc[dff[focus], breakdown].nunique())
    breakdown_h = max(340, 144 + 26 * n_groups)   # full-width row; 12 groups -> 456px
    matrix_h = max(380, 70 + 40 * len(projects))

    status = (f"{_n(len(dff))} / {_n(len(items))} kalem  ·  "
              f"kaynak: {os.path.basename(DATA_PATH)}")
    project_opts = [{"label": _label(p), "value": p} for p in projects]
    compare_opts = [{**o, "disabled": o["value"] == focus} for o in project_opts]
    return (
        status,
        _project_cards(dff, projects, focus, compare),
        _answer(dff, focus, compare, note),
        _kpis(dff, focus, compare, len(projects)),
        fig_combos(dff, focus, compare, combos_h),
        fig_sharing(dff, focus, len(projects), combos_h),     # same row as the combos chart
        fig_breakdown(dff, breakdown, focus, compare, breakdown_h),
        fig_matrix(dff, projects, focus, compare, matrix_h),
        fig_words(dff, matrix_h + 44),          # + the matrix card's heading
        _height(combos_h), _height(combos_h), _height(breakdown_h),
        _height(matrix_h), _height(matrix_h + 44),
        project_opts, compare_opts,
        _opts(items["tip"]), _opts(items["brans"]),
        _opts(items["alt_brans"]), _opts(items["uretici"]),
    )


@callback(
    Output("d4-table", "data"),
    Output("d4-table", "columns"),
    Output("d4-table", "style_header_conditional"),
    Output("d4-table", "style_data_conditional"),
    Output("d4-scope", "options"),
    Output("d4-table-title", "children"),
    Input("d4-focus", "value"),
    Input("d4-compare", "value"),
    *FILTER_INPUTS,
    Input("d4-scope", "value"),
)
def update_table(focus, compare, tips, branslar, alt_branslar, ureticiler, query, scope):
    cat = get_data()
    projects = cat.projects
    focus, compare = _pick(projects, focus, compare)
    dff = _filter(cat.items, tips, branslar, alt_branslar, ureticiler, query)
    masks = _masks(dff, focus, compare)
    view = dff[masks[scope]] if scope in masks else dff

    out = view[TEXT_COLS].copy()
    for p in projects:
        out[p] = np.where(view[p], "✓", "")
    out["n_proj"] = view["n_proj"]
    columns = ([{"name": COLUMN_LABELS[c], "id": c} for c in TEXT_COLS]
               + [{"name": "Proje sayısı", "id": "n_proj", "type": "numeric"}]
               + [{"name": _label(p), "id": p} for p in projects])
    header_styles, data_styles = _table_styles(focus, compare)
    title = f"Kalem listesi · {_n(len(view))} kalem"
    return (out.to_dict("records"), columns, header_styles, data_styles,
            _scope_options(focus, compare), title)


@callback(
    Output("d4-focus", "value"),
    Output("d4-compare", "value"),
    Output("d4-scope", "value", allow_duplicate=True),
    Input({"type": "d4-card", "index": ALL}, "n_clicks"),
    Input({"type": "d4-cmp", "index": ALL}, "n_clicks"),
    Input("d4-g-matrix", "clickData"),
    Input("d4-focus", "value"),
    Input("d4-compare", "value"),
    State("d4-scope", "value"),
    prevent_initial_call=True,
)
def _sync_selection(_cards, _cmps, matrix_click, focus, compare, scope):
    """Cards, the matrix and both dropdowns drive one focus project plus a
    non-empty list of other projects to compare it with."""
    projects = get_data().projects
    compare = [compare] if isinstance(compare, str) else list(compare or [])
    before = (focus, compare)
    trig = ctx.triggered_id
    if isinstance(trig, dict):
        if not ctx.triggered[0]["value"]:       # re-rendered card, not a click
            return no_update, no_update, no_update
        p = {_slug(code): code for code in projects}.get(trig["index"])
        if p is None:
            return no_update, no_update, no_update
        if trig["type"] == "d4-card":           # new focus; old focus takes its place
            if p != focus:
                compare = [focus if c == p else c for c in compare]
                focus = p
        elif p in compare:                      # "Çıkar": the last one stays
            if len(compare) == 1:
                return no_update, no_update, no_update
            compare = [c for c in compare if c != p]
        elif p != focus:                        # "+ Karşılaştır"
            compare = compare + [p]
    elif trig == "d4-g-matrix":
        point = ((matrix_click or {}).get("points") or [{}])[0]
        row, col = point.get("y"), point.get("x")
        if not row or not col or row == col:
            return no_update, no_update, no_update
        focus, compare = row, [col]
    focus, compare = _pick(projects, focus, compare)
    new_scope = "all" if scope == "bazi" and len(compare) == 1 else no_update
    if (focus, compare) == before:
        return no_update, no_update, new_scope
    return focus, compare, new_scope


@callback(
    Output("d4-scope", "value"),
    Input({"type": "d4-seg", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _scope_from_bar(_clicks):
    if not ctx.triggered_id or not ctx.triggered[0]["value"]:
        return no_update
    return ctx.triggered_id["index"]


@callback(
    Output("d4-f-tip", "value"),
    Output("d4-f-brans", "value"),
    Output("d4-f-altbrans", "value"),
    Output("d4-f-uretici", "value"),
    Output("d4-search", "value"),
    Output("d4-scope", "value", allow_duplicate=True),
    Input("d4-clear", "n_clicks"),
    Input("d4-g-words", "clickData"),
    State("d4-search", "value"),
    prevent_initial_call=True,
)
def _filter_shortcuts(_clear, word_click, query):
    """'Temizle' resets every filter and the table view; a keyword bar is
    appended to the search."""
    if ctx.triggered_id == "d4-clear":
        return None, None, None, None, "", "all"
    word = (((word_click or {}).get("points") or [{}])[0]).get("y")
    if not word or _norm(word) in _terms(query):
        return (no_update,) * 6
    return no_update, no_update, no_update, no_update, f"{query or ''} {word}".strip(), no_update
