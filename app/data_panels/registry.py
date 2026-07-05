# -*- coding: utf-8 -*-
"""Registry of available data panels.

The landing page (`pages/home.py`) builds its selector and cards from this list.
To add a new panel:
  1. create `pages/panelN.py` that calls
     `register_page(__name__, path="/panelN", ...)`,
  2. add an entry here (path must match).
"""

DASHBOARDS = [
    {
        "path": "/panel1",
        "title": "Resim Onay Performans Panosu",
        "desc": "Onaylayan bölümün onay hacmi ve bekleme süreleri",
        "cadence": "Aylık güncellenir",
    },
    {
        "path": "/panel2",
        "title": "NCR Performans Panosu",
        "desc": "NCR taleplerinin işlem hacmi ve süreleri",
        "cadence": "Aylık güncellenir",
    },
    {
        "path": "/panel3",
        "title": "Yardım Masası Performans Panosu",
        "desc": "Yardım masası taleplerinin kapanma hacmi ve süreleri",
        "cadence": "Aylık güncellenir",
    },
]
