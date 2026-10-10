"""The app inside a disposable in-process hive (the host's own TestClient fixture; no port, no live hive).

Qualifying host: an OmegaHive with the r15 read actions (the published omegahive has them). A host without r15 is
superseded for this app and the test skips there.

Runs only where the host source and its dependencies import (OMEGALIBRARY_HOST_SRC set to an OmegaHive checkout, and
PeTTa available); skipped otherwise. It proves the people routes and the mind route end to end: before an
attachment a mind is refused, after a reviewed attachment it can search and read, a hidden collection stays hidden,
and after revocation it is refused again.
"""

import hashlib
import inspect
import json
import subprocess
import sys

import pytest

import pdfs
from conftest import HOST_SRC, ROOT

try:
    sys.path.insert(0, str(HOST_SRC))
    from hive.tests.test_core_api import client, agent_headers   # noqa: F401  (host fixture)
except Exception as exc:   # host source or its dependencies missing in this interpreter
    pytest.skip(f"host test fixtures unavailable: {type(exc).__name__}", allow_module_level=True)


def host_commit():
    try:
        return subprocess.run(["git", "-C", str(HOST_SRC), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def host_has_read_actions():
    """r15 (Platform, readacts 9abe9df): Programs.act takes read_only, and effect "read" acts store only a digest."""
    from hive.core.programs import Programs
    return "read_only" in inspect.signature(Programs.act).parameters


def named_admin(client):
    """Synthetic signed-in admin in the disposable host (copied from hive/tests/test_app_attachments.py)."""
    db = client.app.state.hive.db
    db.insert("people", {"id": "u_fixture", "email": "invented@example.invalid", "name": "Invented admin",
                         "role": "admin", "created_at": "fixture"})
    db.execute("UPDATE sessions SET kind='sso',principal='user:u_fixture' WHERE token_hash=?",
               (hashlib.sha256(client.operator_token.encode()).hexdigest(),))


def test_people_and_attached_minds_through_the_real_host(client, lib, monkeypatch):
    lib.collections = {"public": {"visibility": "public"}, "inner": {"visibility": "swarms", "swarms": []}}
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(["intro", "the wombatine revision rule"]))], title="Doc")
    lib.source("inner", [lib.file("inner.pdf", pdfs.text_pdf(["wombatine marimbaphone"]))], collection="inner")
    lib.build()
    monkeypatch.setenv("OMEGALIBRARY_DATA", str(lib.data))
    monkeypatch.setenv("HIVE_PLUGIN_DIRS", str(ROOT / "app"))
    assert client.post("/api/programs/reload").status_code == 200
    info = client.get("/api/programs/omegalibrary").json()
    assert info["error"] is None, info["error"]

    swarm = client.post("/api/swarms", json={"name": "Readers"}).json()
    mind = client.post("/api/agents", json={"name": "Reader", "swarm_id": swarm["id"]}).json()
    lib.set_collections({"public": {"visibility": "public"}, "inner": {"visibility": "swarms", "swarms": []}})

    # people: view and declared reads through the host routes
    v = client.post("/api/programs/omegalibrary/view", json={"swarm_id": swarm["id"]})
    assert v.status_code == 200 and [i["label"] for i in v.json()["items"]] == ["Doc"]
    r = client.post("/api/programs/omegalibrary/read", json={"swarm_id": swarm["id"], "query": "search",
                                                             "params": {"query": "wombatine"}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["total"] == 1 and res["hits"][0]["page"] == 2 and "marimbaphone" not in r.text

    def mind_call(action, params, key):
        return client.post("/api/agent/programs/omegalibrary/act", headers=agent_headers(mind),
                           json={"action": action, "items": [], "params": params, "idempotency_key": key})

    assert mind_call("library_search", {"query": "wombatine"}, "k0").status_code == 403    # no attachment yet

    named_admin(client)
    assert client.post("/api/programs/omegalibrary/activations", json={"swarm_id": swarm["id"]}).status_code == 200
    offered = client.post("/api/programs/omegalibrary/offers", json={"mind_id": mind["id"]}).json()
    assert offered["actions_offered"] == ["library_search", "library_read"]
    at = client.post("/api/programs/omegalibrary/attachments", json={
        "mind_id": mind["id"], "actions": ["library_search", "library_read"], "channels": [],
        "policy": {"library_search": "allow", "library_read": "allow"}, "idempotency_key": "olib-review-1"})
    assert at.status_code == 200 and at.json()["status"] == "active", at.text

    s = mind_call("library_search", {"query": "wombatine"}, "k1")
    assert s.status_code == 200 and s.json()["status"] == "done", s.text
    hits = s.json()["detail"]["hits"]
    assert len(hits) == 1 and set(hits[0]) == {"source_sha256", "title", "page", "snippet"}
    assert "marimbaphone" not in s.text and s.json()["detail"]["total"] == 1
    rd = mind_call("library_read", {"sha256": hits[0]["source_sha256"], "page": 2}, "k2")
    assert rd.status_code == 200 and "wombatine revision rule" in rd.json()["detail"]["text"]

    rows = client.app.state.hive.db.all("SELECT result FROM program_actions WHERE program = 'omegalibrary'")
    print(f"host commit {host_commit()}; r15 read actions: {host_has_read_actions()}")
    if host_has_read_actions():
        # r15 host: no disclosed content is retained, only {redacted, digest, status}; the app ran read-only
        assert rows and all(json.loads(row["result"])["redacted"] == "read" for row in rows), rows
        assert not any("wombatine" in (row["result"] or "") or "Doc" in (row["result"] or "") for row in rows)
        assert s.json()["detail"]["host_read_only"] is True and rd.json()["detail"]["host_read_only"] is True
    else:
        pytest.skip(f"host {host_commit()} has no r15 read actions: superseded (it retains mind results); "
                    "qualify only against the live release main a17bdda or later")

    if host_has_read_actions():
        # a repeat with the same key re-runs the read under current grants: hide the collection and it discloses nothing
        lib.set_collections({"public": {"visibility": "swarms", "swarms": []}, "inner": {"visibility": "swarms", "swarms": []}})
        again = mind_call("library_search", {"query": "wombatine"}, "k1")
        assert again.status_code == 200 and again.json()["detail"]["hits"] == [] and "wombatine revision" not in again.text
        lib.set_collections({"public": {"visibility": "public"}, "inner": {"visibility": "swarms", "swarms": []}})
    assert client.delete(f"/api/programs/omegalibrary/attachments/{at.json()['id']}").json()["status"] == "revoked"
    assert mind_call("library_search", {"query": "wombatine"}, "k3").status_code == 403
    assert mind_call("library_search", {"query": "wombatine"}, "k1").status_code == 403   # a retry is refused too
