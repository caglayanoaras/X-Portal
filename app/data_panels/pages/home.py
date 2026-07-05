# -*- coding: utf-8 -*-
"""Landing page: pick a dashboard and open it.

Lists every dashboard from `registry.DASHBOARDS` as a selector (dropdown + button)
and as clickable cards. Selecting + pressing "Aç" routes to the dashboard's page.
"""
from dash import (Input, Output, State, callback, dcc, html, register_page,
                  get_asset_url, get_relative_path)

import common as C
from registry import DASHBOARDS

register_page(__name__, path="/", name="Ana Sayfa",
              title=C.APP_TITLE, order=0)


def _card(d):
    return dcc.Link(href=get_relative_path(d["path"]), className="dash-card", children=[
        html.Img(src=get_asset_url("emblem_dark.png"), className="dash-card-emblem"),
        html.Div(children=[
            html.H3(d["title"]),
            html.P(d["desc"]),
            html.Div(className="dash-meta", children=[
                html.Span(d["cadence"]),
            ]),
        ]),
    ])


def layout():
    return html.Div(className="home", children=[
        dcc.Location(id="home-redirect"),

        html.Header(className="topbar", children=[
            html.Img(src=get_asset_url("logo_horizontal.png"), className="logo"),
            html.Div(className="titles", children=[
                html.H1(C.APP_TITLE),
            ]),
        ]),

        html.Section(className="home-hero card", children=[
            html.H2("Bir pano seçin"),
            html.Div(className="home-pick", children=[
                html.Div(className="home-pick-sel", children=[
                    dcc.Dropdown(
                        id="home-select", clearable=False,
                        options=[{"label": d["title"], "value": d["path"]}
                                 for d in DASHBOARDS],
                        value=DASHBOARDS[0]["path"] if DASHBOARDS else None),
                ]),
                html.Button("Aç", id="home-go", n_clicks=0, className="btn"),
            ]),
        ]),

        html.Section(className="home-cards",
                     children=[_card(d) for d in DASHBOARDS]),

        html.Footer(className="foot", children=[
            f"{C.COMPANY_NAME} · Veri panoları"]),
    ])


@callback(
    Output("home-redirect", "pathname"),
    Input("home-go", "n_clicks"),
    State("home-select", "value"),
    prevent_initial_call=True,
)
def _go(_n, path):
    return get_relative_path(path or "/")
