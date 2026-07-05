# -*- coding: utf-8 -*-
"""Factory for the embedded Dash "Veri Panoları" app.

Mounted into the FastAPI portal under /data_panels/dash (see mount.py). Because
the Starlette mount strips that prefix before the request reaches Dash, we use
Dash's split-prefix setup:

    routes_pathname_prefix   = "/"                   # paths the server sees (stripped)
    requests_pathname_prefix = "/data_panels/dash/"  # URLs the browser uses

The `pages/` modules do `import common / theme / registry`, so the package dir is
put on sys.path to keep those imports working under Dash's page auto-discovery.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from dash import Dash, html, page_container  # noqa: E402  (after sys.path tweak)

import common as C  # noqa: E402

REQUESTS_PREFIX = "/data_panels/dash/"


def create_dash_app() -> Dash:
    app = Dash(
        "veri_panolari",
        use_pages=True,
        pages_folder=os.path.join(_HERE, "pages"),
        assets_folder=os.path.join(_HERE, "assets"),
        routes_pathname_prefix="/",
        requests_pathname_prefix=REQUESTS_PREFIX,
        suppress_callback_exceptions=True,
        title=C.APP_TITLE,
        update_title=None,
    )
    app.layout = html.Div(page_container)
    return app
