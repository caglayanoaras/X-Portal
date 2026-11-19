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
    """An atlas.db as the first version left it (atlas_class had no color or icon)."""
    conn = sqlite3.connect(os.environ["ATLAS_DB_PATH"])
    try:
        conn.executescript("""
            CREATE TABLE atlas_class (
                id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                path TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
                roles TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                updated_at TEXT NOT NULL, updated_by TEXT NOT NULL);
            PRAGMA user_version = 1;""")
    finally:
        conn.close()


def check_migrated():
    conn = sqlite3.connect(os.environ["ATLAS_DB_PATH"])
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(atlas_class)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert {"color", "icon"} <= columns and version == 2, (columns, version)


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
    finally:
        engine.dispose()
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    test_atlas()
    print("Atlas: all checks passed.")
