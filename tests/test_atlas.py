"""End-to-end check of the Atlas API on throwaway databases.

Runs the real app against a temporary portal DB and a temporary atlas.db, so
neither db.sqlite3 nor atlas.db is touched. Run from the repo root:

    venv\\Scripts\\python.exe tests\\test_atlas.py

(pytest also finds test_atlas if it is installed.)
"""
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
        headers = {"Accept": accept, "Authorization": f"Bearer {self.tokens[user]}"}
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


def ids(response):
    return sorted(item["_id"] for item in response.json()["items"])


def run(api, role):
    # --- access ----------------------------------------------------------------
    api("GET", API, "ux", 403)                                   # no Atlas module
    api("POST", API, "ua", 403, json={"key": "x", "label": "X"})  # not an admin
    r = api("GET", "/atlas/admin", "un", accept="text/html")
    assert r.status_code == 303 and r.headers["location"].endswith("/atlas/"), r.status_code
    api("GET", "/atlas/", "sa", 200, accept="text/html")
    api("GET", "/atlas/admin", "da", 200, accept="text/html")

    # --- definitions -----------------------------------------------------------
    bad = [
        {"key": "Bad Key", "label": "X"},
        {"key": "x", "label": "X", "attributes": [{"key": "sort", "label": "Sort"}]},
        {"key": "x", "label": "X", "attributes": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}]},
        {"key": "x", "label": "X", "attributes": [{"key": "a", "label": "A", "type": "lookthrough", "lookup": "nope.code"}]},
        {"key": "x", "label": "X", "roles": [999]},
        {"key": "x", "label": "X", "relationships": [{"key": "r", "label": "R", "target": "nowhere"}]},
    ]
    for body in bad:
        api("POST", API, "da", 400, json=body)

    labs = {"key": "labs", "label": "Laboratuvarlar", "attributes": [
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

    visible = [c["key"] for c in api("GET", API, "un", 200).json()]
    assert visible == ["labs", "batches", "locations"], visible   # tests: lab_a only
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
                         "label": "Receiving test of", "items": [{"_id": b1["_id"], "_title": "B-1"}]},
                        {"class": "batches", "class_label": "Batches", "relationship": "retests",
                         "label": "Retest of", "items": [{"_id": b2["_id"], "_title": "B-2"}]}]

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


def test_atlas():
    try:
        with TestClient(app) as client:
            role = seed_portal()
            api = Api(client)
            for username in ("sa", "da", "ua", "ub", "uq", "un", "ux"):
                api.login(username)
            run(api, role)
    finally:
        engine.dispose()
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    test_atlas()
    print("Atlas: all checks passed.")
