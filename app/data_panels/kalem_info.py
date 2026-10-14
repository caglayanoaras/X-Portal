# -*- coding: utf-8 -*-
"""
"Kalem nedir?": a visual explainer of what one kalem is, per item kind.

Panel 4 shows it as a collapsible card right under its answer card. Everything
it says is data in KALEM_KINDS below: the parts that make one kalem, with an
example value for each, and how that example looks in the item table. Edit the
data; the layout follows. Example values are generic (public specs), never real
items. Component IDs start "d4-kx-".
"""
from dash import Input, Output, callback, dcc, get_asset_url, html

INTRO = ("Kalem, projelerde kullanılan tek bir malzeme ya da parçadır. Bu panodaki bütün "
         "sayılar kalem sayısıdır. İki proje aynı kalemi kullanıyorsa o kalem bu projelerde "
         "ortaktır.")

# Shown under every kind: what does NOT make a different kalem.
NOT_PARTS = [
    ("Üretici", "bir kalemin birden çok üreticisi olabilir, hepsi aynı satırda yazılır."),
    ("Proje", "aynı kalemi kullanan projeler o kalemi ortak kullanır."),
]

# One entry per tab:
# - parts: what makes one kalem, in order: (key, English label, example value,
#   Turkish meaning, colour). Colours: blue, navy, soft, gray, gold (size is
#   always gold).
# - table: how the example shows in the item table: (column, [(text, part key
#   or None), ...]); a part key underlines the text in that part's colour (plain
#   text while that part is commented out).
KALEM_KINDS = [
    {
        "key": "metal", "tab": "Metal", "art": "kalem/metal.svg",
        "parts": [
            ("spec", "Specification", "AMS4461", "uyduğu standart", "blue"),
            ("type", "Material type", "2024-CLAD", "alaşımı", "navy"),
            ("cond", "Condition", "O", "ısıl işlem durumu", "soft"),
            ("form", "Form", "SHEET METALLIC", "şekli: sac", "gray"),
            ("size", "Dimension", "a=1.6 mm", "ölçüsü: 1.6 mm kalınlık", "gold"),
        ],
        "table": [
            ("Spesifikasyon", [("AMS4461", "spec")]),
            ("Tanım", [("SHEET METALLIC", "form"), ("-", None), ("O", "cond"), ("-", None),
                       ("2024-CLAD", "type")]),
            ("Boyut", [("a=1.6 mm", "size")]),
        ],
    },
    {
        "key": "nonmetal", "tab": "Non-Metal", "art": "kalem/nonmetal.svg",
        "parts": [
            ("spec", "Specification", "MIL-PRF-23377", "uyduğu standart", "blue"),
            # hidden for now; uncomment to show again (with the Boyut cell below)
            # ("type", "Material type", "PRIMER", "ne olduğu: astar boya", "navy"),
            ("desc", "Designation", "EPOXY-CORROSION INHIBITING", "içeriği ve özelliği", "soft"),
            # ("size", "Dimension", "—", "çoğu zaman yok", "gold"),
        ],
        "table": [
            ("Spesifikasyon", [("MIL-PRF-23377", "spec")]),
            ("Tanım", [("PRIMER", "type"), ("-", None), ("EPOXY-CORROSION INHIBITING", "desc")]),
            # ("Boyut", [("boş", "size")]),
        ],
    },
    {
        "key": "standard", "tab": "Standart parça", "art": "kalem/standard.svg",
        "parts": [
            ("spec", "Standard", "MS24694", "uyduğu parça standardı", "blue"),
            ("mat", "Material", "CRES (A-286)", "paslanmaz çelik", "navy"),
            ("part", "Part type", "SCREW, COUNTERSUNK", "havşa başlı vida", "soft"),
            ("finish", "Finish & Lub.", "PASSIVATE PER AMS2700", "yüzey işlemi ve yağlama", "gray"),
            ("size", "Dimension", "0.003 - 0.5", "ölçü aralığı", "gold"),
        ],
        "table": [
            ("Spesifikasyon", [("MS24694", "spec")]),
            ("Tanım", [("CRES (A-286)", "mat"), (" -", None), ("SCREW, COUNTERSUNK", "part")]),
            ("Boyut", [("0.003 - 0.5", "size")]),
        ],
    },
]
_BY_KEY = {kind["key"]: kind for kind in KALEM_KINDS}


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #
def _formula(parts):
    """part + part + ... = 1 kalem"""
    out = []
    for i, (_, label, value, meaning, colour) in enumerate(parts):
        if i:
            out.append(html.Span("+", className="kx-op"))
        out.append(html.Div(className=f"kx-chip k-{colour}", children=[
            # English casing for the upper-case label: "i" -> "I", not the Turkish "İ"
            html.Span(label, className="kx-chip-label", lang="en"),
            html.Span(value, className="kx-chip-value"),
            html.Span(meaning, className="kx-chip-means"),
        ]))
    out += [html.Span("=", className="kx-op"),
            html.Div(className="kx-result", children=[html.B("1"), html.Span("kalem")])]
    return html.Div(out, className="kx-formula")


def _in_table(kind):
    colours = {key: colour for key, *_, colour in kind["parts"]}
    cells = [html.Span(className="kx-cell", children=[
        html.Span(column, className="kx-col"),
        html.Span([html.Span(text, className=f"kx-seg k-{colours[key]}") if key in colours
                   else text for text, key in segments], className="kx-val"),
    ]) for column, segments in kind["table"]]
    return html.Div([html.Span("Tabloda", className="kx-table-label"), *cells],
                    className="kx-table")


def _panel(kind):
    return html.Div(className="kx-panel", children=[
        html.Div(className="kx-main", children=[
            html.Img(src=get_asset_url(kind["art"]), className="kx-art", alt=""),
            _formula(kind["parts"]),
        ]),
        _in_table(kind),
    ])


def explainer():
    """The collapsible card (closed until clicked)."""
    first = KALEM_KINDS[0]
    return html.Details(className="kx", children=[
        html.Summary(className="kx-summary", children=[
            html.Span("?", className="kx-icon"),
            html.Span(className="kx-summary-text", children=[
                html.B("Kalem nedir?"),
                html.Span("Panodaki her sayı bir kalem sayısıdır. Bir kalemi neyin belirlediğini "
                          "metal, non-metal ve standart parça için görün."),
            ]),
            html.Span(className="kx-chevron"),
        ]),
        html.Div(className="kx-body", children=[
            html.P(INTRO, className="kx-intro"),
            dcc.RadioItems(id="d4-kx-kind", className="kx-tabs", value=first["key"],
                           options=[{"label": k["tab"], "value": k["key"]} for k in KALEM_KINDS]),
            html.Div(_panel(first), id="d4-kx-panel"),
            html.Div(className="kx-note", children=[
                html.B("Kalemi değiştirmez:"),
                *[html.Span([html.B(name), ": ", text]) for name, text in NOT_PARTS],
            ]),
        ]),
    ])


@callback(Output("d4-kx-panel", "children"), Input("d4-kx-kind", "value"),
          prevent_initial_call=True)
def _show_kind(key):
    return _panel(_BY_KEY.get(key, KALEM_KINDS[0]))
