"""End-to-end check of the Atlas API on throwaway databases.

Runs the real app against a temporary portal DB and a temporary atlas.db, so
neither db.sqlite3 nor atlas.db is touched. Run from the repo root:

    venv\\Scripts\\python.exe tests\\test_atlas.py

(pytest also finds test_atlas if it is installed.)
"""
import io
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="atlas_test_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "portal.db").replace("\\", "/")
os.environ["ATLAS_DB_PATH"] = os.path.join(TMP, "atlas.db")
os.environ["SECRET_KEY"] = secrets.token_hex(16)
os.environ["ATLAS_MAX_FILE_MB"] = "1"     # so the size limit is quick to hit
os.chdir(ROOT)                       # the app serves app/static and app/templates by relative path
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.core.database import engine  # noqa: E402
from app.dependencies.auth import get_password_hash  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Module, User, UserRole, UserType  # noqa: E402

PASSWORD = secrets.token_urlsafe(12)  # throwaway, for the temporary users below
API = "/atlas/api/classes"


class Api:
    def __init__(self, client):
        self.client, self.tokens = client, {}

    def login(self, username):
        r = self.client.post("/auth/token?set_cookie=false", data={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.tokens[username] = r.json()["access_token"]

    def __call__(self, method, url, user, expect=None, accept="application/json", **kw):
        headers = {"Accept": accept, "Authorization": f"Bearer {self.tokens[user]}", **kw.pop("headers", {})}
        r = self.client.request(method, url, headers=headers, follow_redirects=False, **kw)
        if expect is not None and r.status_code != expect:
            raise AssertionError(f"{method} {url} as {user}: expected {expect}, got {r.status_code}: {r.text[:600]}")
        return r


def seed_portal():
    """Roles lab_a/lab_b (CRUD), quality (read only) and one user per case."""
    with Session(engine) as s:
        modules = s.exec(select(Module)).all()
        atlas = next(m for m in modules if m.key == "atlas")
        roles = {}
        for name, flags in {"lab_a": (1, 1, 1, 1), "lab_b": (1, 1, 1, 1), "quality": (0, 1, 0, 0)}.items():
            roles[name] = UserRole(rolename=name, can_create=bool(flags[0]), can_read=bool(flags[1]),
                                   can_update=bool(flags[2]), can_delete=bool(flags[3]))
            s.add(roles[name])
        people = {  # username: (type, roles, modules)
            "sa": (UserType.superadmin, [], modules),
            "da": (UserType.dataadmin, [], [atlas]),
            "ua": (UserType.regular, ["lab_a"], [atlas]),
            "ub": (UserType.regular, ["lab_b"], [atlas]),
            "uq": (UserType.regular, ["quality"], [atlas]),
            "un": (UserType.regular, [], [atlas]),
            "ux": (UserType.regular, ["lab_a"], []),
        }
        for username, (utype, role_names, mods) in people.items():
            user = User(username=username, name=username, surname="Test", email=f"{username}@example.com",
                        usertype=utype, hashed_pw=get_password_hash(PASSWORD),
                        created_by="test", last_modified_by="test")
            user.roles = [roles[r] for r in role_names]
            user.modules = list(mods)
            s.add(user)
        s.commit()
        return {name: role.id for name, role in roles.items()}


def make_v1_atlas_db():
    """An atlas.db as earlier versions left it: atlas_class without color or icon,
    atlas_attribute without matrix columns, atlas_file with thumbnails and previews
    in its own rows, after the image."""
    conn = sqlite3.connect(os.environ["ATLAS_DB_PATH"])
    try:
        conn.executescript("""
            CREATE TABLE atlas_class (
                id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                path TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
                roles TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                updated_at TEXT NOT NULL, updated_by TEXT NOT NULL);
            CREATE TABLE atlas_attribute (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER NOT NULL REFERENCES atlas_class(id) ON DELETE CASCADE,
                key TEXT NOT NULL, label TEXT NOT NULL, type TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
                required INTEGER NOT NULL DEFAULT 0, is_unique INTEGER NOT NULL DEFAULT 0,
                is_title INTEGER NOT NULL DEFAULT 0, show INTEGER NOT NULL DEFAULT 1,
                options TEXT NOT NULL DEFAULT '[]', lookup TEXT NOT NULL DEFAULT '', UNIQUE (class_id, key));
            CREATE TABLE atlas_file (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, type TEXT NOT NULL,
                size INTEGER NOT NULL, width INTEGER, height INTEGER, sha256 TEXT NOT NULL, data BLOB NOT NULL,
                preview BLOB, thumb BLOB, created_at TEXT NOT NULL, created_by TEXT NOT NULL);
            INSERT INTO atlas_file (name, type, size, width, height, sha256, data, preview, thumb, created_at, created_by)
                VALUES ('scan.tif', 'image/tiff', 5, 2, 1, 'x', CAST('image' AS BLOB), CAST('preview' AS BLOB),
                        CAST('thumb' AS BLOB), '2026-11-01T00:00:00Z', 'test'),
                       ('report.pdf', 'application/pdf', 3, NULL, NULL, 'y', CAST('pdf' AS BLOB), NULL, NULL,
                        '2026-11-01T00:00:00Z', 'test');
            PRAGMA user_version = 1;""")
    finally:
        conn.close()


def check_migrated():
    conn = sqlite3.connect(os.environ["ATLAS_DB_PATH"])
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(atlas_class)")}
        columns |= {f"attribute.{row[1]}" for row in conn.execute("PRAGMA table_info(atlas_attribute)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        copied = conn.execute("SELECT file_id, thumb, preview FROM atlas_file_view").fetchall()
    finally:
        conn.close()
    assert {"color", "icon", "attribute.columns"} <= columns and version == 4, (columns, version)
    assert copied == [(1, b"thumb", b"preview")], copied        # thumbnails moved out from behind the image
    from app.atlas import files
    from app.atlas.db import connect
    with connect() as conn:
        assert [files.load(conn, 1, view)["body"] for view in ("original", "preview", "thumb")] == [
            b"image", b"preview", b"thumb"]
        assert files.load(conn, 2, "preview")["body"] == b"pdf"   # no preview: the original


def check_views_follow_files():
    """A thumbnail or preview goes when its file goes (the old files are swept by now)."""
    conn = sqlite3.connect(os.environ["ATLAS_DB_PATH"])
    try:
        left = conn.execute("SELECT COUNT(*) FROM atlas_file_view v LEFT JOIN atlas_file f ON f.id = v.file_id "
                            "WHERE f.id IS NULL").fetchone()[0]
        old = conn.execute("SELECT COUNT(*) FROM atlas_file WHERE id IN (1, 2)").fetchone()[0]
    finally:
        conn.close()
    assert (left, old) == (0, 0), (left, old)


def ids(response):
    return sorted(item["_id"] for item in response.json()["items"])


def run(api, role):
    # --- access ----------------------------------------------------------------
    r = api.client.get("/static/js/apps/atlas-app.js")
    assert r.headers.get("cache-control") == "no-cache", r.headers   # browsers re-check edited scripts
    r = api.client.get("/static/webfonts/Gotham-Medium.otf")
    assert r.status_code == 200 and "cache-control" not in r.headers, r.headers   # fonts cache as usual
    api("GET", API, "ux", 403)                                   # no Atlas module
    api("POST", API, "ua", 403, json={"key": "x", "label": "X"})  # not an admin
    r = api("GET", "/atlas/admin", "un", accept="text/html")
    assert r.status_code == 303 and r.headers["location"].endswith("/atlas/"), r.status_code
    page = api("GET", "/atlas/", "sa", 200, accept="text/html").text      # versioned script URLs
    assert "atlas-app.js?v=" in page and 'type="importmap"' in page and "atlas-common.js?v=" in page, page[-900:]
    page = api("GET", "/atlas/admin", "da", 200, accept="text/html").text
    assert "atlas-admin-app.js?v=" in page and "atlas.css?v=" in page and "atlas-common.js?v=" in page, page[-900:]

    # --- definitions -----------------------------------------------------------
    bad = [
        {"key": "Bad Key", "label": "X"},
        {"key": "x", "label": "X", "attributes": [{"key": "sort", "label": "Sort"}]},
        {"key": "x", "label": "X", "attributes": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}]},
        {"key": "x", "label": "X", "attributes": [{"key": "a", "label": "A", "type": "lookthrough", "lookup": "nope.code"}]},
        {"key": "x", "label": "X", "roles": [999]},
        {"key": "x", "label": "X", "relationships": [{"key": "r", "label": "R", "target": "nowhere"}]},
        {"key": "x", "label": "X", "color": "blue"},
        {"key": "x", "label": "X", "icon": "flask"},
        {"key": "x", "label": "X", "icon": 'fa-flask" onmouseover="x'},
        {"key": "x", "label": "X", "attributes": [{"key": "incoming_counts", "label": "I"}]},   # reserved
    ]
    for body in bad:
        api("POST", API, "da", 400, json=body)

    labs = {"key": "labs", "label": "Laboratuvarlar", "color": "#0E7490", "icon": "fa-flask", "attributes": [
        {"key": "code", "label": "Kod", "type": "string", "required": True, "unique": True, "title": True},
        {"key": "name", "label": "İsim", "type": "string"}]}
    locations = {"key": "locations", "label": "Lokasyonlar", "path": "Depo", "attributes": [
        {"key": "name", "label": "Lokasyon", "required": True, "title": True},
        {"key": "lab_code", "label": "Laboratuvar", "type": "lookthrough", "lookup": "lab.code", "title": True}],
        "relationships": [{"key": "lab", "label": "Laboratuvar", "target": "labs", "cardinality": "one",
                           "required": True}]}
    tests = {"key": "tests", "label": "Tests", "path": "Lab / QC", "roles": [role["lab_a"]], "attributes": [
        {"key": "sample", "label": "Sample", "required": True, "unique": True, "title": True},
        {"key": "result", "label": "Result", "type": "choice", "options": ["pass", "fail"]},
        {"key": "measured", "label": "Measured", "type": "float"},
        {"key": "tested_on", "label": "Tested on", "type": "date"},
        {"key": "passed", "label": "Passed", "type": "boolean"}],
        "relationships": [{"key": "original", "label": "Retest of", "target": "tests", "cardinality": "one",
                           "reverse_label": "Retests"}]}
    batches = {"key": "batches", "label": "Batches", "path": "Depo", "attributes": [
        {"key": "batch_no", "label": "Batch no", "required": True, "unique": True, "title": True},
        {"key": "qty", "label": "Qty", "type": "int"},
        {"key": "received", "label": "Received", "type": "datetime"},
        {"key": "lab_code", "label": "Lab", "type": "lookthrough", "lookup": "location.lab_code"},
        {"key": "receiving_results", "label": "Receiving results", "type": "lookthrough",
         "lookup": "receiving_tests.result"}],
        "relationships": [
            {"key": "location", "label": "Location", "target": "locations", "cardinality": "one", "required": True},
            {"key": "receiving_tests", "label": "Receiving tests", "target": "tests",
             "reverse_label": "Receiving test of"},
            {"key": "retests", "label": "Retests", "target": "tests", "reverse_label": "Retest of"}]}
    for body in (labs, locations, tests, batches):
        api("POST", API, "da", 201, json=body)
    api("POST", API, "da", 409, json=labs)                       # key taken
    loop = {"key": "loops", "label": "Loops", "attributes": [
        {"key": "a", "label": "A", "type": "lookthrough", "lookup": "me.a"}],
        "relationships": [{"key": "me", "label": "Me", "target": "loops"}]}
    api("POST", API, "da", 400, json=loop)

    listed = api("GET", API, "un", 200).json()
    visible = [c["key"] for c in listed]
    assert visible == ["labs", "batches", "locations"], visible   # tests: lab_a only
    assert (listed[0]["color"], listed[0]["icon"]) == ("#0e7490", "fa-flask"), listed[0]
    assert (listed[1]["color"], listed[1]["icon"]) == ("", ""), listed[1]
    location_def = api("GET", f"{API}/locations", "da", 200).json()
    lab_link = location_def["relationships"][0]
    assert (lab_link["target_color"], lab_link["target_icon"]) == ("#0e7490", "fa-flask"), lab_link
    api("PUT", f"{API}/locations", "da", 200, json=location_def)    # a GET's JSON goes straight back
    api("GET", f"{API}/tests", "un", 404)
    assert api("GET", f"{API}/batches", "ua", 200).json()["attributes"][3]["value_type"] == "string"
    assert "roles" not in api("GET", f"{API}/tests", "ua", 200).json()        # roles: admins only
    assert api("GET", f"{API}/tests", "da", 200).json()["roles"] == [role["lab_a"]]

    # --- objects: open class, object roles -------------------------------------
    labs_url = f"{API}/labs/objects"
    l1 = api("POST", labs_url, "un", 201, json={"code": "L-1", "name": "Çelik Lab"}).json()
    assert l1["_created_by"] == "un" and l1["_version"] == 1 and l1["_roles"] == [] and l1["_title"] == "L-1"
    l2 = api("POST", labs_url, "un", 201, json={"code": "L-2", "name": "Kimya"}).json()
    l3 = api("POST", labs_url, "da", 201, json={"code": "L-3", "_roles": [role["lab_a"]]}).json()
    api("POST", labs_url, "un", 403, json={"code": "L-4", "_roles": [role["lab_a"]]})
    api("POST", labs_url, "un", 409, json={"code": "L-1"})
    api("POST", labs_url, "un", 400, json={"name": "no code"})
    api("POST", labs_url, "un", 400, json={"code": "L-5", "colour": "red"})
    assert ids(api("GET", labs_url, "un", 200)) == [l1["_id"], l2["_id"]]
    assert ids(api("GET", labs_url, "ua", 200)) == [l1["_id"], l2["_id"], l3["_id"]]
    assert ids(api("GET", labs_url, "ub", 200)) == [l1["_id"], l2["_id"]]
    assert ids(api("GET", labs_url + "?q=CELIK", "un", 200)) == [l1["_id"]]      # Turkish-insensitive
    api("GET", f"{labs_url}/{l3['_id']}", "un", 404)

    loc_url = f"{API}/locations/objects"
    loc = api("POST", loc_url, "un", 201, json={"name": "Raf 1", "lab": l1["_id"]}).json()
    assert loc["lab_code"] == "L-1" and loc["_title"] == "Raf 1 · L-1", loc
    api("POST", loc_url, "un", 400, json={"name": "Raf 2"})                             # lab required
    api("POST", loc_url, "un", 400, json={"name": "Raf 2", "lab": [l1["_id"], l2["_id"]]})  # one only
    api("POST", loc_url, "un", 400, json={"name": "Raf 2", "lab": l3["_id"]})           # can't see L-3

    # --- objects: restricted class ---------------------------------------------
    tests_url = f"{API}/tests/objects"
    api("POST", tests_url, "un", 403, json={"sample": "S-0"})
    api("POST", tests_url, "uq", 403, json={"sample": "S-0"})
    t1 = api("POST", tests_url, "ua", 201, json={"sample": "S-1", "result": "fail", "measured": "1,5",
                                                 "tested_on": "06.11.2026", "passed": "evet"}).json()
    assert (t1["measured"], t1["tested_on"], t1["passed"]) == (1.5, "2026-11-06", True), t1
    t2 = api("POST", tests_url, "ua", 201, json={"sample": "S-2", "result": "pass", "original": t1["_id"]}).json()
    assert ids(api("GET", tests_url, "ub", 200)) == []
    api("PATCH", f"{tests_url}/{t2['_id']}", "da", 200, json={"_roles": [role["lab_b"]]})
    assert ids(api("GET", tests_url, "ub", 200)) == [t2["_id"]]            # object role adds lab_b
    assert ids(api("GET", tests_url, "ua", 200)) == [t1["_id"], t2["_id"]]  # class role keeps lab_a
    api("PATCH", f"{tests_url}/{t2['_id']}", "ub", 200, json={"result": "fail"})
    api("GET", f"{tests_url}/{t1['_id']}", "ub", 404)
    t1 = api("PATCH", f"{tests_url}/{t1['_id']}", "da", 200, json={"_roles": [role["quality"]]}).json()
    seen = api("GET", tests_url, "uq", 200).json()["items"]
    assert [i["_id"] for i in seen] == [t1["_id"]] and seen[0]["_can"] == {"update": False, "delete": False}
    api("PATCH", f"{tests_url}/{t1['_id']}", "uq", 403, json={"result": "pass"})
    api("DELETE", f"{tests_url}/{t1['_id']}", "uq", 403)
    api("PATCH", f"{tests_url}/{t1['_id']}", "ua", 403, json={"_roles": []})          # roles: admins only
    api("PATCH", f"{tests_url}/{t1['_id']}", "ua", 200, json={"_roles": [role["quality"]]})  # unchanged: fine
    api("PATCH", f"{tests_url}/{t1['_id']}", "ua", 400, json={"result": "maybe"})
    r = api("PATCH", f"{tests_url}/{t1['_id']}", "ua", 400, json={"measured": "abc"})
    assert r.json()["detail"] == "Measured must be a float.", r.json()

    v = api("GET", f"{tests_url}/{t1['_id']}", "ua", 200).json()["_version"]
    t1 = api("PATCH", f"{tests_url}/{t1['_id']}", "ua", 200, json={"measured": 2, "_version": v}).json()
    assert t1["_version"] == v + 1 and t1["_updated_by"] == "ua"
    r = api("PATCH", f"{tests_url}/{t1['_id']}", "ua", 409, json={"measured": 3, "_version": v})
    assert r.json()["version"] == v + 1
    same = api("PATCH", f"{tests_url}/{t1['_id']}", "ua", 200, json={"measured": "2"}).json()
    assert same["_version"] == v + 1                                       # nothing changed: no new version

    # --- relationships, look-throughs, filters ---------------------------------
    b_url = f"{API}/batches/objects"
    api("POST", b_url, "un", 400, json={"batch_no": "B-0", "location": loc["_id"], "receiving_tests": [t1["_id"]]})
    b1 = api("POST", b_url, "ua", 201, json={
        "batch_no": "B-1", "qty": "5", "received": "2026-11-06 10:30", "location": loc["_id"],
        "receiving_tests": [t1["_id"]], "retests": [t2["_id"]]}).json()
    assert (b1["qty"], b1["received"], b1["lab_code"], b1["receiving_results"]) == \
        (5, "2026-11-06T10:30:00", "L-1", ["fail"]), b1
    b2 = api("POST", b_url, "ua", 201, json={"batch_no": "B-2", "qty": 12, "location": loc["_id"],
                                             "retests": [t1["_id"], t2["_id"]]}).json()
    r = api("POST", b_url, "ua", 400, json={"batch_no": "B-9", "qty": "1.5", "location": loc["_id"]})
    assert r.json()["detail"] == "Qty must be an integer.", r.json()
    r = api("GET", b_url, "ua", 400, params={"qty__gte": "abc"})       # filters take any number
    assert r.json()["detail"] == "Filter 'qty': the value must be a number.", r.json()

    def query(params, user="ua"):
        return ids(api("GET", b_url, user, 200, params=params))

    assert query({"qty__gte": 10}) == [b2["_id"]]
    assert query({"retests": t1["_id"]}) == [b2["_id"]]
    assert query({"retests__in": f"{t1['_id']},{t2['_id']}"}) == [b1["_id"], b2["_id"]]
    assert query({"lab_code": "L-1"}) == [b1["_id"], b2["_id"]]
    assert query({"receiving_results": "fail"}) == [b1["_id"]]
    assert query({"receiving_results": "pass"}) == []
    assert query({"receiving_tests__isnull": "true"}) == [b2["_id"]]
    assert query({"received__on": "2026-11-06"}) == [b1["_id"]]
    assert query({"batch_no__contains": "b-1"}) == [b1["_id"]]
    assert query({"location__contains": "raf"}) == [b1["_id"], b2["_id"]]
    assert query({"q": "raf 1"}) == [b1["_id"], b2["_id"]]
    assert query({"_created_by": "ua", "qty__lt": 6}) == [b1["_id"]]
    assert [i["_id"] for i in api("GET", b_url, "ua", 200, params={"sort": "-qty"}).json()["items"]] == \
        [b2["_id"], b1["_id"]]
    api("GET", b_url, "ua", 200, params={"sort": "location,-lab_code,_title"})
    page = api("GET", b_url, "ua", 200, params={"page_size": 1, "page": 2}).json()
    assert page["total"] == 2 and [i["_id"] for i in page["items"]] == [b2["_id"]]
    one = api("GET", b_url, "ua", 200, params={"fields": "_title", "expand": "*"}).json()["items"][0]
    assert set(one) == {"_id", "_title"}, one
    full = api("GET", f"{b_url}/{b1['_id']}?expand=*", "ua", 200).json()
    assert full["location"] == {"_id": loc["_id"], "_title": "Raf 1 · L-1"}, full["location"]
    assert full["retests"] == [{"_id": t2["_id"], "_title": "S-2"}]
    for params in ({"colour": "red"}, {"qty__near": 1}, {"qty": "abc"}, {"sort": "nope"}, {"expand": "qty"}):
        api("GET", b_url, "ua", 400, params=params)

    incoming = api("GET", f"{tests_url}/{t1['_id']}", "ua", 200).json()["_incoming"]
    assert [(g["label"], [i["_id"] for i in g["items"]]) for g in incoming] == [
        ("Receiving test of", [b1["_id"]]), ("Retest of", [b2["_id"]]), ("Retests", [t2["_id"]])], incoming
    incoming = api("GET", f"{tests_url}/{t1['_id']}", "uq", 200).json()["_incoming"]   # can't read S-2
    assert incoming == [{"class": "batches", "class_label": "Batches", "relationship": "receiving_tests",
                         "label": "Receiving test of", "count": 1, "items": [{"_id": b1["_id"], "_title": "B-1"}]},
                        {"class": "batches", "class_label": "Batches", "relationship": "retests",
                         "label": "Retest of", "count": 1, "items": [{"_id": b2["_id"], "_title": "B-2"}]}]

    # the reverse side in lists: per incoming relationship, how many readable objects link here
    def counts(user):
        items = api("GET", tests_url, user, 200, params={"incoming_counts": "true"}).json()["items"]
        return {i["_id"]: i["_incoming_counts"] for i in items}
    assert counts("ua")[t1["_id"]] == {"batches.receiving_tests": 1, "batches.retests": 1,
                                       "tests.original": 1}, counts("ua")
    assert counts("uq") == {t1["_id"]: {"batches.receiving_tests": 1, "batches.retests": 1,
                                        "tests.original": 0}}, counts("uq")     # uq can't read S-2
    assert "_incoming_counts" not in api("GET", tests_url, "ua", 200).json()["items"][0]
    entry = next(e for e in api("GET", f"{API}/tests", "ua", 200).json()["incoming"]
                 if e["relationship"] == "retests")
    assert entry == {"class": "batches", "class_label": "Batches", "relationship": "retests",
                     "label": "Retest of", "show": True, "color": "", "icon": ""}, entry

    api("DELETE", f"{loc_url}/{loc['_id']}", "un", 409)                    # the only location of B-1, B-2
    api("PATCH", f"{b_url}/{b2['_id']}", "ua", 409, json={"batch_no": "B-1"})
    api("DELETE", f"{tests_url}/{t2['_id']}", "ua", 204)
    assert api("GET", f"{b_url}/{b2['_id']}", "ua", 200).json()["retests"] == [t1["_id"]]

    # --- schema changes ----------------------------------------------------------
    batches["attributes"].append({"key": "notes", "label": "Notes", "type": "text"})
    api("PUT", f"{API}/batches", "da", 200, json=batches)
    assert api("GET", f"{b_url}/{b1['_id']}", "ua", 200).json()["notes"] is None
    batches["attributes"][1]["type"] = "string"                            # qty int -> string
    api("PUT", f"{API}/batches", "da", 200, json=batches)
    assert api("GET", f"{b_url}/{b1['_id']}", "ua", 200).json()["qty"] == "5"
    batches["attributes"][1]["type"] = "int"
    api("PUT", f"{API}/batches", "da", 200, json=batches)
    assert api("GET", f"{b_url}/{b2['_id']}", "ua", 200).json()["qty"] == 12
    api("PATCH", f"{b_url}/{b1['_id']}", "ua", 200, json={"notes": "abc"})
    api("PATCH", f"{b_url}/{b2['_id']}", "ua", 200, json={"notes": "7"})
    batches["attributes"][-1]["type"] = "int"                              # 'abc' doesn't survive
    r = api("PUT", f"{API}/batches", "da", 409, json=batches)
    assert r.json()["requires_force"] and len(r.json()["warnings"]) == 1, r.json()
    assert r.json()["warnings"][0] == "Changing 'Notes' to integer clears 1 value(s) that don't fit.", r.json()
    api("PUT", f"{API}/batches?force=true", "da", 200, json=batches)
    assert [api("GET", f"{b_url}/{b['_id']}", "ua", 200).json()["notes"] for b in (b1, b2)] == [None, 7]
    batches["attributes"].pop()                                            # remove notes (B-2 has 7)
    api("PUT", f"{API}/batches", "da", 409, json=batches)
    api("PUT", f"{API}/batches?force=true", "da", 200, json=batches)
    assert "notes" not in api("GET", f"{b_url}/{b2['_id']}", "ua", 200).json()

    t3 = api("POST", tests_url, "ua", 201, json={"sample": "S-3"}).json()
    api("PATCH", f"{b_url}/{b2['_id']}", "ua", 200, json={"receiving_tests": [t1["_id"], t3["_id"]]})
    batches["relationships"][1]["cardinality"] = "one"
    api("PUT", f"{API}/batches", "da", 409, json=batches)                  # B-2 has two
    batches["relationships"][1]["cardinality"] = "many"
    batches["relationships"][2]["target"] = "labs"
    api("PUT", f"{API}/batches", "da", 400, json=batches)                  # target can't change
    batches["relationships"].pop()                                         # drop retests (has links)
    api("PUT", f"{API}/batches", "da", 409, json=batches)
    api("PUT", f"{API}/batches?force=true", "da", 200, json=batches)

    labs.update(color="", icon="fa-microscope")                              # a new look, same data
    out = api("PUT", f"{API}/labs", "da", 200, json=labs).json()
    assert (out["color"], out["icon"]) == ("", "fa-microscope"), out
    api("PATCH", f"{labs_url}/{l2['_id']}", "un", 200, json={"name": "Çelik Lab"})
    labs["attributes"][1]["unique"] = True
    api("PUT", f"{API}/labs", "da", 409, json=labs)                        # duplicate names
    labs["attributes"][1]["unique"] = False
    api("PUT", f"{API}/labs", "da", 400, json={**labs, "attributes": labs["attributes"][1:]})  # code is looked through
    api("PUT", f"{API}/labs", "da", 400, json={**labs, "key": "renamed"})

    # --- role deletion purges Atlas --------------------------------------------
    tests["roles"] = [role["lab_a"], role["lab_b"]]
    api("PUT", f"{API}/tests", "da", 200, json=tests)
    api("PATCH", f"{tests_url}/{t1['_id']}", "da", 200, json={"_roles": [role["quality"], role["lab_b"]]})
    api("DELETE", f"/users_and_permissions/userroles/{role['lab_b']}", "sa", 204)
    assert api("GET", f"{API}/tests", "da", 200).json()["roles"] == [role["lab_a"]]
    assert api("GET", f"{tests_url}/{t1['_id']}", "da", 200).json()["_roles"] == [role["quality"]]

    # --- folders: a name matches whatever its case or Turkish letters ------------
    api("POST", API, "da", 201, json={"key": "notes", "label": "Notes", "path": "Kalite Güvence / Raporlar"})
    memos = {"key": "memos", "label": "Memos", "path": "KALİTE GÜVENCE/raporlar/Eski"}
    assert api("POST", API, "da", 201, json=memos).json()["path"] == "Kalite Güvence/Raporlar/Eski"
    memos["path"] = "LAB/qc"
    assert api("PUT", f"{API}/memos", "da", 200, json=memos).json()["path"] == "Lab/QC"
    for key in ("notes", "memos"):
        api("DELETE", f"{API}/{key}", "da", 204)

    # --- deleting classes --------------------------------------------------------
    api("DELETE", f"{API}/labs", "da", 409)                                # locations link to labs
    r = api("DELETE", f"{API}/batches", "da", 409)
    assert r.json()["requires_force"]
    api("DELETE", f"{API}/batches?force=true", "da", 204)
    conn = sqlite3.connect(os.environ["ATLAS_DB_PATH"])
    try:
        left = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%batches%'")}
    finally:
        conn.close()
    assert not left, left


def image_bytes(size=(64, 48), fmt="PNG"):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", size, (190, 40, 30)).save(buffer, fmt)
    return buffer.getvalue()


def tiff16_bytes():
    """A 16-bit greyscale TIFF, as microscope software writes them."""
    import numpy as np
    from PIL import Image
    buffer = io.BytesIO()
    Image.fromarray((np.arange(64 * 64, dtype=np.uint16).reshape(64, 64) * 9)).save(buffer, "TIFF")
    return buffer.getvalue()


def stored_files():
    conn = sqlite3.connect(os.environ["ATLAS_DB_PATH"])
    try:
        return conn.execute("SELECT COUNT(*) FROM atlas_file").fetchone()[0]
    finally:
        conn.close()


def run_files(api, role):
    from PIL import Image

    for body in (
        {"key": "y", "label": "Y", "attributes": [{"key": "p", "label": "P", "type": "image", "unique": True}]},
        {"key": "y", "label": "Y", "attributes": [{"key": "p", "label": "P", "type": "file", "title": True}]},
    ):
        api("POST", API, "da", 400, json=body)
    specimens = {"key": "specimens", "label": "Specimens", "roles": [role["lab_a"], role["quality"]], "attributes": [
        {"key": "code", "label": "Code", "required": True, "title": True},
        {"key": "photo", "label": "Photo", "type": "image", "required": True},
        {"key": "report", "label": "Report", "type": "file"}]}
    api("POST", API, "da", 201, json=specimens)
    api("POST", API, "da", 400, json={"key": "views", "label": "Views", "attributes": [   # can't look through an image
        {"key": "pic", "label": "Pic", "type": "lookthrough", "lookup": "spec.photo"}],
        "relationships": [{"key": "spec", "label": "Spec", "target": "specimens", "cardinality": "one"}]})

    url = f"{API}/specimens/objects"
    s1 = api("POST", url, "ua", 201, json={"code": "SP-1"}).json()       # a new object can't carry uploads yet
    assert s1["photo"] is None and s1["report"] is None, s1
    api("PATCH", f"{url}/{s1['_id']}", "ua", 400, json={"code": "SP-1b"})   # a change needs the required photo
    files_url = f"{url}/{s1['_id']}/files"

    def put(user, key, name, data, expect, version=None, files_url=files_url):
        form = {"_version": str(version)} if version is not None else {}
        return api("PUT", f"{files_url}/{key}", user, expect, files={"file": (name, data)}, data=form)

    # --- uploads are checked --------------------------------------------------
    put("ua", "photo", "fake.png", b"not an image", 400)
    put("ua", "photo", "doc.pdf", b"%PDF-1.4 x", 400)                       # a PDF isn't an image
    put("ua", "report", "tool.exe", b"MZ\x90\x00", 400)
    put("ua", "report", "fake.pdf", b"MZ\x90\x00", 400)                     # an .exe renamed to .pdf
    put("ua", "report", "big.pdf", b"%PDF-" + b"0" * (1024 * 1024), 413)
    put("ua", "code", "a.png", image_bytes(), 404)                          # not an image or file attribute
    put("ub", "photo", "a.png", image_bytes(), 404)                         # ub can't see the object
    put("uq", "photo", "a.png", image_bytes(), 403)                         # uq can read, not change

    # --- images: original, thumbnail, preview; versions ------------------------
    png = image_bytes((800, 600))
    obj = put("ua", "photo", "Kırık yüzey.png", png, 200, version=s1["_version"]).json()
    assert obj["photo"] == {"name": "Kırık yüzey.png", "type": "image/png", "size": len(png),
                            "width": 800, "height": 600}, obj["photo"]
    assert obj["_version"] == s1["_version"] + 1 and obj["_updated_by"] == "ua"
    put("ua", "photo", "b.png", image_bytes(), 409, version=s1["_version"])  # changed meanwhile
    r = api("GET", f"{files_url}/photo", "uq", 200)
    assert r.content == png and r.headers["content-type"] == "image/png", r.headers
    assert r.headers["content-disposition"].startswith("inline;") and "K%C4%B1r%C4%B1k" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    api("GET", f"{files_url}/photo", "uq", 304, headers={"If-None-Match": r.headers["etag"]})
    thumb = api("GET", f"{files_url}/photo", "uq", 200, params={"view": "thumb"})
    assert thumb.headers["content-type"] == "image/jpeg" and max(Image.open(io.BytesIO(thumb.content)).size) == 240
    assert api("GET", f"{files_url}/photo", "uq", 200, params={"view": "preview"}).content == png   # shown as it is
    download = api("GET", f"{files_url}/photo", "uq", 200, params={"download": "true"})
    assert download.headers["content-disposition"].startswith("attachment;")
    api("GET", f"{files_url}/photo", "ub", 404)
    obj = put("ua", "photo", "micro.tif", tiff16_bytes(), 200).json()        # replaces the PNG
    assert obj["photo"]["type"] == "image/tiff", obj["photo"]
    preview = api("GET", f"{files_url}/photo", "ua", 200, params={"view": "preview"})
    assert preview.headers["content-type"] == "image/jpeg"                  # browsers can't show TIFF
    assert stored_files() == 1, stored_files()                             # the PNG it replaced is gone

    # --- files: PDF opens in the browser, Office files download ----------------
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    obj = put("ua", "report", "Rapor.pdf", pdf, 200).json()
    assert obj["report"] == {"name": "Rapor.pdf", "type": "application/pdf", "size": len(pdf)}, obj["report"]
    assert api("GET", f"{files_url}/report", "ua", 200).headers["content-disposition"].startswith("inline;")
    put("ua", "report", "Sonuçlar.xlsx", b"PK\x03\x04" + bytes(100), 200)
    r = api("GET", f"{files_url}/report", "ua", 200)
    assert r.headers["content-disposition"].startswith('attachment; filename="Sonuclar.xlsx"'), r.headers
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml")
    api("GET", f"{files_url}/report", "ua", 404, params={"view": "thumb"})  # only images have thumbnails
    assert stored_files() == 2

    # --- JSON writes: null clears, what GET returned is left alone, the rest is refused
    current = api("GET", f"{url}/{s1['_id']}", "ua", 200).json()
    assert api("PATCH", f"{url}/{s1['_id']}", "ua", 200, json=current).json()["_version"] == current["_version"]
    api("PATCH", f"{url}/{s1['_id']}", "ua", 400, json={"report": "c:/reports/x.pdf"})
    api("PATCH", f"{url}/{s1['_id']}", "ua", 400, json={"photo": None})     # required
    api("DELETE", f"{files_url}/photo", "ua", 400)                          # required: upload another instead
    assert api("PATCH", f"{url}/{s1['_id']}", "ua", 200, json={"report": None}).json()["report"] is None
    assert stored_files() == 1

    # --- filters, search and sorting use the file name ------------------------
    put("ua", "report", "Rapor 2.pdf", pdf, 200)
    s2 = api("POST", url, "ua", 201, json={"code": "SP-2"}).json()
    put("ua", "photo", "other.png", image_bytes(), 200, files_url=f"{url}/{s2['_id']}/files")

    def found(params):
        return ids(api("GET", url, "ua", 200, params=params))

    assert found({"report__isnull": "false"}) == [s1["_id"]]
    assert found({"photo__contains": "micro"}) == [s1["_id"]]
    assert found({"q": "rapor"}) == [s1["_id"]]
    assert found({"report": "Rapor 2.pdf"}) == [s1["_id"]]
    api("GET", url, "ua", 200, params={"sort": "photo,-report"})
    api("GET", url, "ua", 400, params={"photo__gt": "a"})

    # --- deleting objects, attributes and classes deletes their files ------------
    assert stored_files() == 3
    api("DELETE", f"{url}/{s2['_id']}", "ua", 204)
    assert stored_files() == 2
    specimens["attributes"][2]["type"] = "string"                           # report: file -> text
    r = api("PUT", f"{API}/specimens", "da", 409, json=specimens)
    assert r.json()["warnings"] == ["Changing 'Report' to text deletes its value in 1 object(s)."], r.json()
    api("PUT", f"{API}/specimens?force=true", "da", 200, json=specimens)
    assert stored_files() == 1 and api("GET", f"{url}/{s1['_id']}", "ua", 200).json()["report"] is None
    api("DELETE", f"{API}/specimens?force=true", "da", 204)
    assert stored_files() == 0


def run_export(api):
    import csv
    import datetime
    import time

    import openpyxl

    from app.atlas.export import CELL_MAX, Cells

    api("POST", API, "da", 201, json={"key": "test_types", "label": "Test types", "attributes": [
        {"key": "name", "label": "Name", "required": True, "title": True}]})
    api("POST", API, "da", 201, json={"key": "test_results", "label": "Test results", "attributes": [
        {"key": "sample", "label": "Sample", "title": True},
        {"key": "value", "label": "Value", "type": "float"},
        {"key": "tested", "label": "Tested", "type": "datetime"},
        {"key": "passed", "label": "Passed", "type": "boolean"},
        {"key": "note", "label": "Note"}],
        "relationships": [
            {"key": "type", "label": "Type", "target": "test_types", "cardinality": "one", "reverse_label": "Results"},
            {"key": "related", "label": "Related", "target": "test_results"}]})
    types_url, results_url = f"{API}/test_types/objects", f"{API}/test_results/objects"
    tensile = api("POST", types_url, "ua", 201, json={"name": "Tensile"}).json()
    shear = api("POST", types_url, "ua", 201, json={"name": "Shear"}).json()

    # --- 5,000 results of one test type -----------------------------------------
    conn = sqlite3.connect(os.environ["ATLAS_DB_PATH"])
    stamp = "2026-11-19T07:00:00Z"
    with conn:
        for n in range(5000):
            row = conn.execute('INSERT INTO obj_test_results ("_roles", "_created_at", "_created_by", "_updated_at", '
                               '"_updated_by", "_version", "sample", "value") VALUES (?,?,?,?,?,?,?,?)',
                               ("[]", stamp, "seed", stamp, "seed", 1, f"R-{n:04d}", n / 10)).lastrowid
            conn.execute('INSERT INTO "link_test_results__type" (source_id, target_id) VALUES (?, ?)',
                         (row, tensile["_id"]))
    conn.close()
    started = time.perf_counter()
    group = api("GET", f"{types_url}/{tensile['_id']}", "ua", 200).json()["_incoming"][0]
    assert time.perf_counter() - started < 3, "reading a type with 5,000 results should stay quick"
    assert (group["label"], group["count"], len(group["items"])) == ("Results", 5000, 20), group
    counts = api("GET", types_url, "ua", 200, params={"incoming_counts": "true"}).json()["items"]
    assert {i["_id"]: i["_incoming_counts"]["test_results.type"] for i in counts} == {tensile["_id"]: 5000,
                                                                                    shear["_id"]: 0}
    page = api("GET", results_url, "ua", 200, params={"type": tensile["_id"], "fields": "_title", "sort": "_title",
                                                      "page": 2, "page_size": 50}).json()
    assert page["total"] == 5000 and [i["_title"] for i in page["items"][:2]] == ["R-0050", "R-0051"], page["total"]
    found = api("GET", results_url, "ua", 200, params={"type": tensile["_id"], "q": "R-4321"}).json()
    assert [i["_title"] for i in found["items"]] == ["R-4321"], found

    # --- export: the grid's rows (filters, sort) and columns, links as "title (#id)" -----------
    r1 = api("POST", results_url, "ua", 201, json={"sample": "S-1", "value": 1.5, "tested": "2026-11-19 09:30",
                                                   "passed": True, "note": "=1+1", "type": shear["_id"]}).json()
    r2 = api("POST", results_url, "ua", 201, json={"sample": "S-2", "value": 2.25, "passed": False, "note": "-5 mm",
                                                   "type": shear["_id"], "related": [r1["_id"]]}).json()
    params = {"type": shear["_id"], "sort": "-value", "incoming_counts": "true", "_tz": "Europe/Istanbul",
              "fields": "_id,sample,value,tested,passed,note,type,related,_updated_at"}
    r = api("GET", f"{results_url}.xlsx", "ua", 200, params=params)
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml")
    assert r.headers["content-disposition"].startswith('attachment; filename="Test results ')
    sheet = openpyxl.load_workbook(io.BytesIO(r.content)).active
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0] == ("ID", "Sample", "Value", "Tested", "Passed", "Note", "Type", "Related", "Updated",
                       "Test results › Related (count)"), rows[0]
    updated = (datetime.datetime.strptime(r2["_updated_at"], "%Y-%m-%dT%H:%M:%SZ") + datetime.timedelta(hours=3))
    assert rows[1] == (r2["_id"], "S-2", 2.25, None, False, "-5 mm", f"Shear (#{shear['_id']})",
                       f"S-1 (#{r1['_id']})", updated, 0), rows[1]
    assert rows[2][:8] == (r1["_id"], "S-1", 1.5, datetime.datetime(2026, 11, 19, 9, 30), True, "=1+1",
                           f"Shear (#{shear['_id']})", None) and rows[2][9] == 1, rows[2]
    assert sheet.cell(row=3, column=6).data_type == "s"                    # "=1+1" stays text, not a formula
    assert len(rows) == 3 and sheet.freeze_panes == "A2" and sheet.auto_filter.ref == "A1:J3"
    r = api("GET", f"{results_url}.csv", "ua", 200, params=params)
    assert r.content.startswith(b"\xef\xbb\xbf")                           # so Excel reads UTF-8
    table = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert table[1][2:6] == ["2.25", "", "false", "'-5 mm"] and table[2][3:6] == ["2026-11-19 09:30:00", "true", "'=1+1"], table
    everything = list(csv.reader(io.StringIO(api("GET", f"{types_url}.csv", "ua", 200).content.decode("utf-8-sig"))))
    assert everything[0] == ["ID", "Name", "Created", "Created by", "Updated", "Updated by"], everything[0]
    assert len(api("GET", f"{API}/tests/objects.csv", "un", 200).content.decode("utf-8-sig").splitlines()) == 1
    api("GET", f"{results_url}.xlsx", "ua", 400, params={"fields": "_id,nope"})
    api("GET", f"{results_url}.pdf", "ua", 404)
    many = Cells.links([{"_id": n, "_title": f"R-{n:05d}"} for n in range(10000)])
    assert len(many) <= CELL_MAX and many.endswith("more"), many[-40:]   # fits one Excel cell

    api("DELETE", f"{API}/test_results?force=true", "da", 204)
    api("DELETE", f"{API}/test_types?force=true", "da", 204)


def run_series(api):
    """List and matrix attributes: parsing, the brief form, filters, sort, column changes, export."""
    import csv

    import openpyxl

    curve = {"key": "curve", "label": "Curve", "type": "matrix",
             "columns": [{"key": "strain", "label": "Strain (%)"}, {"key": "stress", "label": "Stress (MPa)"}]}
    base = [{"key": "name", "label": "Name", "title": True}, {"key": "readings", "label": "Readings", "type": "list"},
            curve, {"key": "raw", "label": "Raw"}]
    for bad, message in [
        ({**curve, "columns": []}, "needs at least one column"),
        ({**curve, "columns": ["A", "a"]}, "two columns with the key 'a'"),
        ({"key": "r", "label": "R", "type": "list", "unique": True}, "can't be unique or part of the title"),
        ({"key": "r", "label": "R", "type": "matrix", "title": True, "columns": ["X"]}, "can't be unique"),
    ]:
        r = api("POST", API, "da", 400, json={"key": "bad", "label": "Bad", "attributes": [bad]})
        assert message in r.json()["detail"], r.json()
    spec = api("POST", API, "da", 201, json={"key": "specimens", "label": "Specimens", "attributes": base}).json()
    assert spec["attributes"][2]["columns"][1] == {"key": "stress", "label": "Stress (MPa)"}, spec["attributes"][2]
    r = api("POST", API, "da", 400, json={"key": "looks", "label": "Looks", "attributes": [
        {"key": "r", "label": "R", "type": "lookthrough", "lookup": "spec.readings"}],
        "relationships": [{"key": "spec", "label": "Spec", "target": "specimens"}]})
    assert "can't show a list" in r.json()["detail"], r.json()
    labels = api("POST", API, "da", 201, json={"key": "label_only", "label": "L", "attributes": [
        {"key": "m", "label": "M", "type": "matrix", "columns": ["Sıcaklık (°C)", "Gerilme"]}]}).json()
    assert [c["key"] for c in labels["attributes"][0]["columns"]] == ["sicaklik_c", "gerilme"], labels
    api("DELETE", f"{API}/label_only", "da", 204)

    url = f"{API}/specimens/objects"
    s1 = api("POST", url, "ua", 201, json={"name": "S-1", "readings": "210; 212,5; 208",
                                           "curve": [[0, 0], [0.1, 210], ["0,2", None]], "raw": "5; 6"}).json()
    assert s1["readings"] == [210, 212.5, 208] and s1["curve"] == [[0, 0], [0.1, 210], [0.2, None]], s1
    s2 = api("POST", url, "ua", 201, json={"name": "S-2", "readings": [1, 2], "curve": "0\t0\n0,5\t300\n"}).json()
    assert s2["curve"] == [[0, 0], [0.5, 300]], s2
    r = api("POST", url, "ua", 400, json={"name": "S-3", "readings": "a; 2"})
    assert r.json()["detail"] == "Readings must be numbers (value 1 is 'a').", r.json()
    r = api("POST", url, "ua", 400, json={"name": "S-3", "curve": [[1, 2, 3]]})
    assert "row 1 has 3 values but there are 2 columns" in r.json()["detail"], r.json()
    api("POST", url, "ua", 400, json={"name": "S-3", "readings": list(range(100_001))})

    brief = api("GET", url, "ua", 200, params={"brief": "true", "sort": "_id"}).json()["items"]
    assert [(i["readings"], i["curve"]) for i in brief] == [({"count": 3}, {"count": 3}), ({"count": 2}, {"count": 2})]
    full = api("GET", url, "ua", 200, params={"_id": s1["_id"], "fields": "curve"}).json()["items"][0]
    assert full == {"_id": s1["_id"], "curve": s1["curve"]}, full
    assert ids(api("GET", url, "ua", 200, params={"curve__isnull": "false"})) == [s1["_id"], s2["_id"]]
    assert ids(api("GET", url, "ua", 200, params={"readings__isnull": "true"})) == []
    api("GET", url, "ua", 400, params={"curve__gt": "1"})
    by_count = api("GET", url, "ua", 200, params={"sort": "-curve,_id"}).json()["items"]
    assert [i["_id"] for i in by_count] == [s1["_id"], s2["_id"]]
    assert api("GET", url, "ua", 200, params={"q": "212"}).json()["total"] == 0   # numbers aren't searched

    one = f"{url}/{s1['_id']}"
    patched = api("PATCH", one, "ua", 200, json={"curve": {"count": 3}, "_version": s1["_version"]}).json()
    assert patched["_version"] == s1["_version"] and patched["curve"] == s1["curve"]   # a brief value is left alone
    patched = api("PATCH", one, "ua", 200, json={"readings": None}).json()
    assert patched["readings"] is None and patched["_version"] == s1["_version"] + 1
    api("PATCH", one, "ua", 200, json={"readings": [210, 212.5, 208]})

    # --- changing the definition: columns move, are added and removed; a type change converts
    def put(attributes, expect=200, **params):
        return api("PUT", f"{API}/specimens", "da", expect, params=params,
                   json={"label": "Specimens", "attributes": attributes})

    version = api("GET", one, "ua", 200).json()["_version"]
    moved = {**curve, "columns": [curve["columns"][1], curve["columns"][0], {"key": "temp", "label": "Temp"}]}
    put([base[0], base[1], moved, {**base[3], "type": "list"}])
    s1 = api("GET", one, "ua", 200).json()
    assert s1["curve"] == [[0, 0, None], [210, 0.1, None], [None, 0.2, None]] and s1["raw"] == [5, 6], s1
    assert s1["_version"] == version + 1 and s1["_updated_by"] == "da", s1          # rows rewritten: a new version
    r = api("PATCH", one, "ua", 409, json={"curve": [[1, 2]], "_version": version})  # a form from before can't save
    assert "was changed by da" in r.json()["detail"], r.json()
    r = put([base[0], base[1], {**curve, "columns": [curve["columns"][1]]}], 409)
    assert sorted(r.json()["warnings"]) == ["Removing 'Raw' deletes its value in 1 object(s).",
                                            "Removing the column 'Strain (%)' of 'Curve' deletes values in 2 object(s)."
                                            ], r.json()      # Temp held nothing, so it isn't named
    put([base[0], {**base[1], "type": "matrix", "columns": ["Value"]}, {**curve, "columns": [curve["columns"][1]]}],
        force="true")
    s1 = api("GET", one, "ua", 200).json()
    assert s1["curve"] == [[0], [210]] and s1["readings"] == [[210], [212.5], [208]], s1   # the empty row is gone

    # --- export: JSON in the cell, which an import reads back
    r = api("GET", f"{url}.xlsx", "ua", 200, params={"fields": "_id,readings,curve", "sort": "_id"})
    rows = list(openpyxl.load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True))
    assert rows[1] == (s1["_id"], "[[210], [212.5], [208]]", "[[0], [210]]"), rows
    r = api("GET", f"{url}.csv", "ua", 200, params={"fields": "_id,curve", "sort": "_id"})
    assert list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))[2] == [str(s2["_id"]), "[[0], [300]]"]
    api("DELETE", f"{API}/specimens?force=true", "da", 204)


