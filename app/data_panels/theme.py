# -*- coding: utf-8 -*-
"""Brand palette and a Plotly template.

Colours come from the corporate brand guide: Reflex Blue, 485 C red, Cool Gray 11,
plus complementary gold/navy/light. Discipline: blue is the workhorse, RED is
reserved for attention (rejects / long waits), grays carry structure.
"""
import plotly.graph_objects as go
import plotly.io as pio

# --- Primary (corporate) ---
BLUE = "#263685"      # Pantone Reflex Blue
RED = "#DD140E"       # Pantone 485 C
GRAY = "#4D4D4D"      # Pantone Cool Gray 11

# --- Complementary ---
LIGHT = "#F8F7F7"     # page background
GOLD = "#D29F13"      # accent / "medium" severity
NAVY = "#1E1A34"      # deep accent
NEARBLACK = "#222223" # headings

# --- Derived UI tones ---
WHITE = "#FFFFFF"
INK = "#222223"               # primary text
MUTED = "#6E7079"             # secondary text
HAIRLINE = "#E6E6EA"          # borders / gridlines
BLUE_SOFT = "#6E79B0"         # blue tint
BLUE_FAINT = "#D8DBEC"        # very light blue (bars background)

FONT_FAMILY = "Gotham, Gotham Book, Arial, sans-serif"

# Disposition colours (event_type): blues = resolved-good, red = reject.
EVENT_COLORS = {
    "__Approve": BLUE,
    "__Complete": BLUE_SOFT,
    "__Reject": RED,
}

# Sequential-ish categorical palette (used sparingly).
CATEGORICAL = [BLUE, GOLD, NAVY, BLUE_SOFT, GRAY, RED]


def _template() -> go.layout.Template:
    t = go.layout.Template()
    t.layout = go.Layout(
        font=dict(family=FONT_FAMILY, color=INK, size=13),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=CATEGORICAL,
        margin=dict(l=54, r=54, t=46, b=40),
        title=dict(font=dict(family="Gotham Bold, " + FONT_FAMILY, size=15,
                             color=INK), x=0.0, xanchor="left", y=0.97),
        xaxis=dict(showgrid=False, zeroline=False, linecolor=HAIRLINE,
                   ticks="outside", tickcolor=HAIRLINE, tickfont=dict(size=12),
                   automargin=True),
        yaxis=dict(showgrid=True, gridcolor=HAIRLINE, zeroline=False,
                   tickfont=dict(size=12), automargin=True),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right",
                    x=1.0, font=dict(size=12), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(font=dict(family=FONT_FAMILY, size=12),
                        bgcolor=NEARBLACK, bordercolor=NEARBLACK,
                        font_color=WHITE),
        colorscale=dict(sequential=[[0, BLUE_FAINT], [1, BLUE]]),
    )
    return t


pio.templates["brand"] = _template()
TEMPLATE = "brand"


def style_fig(fig: go.Figure, height: int | None = None) -> go.Figure:
    """Apply the brand template + a few shared cosmetic defaults."""
    fig.update_layout(template=TEMPLATE)
    if height:
        fig.update_layout(height=height)
    fig.update_layout(modebar=dict(remove=["select", "lasso", "autoScale"]))
    return fig