def run_import(api, role):
    """Excel and CSV import (admins only): mapping, checks, all-or-nothing, roles, a round trip
    through an export."""
    import datetime
    import json
    import time

    import openpyxl

    def book(rows, name="Results"):
        wb = openpyxl.Workbook()
        wb.active.title = name
        for row in rows:
            wb.active.append(row)
        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()

    def send(data, user="da", expect=200, filename="results.xlsx", **form):     # imports are for admins
        form = {k: (json.dumps(v) if k == "mapping" else str(v).lower() if isinstance(v, bool) else v)
                for k, v in form.items()}
        return api("POST", f"{API}/results/import", user, expect, files={"file": (filename, data)}, data=form)

    api("POST", API, "da", 201, json={"key": "types", "label": "Test types", "attributes": [
        {"key": "name", "label": "Ad", "required": True, "title": True}]})
    api("POST", API, "da", 201, json={"key": "results", "label": "Results", "attributes": [
        {"key": "sample", "label": "Numune", "title": True, "unique": True},
        {"key": "value", "label": "Değer", "type": "float"},
        {"key": "tested", "label": "Tarih", "type": "date"},
        {"key": "passed", "label": "Geçti", "type": "boolean"},
        {"key": "grade", "label": "Sınıf", "type": "choice", "options": ["Pass", "Fail"]},
        {"key": "readings", "label": "Okumalar", "type": "list"},
        {"key": "curve", "label": "Eğri", "type": "matrix", "columns": ["Strain", "Stress"]},
        {"key": "photo", "label": "Foto", "type": "image"}],
        "relationships": [{"key": "type", "label": "Test türü", "target": "types", "cardinality": "one",
                           "required": True},
                          {"key": "related", "label": "İlgili", "target": "results"}]})
    types_url, url = f"{API}/types/objects", f"{API}/results/objects"
    tensile = api("POST", types_url, "ua", 201, json={"name": "Çekme"}).json()
    hardness = api("POST", types_url, "ua", 201, json={"name": "Sertlik"}).json()
    for _ in range(2):
        api("POST", types_url, "ua", 201, json={"name": "Darbe"})

    header = ["ID", "NUMUNE", "Değer", "tarih", "Geçti", "Sınıf", "Okumalar", "Eğri", "Test türü", "Created", "Foto"]
    rows = [header,
            [None, "S-1", 1.5, datetime.datetime(2026, 11, 20), True, "pass", "210; 212,5", "0; 0 | 0,5; 210", "çekme"],
            [None, "S-2", "2,5", "25.11.2026", "evet", "Fail", "[1, 2]", "[[0, 1]]", f"Sertlik (#{hardness['_id']})"],
            [None, "S-3", "abc", None, None, None, None, None, "Çekme"],
            [None, None, None, None, None, None, None, None, None],
            [None, "S-4", 1, None, None, None, None, None, None],
            [None, "S-5", 2, None, None, None, None, None, "Darbe"],
            [None, "S-6", 3, None, None, None, None, None, "Yok"]]
    report = send(book(rows)).json()
    assert [c["field"] for c in report["columns"]] == ["_id", "sample", "value", "tested", "passed", "grade",
                                                       "readings", "curve", "type", "", ""], report["columns"]
    assert [c["note"] for c in report["columns"][-2:]] == ["Atlas fills this in", "Images are uploaded in the form"]
    assert report["columns"][1]["examples"] == ["S-1", "S-2", "S-3"], report["columns"][1]
    assert (report["rows"], report["created"], report["failed"], report["imported"]) == (6, 2, 4, False), report
    problems = {e["message"]: e["rows"] for e in report["errors"]}
    assert problems == {"Değer must be a float.": [4], "Required: Test türü.": [6],
                        f"Test türü: 2 Test types objects are titled 'Darbe' (#{tensile['_id'] + 2}, "
                        f"#{tensile['_id'] + 3}). Write the one you mean as #id.": [7],
                        "Test türü: no Test types object is titled 'Yok' (or you can't see it). "
                        "Write an ID as #12.": [8]}, problems
    assert send(book(rows), dry_run=False).json()["imported"] is False          # a problem: nothing goes in
    assert api("GET", url, "ua", 200).json()["total"] == 0

    report = send(book(rows[:3]), dry_run=False).json()
    assert (report["created"], report["failed"], report["imported"]) == (2, 0, True), report
    s1, s2 = api("GET", url, "ua", 200, params={"sort": "sample"}).json()["items"]
    assert (s1["value"], s1["tested"], s1["passed"], s1["grade"], s1["readings"], s1["curve"], s1["type"]) == (
        1.5, "2026-11-20", True, "Pass", [210, 212.5], [[0, 0], [0.5, 210]], tensile["_id"]), s1
    assert (s2["value"], s2["tested"], s2["passed"], s2["type"]) == (2.5, "2026-11-25", True, hardness["_id"]), s2

    # --- round trip: export, change a cell, import it back ----------------------
    r = api("GET", f"{url}.xlsx", "ua", 200, params={"fields": "_id,sample,value,type,related,curve,_version",
                                                     "sort": "sample"})
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb.active
    assert [c.value for c in ws[1]] == ["ID", "Numune", "Değer", "Test türü", "İlgili", "Eğri", "Version"]
    ws["C2"] = 9.75                                     # S-1's value
    ws["E2"] = f"S-2 (#{s2['_id']})"                    # S-1 now links to S-2
    buffer = io.BytesIO()
    wb.save(buffer)
    exported = buffer.getvalue()
    report = send(exported, dry_run=False).json()
    assert (report["updated"], report["unchanged"], report["failed"]) == (1, 1, 0), report
    s1 = api("GET", f"{url}/{s1['_id']}", "ua", 200).json()
    assert s1["value"] == 9.75 and s1["related"] == [s2["_id"]] and s1["curve"] == [[0, 0], [0.5, 210]], s1
    report = send(exported).json()                       # the file's Version is now out of date
    assert report["failed"] == 1 and "was changed by da" in report["errors"][0]["message"], report

    # --- a CSV export puts ' before text Excel would run as a formula; an import takes it off
    plus = api("POST", url, "ua", 201, json={"sample": "+90-S", "type": tensile["_id"]}).json()
    r = api("GET", f"{url}.csv", "ua", 200, params={"fields": "_id,sample", "sample": "+90-S"})
    assert "'+90-S" in r.content.decode("utf-8-sig"), r.content
    assert send(r.content, filename="results.csv").json()["unchanged"] == 1
    api("DELETE", f"{url}/{plus['_id']}", "ua", 204)

    # --- a curve too long for an Excel cell: the export says so, and an import leaves it be
    api("PATCH", f"{url}/{s2['_id']}", "ua", 200, json={"curve": [[n / 1000, 200 + n / 7] for n in range(6000)]})
    r = api("GET", f"{url}.xlsx", "ua", 200, params={"fields": "_id,sample,curve", "sort": "sample"})
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.active["C3"].value == "(6,000 rows: too long for an Excel cell; the CSV export holds them all)"
    assert send(r.content).json()["unchanged"] == 2
    r = api("GET", f"{url}.csv", "ua", 200, params={"fields": "_id,sample,curve", "sort": "sample"})
    assert len(r.content) > 131_072                     # more than Python's CSV reader takes in a cell by default
    assert send(r.content, filename="results.csv").json()["unchanged"] == 2
    wb.active["A3"], wb.active["B3"] = None, "S-NEW"     # the note can't make a new object
    buffer = io.BytesIO()
    wb.save(buffer)
    report = send(buffer.getvalue()).json()
    assert report["errors"][0]["message"].startswith("Eğri: the cell holds an export's note"), report
    assert len(api("GET", f"{url}/{s2['_id']}", "ua", 200).json()["curve"]) == 6000

    # --- rows without an ID: a duplicate, unless matched on a unique attribute -----
    again = book([["Numune", "Değer", "Test türü"], ["S-2", 7, f"#{tensile['_id']}"], ["S-7", 1, "Çekme"]])
    report = send(again).json()
    assert report["errors"][0]["message"] == "Numune must be unique: 'S-2' is already used.", report
    report = send(again, match="sample", dry_run=False).json()
    assert (report["updated"], report["created"], report["imported"]) == (1, 1, True), report
    assert api("GET", f"{url}/{s2['_id']}", "ua", 200).json()["value"] == 7
    report = send(book([["Numune", "Test türü"], ["S-8", "Çekme"], ["s-8 ", "Çekme"]]), match="sample").json()
    assert report["errors"] == [], report               # "S-8" and "s-8" are different values
    report = send(book([["Numune", "Test türü"], ["S-8", "Çekme"], ["S-8", "Çekme"]]), match="sample").json()
    assert report["errors"][0] == {"message": "Numune 'S-8' is on row 2 too.", "count": 1, "rows": [3]}, report
    send(again, expect=400, match="value")               # not a unique attribute
    report = send(book([["ID", "Değer"], [s1["_id"], 1], [s1["_id"], 2], [99999, 3], ["x", 4]])).json()
    assert [e["message"] for e in report["errors"]] == [
        f"ID {s1['_id']} is on row 2 too.", "Results #99999 doesn't exist, or you can't see it.",
        "ID must be a whole number, not 'x'."], report

    # --- a link to a row above, an Excel error, one object changed by two rows ---------
    report = send(book([["Numune", "Test türü", "İlgili"], ["S-20", "Çekme", "S-1"], ["S-21", "Çekme", "S-20; S-1"],
                        ["S-22", "#N/A", None]])).json()
    assert (report["created"], [e["message"] for e in report["errors"]]) == (2, [
        "Test türü: the cell shows the Excel error #N/A. Fix its formula, or type the value in."]), report
    report = send(book([["ID", "Numune", "Değer"], [s1["_id"], "S-1", 1], [None, "S-1", 2]]), match="sample").json()
    assert [e["message"] for e in report["errors"]] == [f"Row 2 changes the same object (#{s1['_id']})."], report

    # --- mapping chosen by hand, CSV in the Turkish code page with semicolons ------
    csv_bytes = "Kod;Değer;Tür\r\nS-9;3,5;ÇEKME\r\n".encode("cp1254")
    report = send(csv_bytes, filename="sonuclar.csv").json()
    assert [c["field"] for c in report["columns"]] == ["", "value", ""], report["columns"]
    report = send(csv_bytes, filename="sonuclar.csv", mapping=["sample", "value", "type"], dry_run=False).json()
    assert report["imported"] and report["sheet"] == "sonuclar", report
    assert api("GET", url, "ua", 200, params={"sample": "S-9"}).json()["items"][0]["value"] == 3.5
    r = send(csv_bytes, filename="sonuclar.csv", expect=400, mapping=["sample", "sample", ""])
    assert r.json()["detail"] == "Columns A and B both go into Numune.", r.json()
    send(csv_bytes, filename="sonuclar.csv", expect=400, mapping=["sample", "photo", ""])   # uploads can't be imported
    assert "Excel 97-2003" in send(b"\xd0\xcf\x11\xe0" + b"\0" * 64, filename="old.xls", expect=400).json()["detail"]
    send(b"PK\x03\x04 not really a zip", expect=400)
    send(b"%PDF-1.7", filename="a.pdf", expect=400)
    report = send("Numune,Test türü\r\nS-30,Çekme\r\nS-31,Çekme,x\r\n".encode(), filename="wide.csv").json()
    assert (report["created"], report["errors"][0]["rows"]) == (1, [3]), report          # its cells have moved
    assert report["errors"][0]["message"].startswith("This row has more cells than the header row has columns")
    report = send(b'Numune;"Not, ek"\r\nS-32;x\r\n', filename="quoted.csv").json()   # the comma is in quotes
    assert [c["header"] for c in report["columns"]] == ["Numune", "Not, ek"], report["columns"]
    report = send(b"PK;Numune\r\n1;S-33\r\n", filename="pk.csv").json()                  # text, not a zip
    assert [c["field"] for c in report["columns"]] == ["", "sample"], report["columns"]
    report = send("Numune\tTest türü\r\nS-34\tÇekme\r\n".encode("utf-16"), filename="unicode.csv").json()
    assert ([c["field"] for c in report["columns"]], report["created"]) == (["sample", "type"], 1), report
    from openpyxl.chart import BarChart, Reference
    wb = openpyxl.Workbook()                             # a chart sheet first: not a sheet with cells
    wb.active.append(["Numune", "Test türü"])
    wb.active.append(["S-35", "Çekme"])
    chart = BarChart()
    chart.add_data(Reference(wb.active, min_col=1, min_row=1, max_row=2))
    wb.create_chartsheet("Chart", 0).add_chart(chart)
    buffer = io.BytesIO()
    wb.save(buffer)
    report = send(buffer.getvalue()).json()
    assert (report["sheets"], report["created"]) == (["Sheet"], 1), report

    # --- sheets: the first one Atlas recognises, or the one asked for ---------------
    wb = openpyxl.Workbook()
    wb.active.append(["Notes"])
    wb.create_sheet("Data").append(["Numune", "Test türü"])
    wb["Data"].append(["S-10", "Sertlik"])
    buffer = io.BytesIO()
    wb.save(buffer)
    report = send(buffer.getvalue()).json()
    assert (report["sheet"], report["sheets"], report["created"]) == ("Data", ["Sheet", "Data"], 1), report
    assert send(buffer.getvalue(), sheet="Sheet").json()["rows"] == 0
    send(buffer.getvalue(), sheet="Nope", expect=400)

    # --- who may: Data Admins and Superadmins; everyone else adds objects in the form --------
    for user in ("ua", "uq"):
        r = send(book([["Numune", "Test türü"], ["S-X", "Çekme"]]), user=user, expect=403)
        assert r.json()["detail"] == "Only Data Admins and Superadmins can import.", r.json()
        api("GET", f"{API}/results/import/template.xlsx", user, 403)
    assert send(book([["Numune", "Test türü"], ["S-X", "Çekme"]]), user="sa").json()["created"] == 1

    # --- roles, written as links are: names or #id, separated by semicolons ---------------------
    # (lab_b was deleted in run(): lab_a and quality are left)
    s7 = api("GET", url, "ua", 200, params={"sample": "S-7"}).json()["items"][0]
    picks = book([["ID", "Roles"], [s1["_id"], "lab_a; QUALITY"], [s2["_id"], f"#{role['quality']}"],
                  [s7["_id"], "lab_a, quality"]])                          # commas, as the grid shows them
    report = send(picks, dry_run=False).json()
    assert [c["field"] for c in report["columns"]] == ["_id", "_roles"] and report["updated"] == 3, report
    assert [api("GET", f"{url}/{o['_id']}", "da", 200).json()["_roles"] for o in (s1, s2, s7)] == [
        [role["lab_a"], role["quality"]], [role["quality"]], [role["lab_a"], role["quality"]]]
    r = api("GET", f"{url}.xlsx", "da", 200, params={"fields": "_id,sample,_roles", "sort": "sample"})
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    assert [ws["C2"].value, ws["C3"].value] == ["lab_a; quality", "quality"], [ws["C2"].value, ws["C3"].value]
    assert send(r.content).json()["unchanged"] == 4                         # an export imports back as it is
    report = send(book([["ID", "Roles"], [s1["_id"], "lab_c"], [s2["_id"], f"#{role['lab_b']}"]])).json()
    assert [e["message"] for e in report["errors"]] == ["Roles: there is no role 'lab_c'.",
                                                        f"Roles: there is no role '#{role['lab_b']}'."], report
    report = send(book([["Numune", "Test türü", "Roles"], ["S-11", "Çekme", "Quality"]]), dry_run=False).json()
    assert report["created"] == 1, report
    assert api("GET", url, "da", 200, params={"sample": "S-11"}).json()["items"][0]["_roles"] == [role["quality"]]
    assert api("GET", url, "ua", 200, params={"sample": "S-11"}).json()["total"] == 0     # ua holds lab_a only
    report = send(book([["ID", "Roles"], [s1["_id"], None], [s2["_id"], " "], [s7["_id"], None]]),
                  dry_run=False).json()                                     # empty cells clear them
    assert report["updated"] == 3, report
    assert [api("GET", f"{url}/{o['_id']}", "da", 200).json()["_roles"] for o in (s1, s2, s7)] == [[], [], []]

    # --- the template: a guide first, then the sheet to fill in -----------------------
    r = api("GET", f"{API}/results/import/template.xlsx", "da", 200)
    assert "Results import template.xlsx" in r.headers["content-disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Read me first", "Results"]
    ws = wb["Results"]
    assert [c.value for c in ws[1]] == ["Numune", "Değer", "Tarih", "Geçti", "Sınıf", "Okumalar", "Eğri",
                                        "Test türü", "İlgili", "Roles"]
    assert [c.value for c in ws[1] if c.fill.fgColor.rgb.endswith("FFF2CC")] == ["Test türü"]    # required: yellow
    assert [ws.column_dimensions[c].number_format for c in "ABC"] == ["@", "@", "yyyy-mm-dd"]  # text stays text
    rules = {str(dv.sqref).split(":")[0].rstrip("0123456789"): dv for dv in ws.data_validations.dataValidation}
    assert len(rules) == 10 and all(dv.prompt and dv.showInputMessage for dv in rules.values())   # a note per column
    assert (rules["C"].type, rules["D"].formula1, rules["E"].formula1) == ("date", '"Yes,No"', '"Pass,Fail"')
    assert rules["A"].prompt.startswith("Any text. Every object needs a different one. Example: "), rules["A"].prompt
    assert rules["H"].prompt == "Must be filled in. The title of a Test types object, or its ID as #12. Example: Darbe"
    guide = list(wb["Read me first"].iter_rows(values_only=True))
    assert guide[4][0] == "1. Click the tab 'Results' at the bottom of the window. That is the sheet you fill in."
    start = next(n for n, row in enumerate(guide) if row[0] == "Column") + 1
    how = {row[0]: row[1:] for row in guide[start:start + 10]}      # column -> (must fill in, what to write, example)
    assert list(how) == [c.value for c in ws[1]], how
    assert how["Test türü"] == ("Yes", "The title of one Test types object, as Atlas shows it. If two Test types "
                                       "objects have that title, write the ID instead, as #12.", "Darbe"), how
    assert (how["Tarih"][2], how["Geçti"][2]) == ("2026-11-25", "Yes"), how       # examples from Atlas
    assert "Strain; Stress" in how["Eğri"][1] and how["İlgili"][1].endswith("a row above in this file."), how
    assert how["Roles"][1].startswith("Usually empty: then everyone can work with the object."), how["Roles"]
    assert how["Roles"][1].endswith("Roles: lab_a; quality."), how["Roles"]
    ws.append(["S-T", "2,5", datetime.date(2026, 11, 26), "Yes", "Pass", "1; 2", "1; 2 | 3; 4", "Çekme"])
    buffer = io.BytesIO()
    wb.save(buffer)
    report = send(buffer.getvalue()).json()             # filled in, it imports: the data sheet, not the guide
    assert (report["sheet"], report["created"], report["failed"]) == ("Results", 1, 0), report

    # --- a big sheet ---------------------------------------------------------------
    big = [["Numune", "Değer", "Okumalar", "Test türü"]] + [[f"B-{n:05d}", n / 4, "1; 2; 3", "Çekme"]
                                                             for n in range(5000)]
    started = time.perf_counter()
    report = send(book(big), dry_run=False).json()
    took = time.perf_counter() - started
    assert report["created"] == 5000 and report["imported"], report
    assert took < 30, f"importing 5,000 rows took {took:.1f} s"
    print(f"  import: 5,000 rows in {took:.1f} s")

    # --- attributes called Version and ID: the system columns are "… (Atlas)" then -------
    api("POST", API, "da", 201, json={"key": "docs", "label": "Docs", "attributes": [
        {"key": "code", "label": "Code", "title": True}, {"key": "rev", "label": "Version"},
        {"key": "ref", "label": "ID"}]})
    docs = f"{API}/docs"
    filled = book([["Code", "Version", "ID"], ["D-1", "B", "77"]], "Docs")
    report = api("POST", f"{docs}/import", "da", 200, files={"file": ("docs.xlsx", filled)},
                 data={"dry_run": "false"}).json()
    assert ([c["field"] for c in report["columns"]], report["created"]) == (["code", "rev", "ref"], 1), report
    assert (report["fields"]["_id"], report["fields"]["_version"]) == ("ID (Atlas)", "Version (Atlas)"), report
    r = api("GET", f"{docs}/objects.xlsx", "da", 200, params={"fields": "_id,code,rev,ref,_version"})
    assert [c.value for c in openpyxl.load_workbook(io.BytesIO(r.content)).active[1]] == [
        "ID (Atlas)", "Code", "Version", "ID", "Version (Atlas)"]
    assert api("POST", f"{docs}/import", "da", 200, files={"file": ("docs.xlsx", r.content)}).json()["unchanged"] == 1
    api("DELETE", f"{docs}?force=true", "da", 204)

    api("DELETE", f"{API}/results?force=true", "da", 204)
    api("DELETE", f"{API}/types?force=true", "da", 204)


def run_users(api):
    """User attributes: a portal user's id, names read from the portal, filters, search,
    sort, look-throughs, unique, export and import, type changes, and deleting a user."""
    import openpyxl

    def book(rows):
        wb = openpyxl.Workbook()
        wb.active.title = "Runs"
        for row in rows:
            wb.active.append(row)
        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()

    with Session(engine) as s:     # names can be shared ("ub Test" twice), usernames can't
        for username, name, surname, active in (("ub2", "ub", "Test", True), ("tech", "Ayşe", "Yılmaz", False)):
            s.add(User(username=username, name=name, surname=surname, email=f"{username}@example.com",
                       usertype=UserType.regular, is_active=active, hashed_pw=get_password_hash(PASSWORD),
                       created_by="test", last_modified_by="test"))
        s.commit()
    people = {u["username"]: u for u in api("GET", "/atlas/api/users", "ua", 200).json()}
    assert people["ua"]["name"] == "ua Test" and people["ua"]["active"], people["ua"]
    assert (people["tech"]["name"], people["tech"]["active"]) == ("Ayşe Yılmaz", False), people["tech"]
    ua, ub, ub2, tech = (people[n]["id"] for n in ("ua", "ub", "ub2", "tech"))

    api("POST", API, "da", 201, json={"key": "machines", "label": "Machines", "attributes": [
        {"key": "code", "label": "Code", "title": True}, {"key": "owner", "label": "Owner", "type": "user"}]})
    api("POST", API, "da", 201, json={"key": "runs", "label": "Runs", "attributes": [
        {"key": "sample", "label": "Sample", "title": True},
        {"key": "technician", "label": "Technician", "type": "user", "required": True, "title": True},
        {"key": "checker", "label": "Checker", "type": "user"},
        {"key": "machine_owner", "label": "Machine owner", "type": "lookthrough", "lookup": "machine.owner"}],
        "relationships": [{"key": "machine", "label": "Machine", "target": "machines", "cardinality": "one"}]})
    url, machines_url = f"{API}/runs/objects", f"{API}/machines/objects"

    # --- writing: an id or a username; names when nobody else has them ----------------
    r1 = api("POST", url, "ua", 201, json={"sample": "R-1", "technician": "ua", "checker": ub}).json()
    assert (r1["technician"], r1["checker"], r1["_title"]) == (ua, ub, "R-1 · ua Test"), r1
    r2 = api("POST", url, "ua", 201, json={"sample": "R-2", "technician": "Ayşe Yılmaz"}).json()   # inactive: fine
    assert r2["technician"] == tech, r2
    for value, message in (("ub Test", "Technician: 2 users are named 'ub Test' (ub, ub2); write the username."),
                           ("nobody", "Technician: there is no user 'nobody'."),
                           (99999, "Technician: there is no user #99999."),
                           (None, "Required: Technician.")):
        r = api("POST", url, "ua", 400, json={"sample": "R-X", "technician": value})
        assert r.json()["detail"] == message, r.json()

    # --- filters, search and sort by person ----------------------------------------------
    def samples(**params):
        return [i["sample"] for i in api("GET", url, "ua", 200, params={"sort": "sample", **params}).json()["items"]]

    assert samples(technician="ua") == ["R-1"] and samples(technician=tech) == ["R-2"]
    assert samples(technician__in=f"ua,{tech}") == ["R-1", "R-2"] and samples(technician__ne="ua") == ["R-2"]
    assert samples(technician__contains="yilmaz") == ["R-2"] and samples(checker__isnull="true") == ["R-2"]
    assert samples(q="yılmaz") == ["R-2"] and samples(q="ub") == ["R-1"]
    api("GET", url, "ua", 400, params={"technician__gt": "1"})
    by_name = api("GET", url, "ua", 200, params={"sort": "technician"}).json()["items"]
    assert [i["sample"] for i in by_name] == ["R-2", "R-1"], by_name       # Ayşe Yılmaz, then ua Test

    # --- a look-through to a user, and a unique user that titles its object ---------------
    machine = api("POST", machines_url, "ua", 201, json={"code": "M-1", "owner": "ub"}).json()
    api("PATCH", f"{url}/{r1['_id']}", "ua", 200, json={"machine": machine["_id"]})
    assert api("GET", f"{url}/{r1['_id']}", "ua", 200).json()["machine_owner"] == ub
    assert samples(machine_owner="ub") == ["R-1"] and samples(machine_owner__contains="ub test") == ["R-1"]
    api("POST", API, "da", 201, json={"key": "profiles", "label": "Profiles", "attributes": [
        {"key": "person", "label": "Person", "type": "user", "unique": True, "title": True}]})
    profile = api("POST", f"{API}/profiles/objects", "ua", 201, json={"person": "ua"}).json()
    assert profile["_title"] == "ua Test", profile
    r = api("POST", f"{API}/profiles/objects", "ua", 409, json={"person": ua})
    assert r.json()["detail"] == "Person must be unique: 'ua Test' is already used.", r.json()

    # --- export as "name (username)", which an import reads back ------------------------
    r = api("GET", f"{url}.xlsx", "ua", 200, params={"fields": "_id,sample,technician,checker,machine_owner",
                                                    "sort": "sample"})
    rows = list(openpyxl.load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True))
    assert rows[1][2:] == ("ua Test (ua)", "ub Test (ub)", "ub Test (ub)"), rows[1]
    assert rows[2][2:] == ("Ayşe Yılmaz (tech)", None, None), rows[2]

    def send(data, **form):
        return api("POST", f"{API}/runs/import", "da", 200, files={"file": ("runs.xlsx", data)},
                   data={k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()}).json()

    assert send(r.content)["unchanged"] == 2
    report = send(book([["ID", "Checker"], [r1["_id"], "ub2"], [r2["_id"], "nobody"]]))
    assert [e["message"] for e in report["errors"]] == ["Checker: there is no user 'nobody'."], report
    report = send(book([["ID", "Checker"], [r1["_id"], "ub2"], [r2["_id"], "AYŞE YILMAZ"]]), dry_run=False)
    assert report["updated"] == 2, report
    assert [api("GET", f"{url}/{o['_id']}", "ua", 200).json()["checker"] for o in (r1, r2)] == [ub2, tech]
    wb = openpyxl.load_workbook(io.BytesIO(api("GET", f"{API}/runs/import/template.xlsx", "da", 200).content))
    how = {row[0]: row[1:] for row in wb["Read me first"].iter_rows(values_only=True) if row[0]}
    assert how["Technician"][1].startswith("A person: the username they sign in with"), how["Technician"]
    assert how["Technician"][2] == "da", how["Technician"]              # the example: whoever downloads it

    # --- type changes: text that names people becomes users, and back -----------------
    api("POST", API, "da", 201, json={"key": "notes", "label": "Notes", "attributes": [{"key": "who", "label": "Who"}]})
    notes_url = f"{API}/notes/objects"
    notes = [api("POST", notes_url, "ua", 201, json={"who": who}).json()["_id"] for who in ("ua", "Ayşe Yılmaz", "nobody")]
    as_user = {"label": "Notes", "attributes": [{"key": "who", "label": "Who", "type": "user"}]}
    r = api("PUT", f"{API}/notes", "da", 409, json=as_user)
    assert r.json()["warnings"] == ["Changing 'Who' to user clears 1 value(s) that don't fit."], r.json()
    api("PUT", f"{API}/notes?force=true", "da", 200, json=as_user)
    assert [api("GET", f"{notes_url}/{n}", "ua", 200).json()["who"] for n in notes] == [ua, tech, None]
    api("PUT", f"{API}/notes", "da", 200, json={"label": "Notes", "attributes": [{"key": "who", "label": "Who"}]})
    assert [api("GET", f"{notes_url}/{n}", "ua", 200).json()["who"] for n in notes] == ["ua Test", "Ayşe Yılmaz", None]

    # --- the portal keeps a user Atlas names (deactivate them instead) ----------------------
    r = api("DELETE", "/users_and_permissions/users/tech", "sa", 409)
    assert r.json()["detail"] == ("Atlas names tech in Runs › Technician (1 object); Runs › Checker (1 object). "
                                  "Deactivate the account instead, or clear those fields first."), r.json()
    api("PATCH", f"{url}/{r2['_id']}", "ua", 200, json={"technician": "ua", "checker": None})
    api("DELETE", "/users_and_permissions/users/tech", "sa", 204)
    assert "tech" not in {u["username"] for u in api("GET", "/atlas/api/users", "ua", 200).json()}

    for key in ("runs", "machines", "profiles", "notes"):
        api("DELETE", f"{API}/{key}?force=true", "da", 204)


def test_atlas():
    try:
        make_v1_atlas_db()                   # starting the app must bring it up to date
        with TestClient(app) as client:
            check_migrated()
            role = seed_portal()
            api = Api(client)
            for username in ("sa", "da", "ua", "ub", "uq", "un", "ux"):
                api.login(username)
            run(api, role)
            run_files(api, role)
            run_export(api)
            run_series(api)
            run_import(api, role)
            run_users(api)
            check_views_follow_files()
    finally:
        engine.dispose()
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    test_atlas()
    print("Atlas: all checks passed.")
