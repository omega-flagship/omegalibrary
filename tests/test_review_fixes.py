"""Review fixes, 2026-10-07 (stand-in and Kimi FIX-FIRST on slice 1)."""

import asyncio
import base64
import json

import pytest

import pdfs
from conftest import ROOT
from olib import api, extract, grants as collections, shelf as shelfmod
from test_access_and_corrections import Ctx, load_program

OUT, IN = "s_outside", "s_inside"


def _two_collections(lib):
    lib.collections = {"public": {"visibility": "public"}, "inner": {"visibility": "swarms", "swarms": [IN]}}


def test_a_hidden_byte_twin_is_never_named_to_a_public_viewer(lib, monkeypatch):
    _two_collections(lib)
    same = pdfs.text_pdf(["shared bytes about lemurs"])
    lib.source("pub", [lib.file("pub.pdf", same)], title="Public copy")
    lib.source("secret-twin", [lib.file("twin.pdf", same)], collection="inner", title="Secret twin title")
    lib.build()
    sha = shelfmod.Shelf(lib.data).manifest()["entries"]["pub"]["sha256"]
    out_list = api.shelf_list(lib.data, OUT)
    assert out_list["count"] == 1 and out_list["sources"][0]["same_bytes_as"] == []
    prog = load_program(monkeypatch, lib.data)
    answers = [out_list, api.search(lib.data, OUT, "lemurs"), api.read(lib.data, OUT, sha, 1),
               prog.view(Ctx(OUT)), prog.view(Ctx(OUT), f"src:{sha}"), prog.view(Ctx(OUT), "q:lemurs"),
               prog.read(Ctx(OUT), "shelf", {})]
    for a in answers:
        text = json.dumps(a)
        assert "secret-twin" not in text and "Secret twin title" not in text
    assert api.search(lib.data, OUT, "lemurs")["hits"][0]["title"] == "Public copy"
    assert api.shelf_list(lib.data, IN)["sources"][0]["same_bytes_as"] in (["secret-twin"], ["pub"])


def test_changing_only_a_hidden_source_moves_nothing_a_public_viewer_sees(lib):
    _two_collections(lib)
    lib.source("pub", [lib.file("pub.pdf", pdfs.text_pdf(["public page"]))])
    lib.source("hid", [lib.file("hid.pdf", pdfs.text_pdf(["hidden v1"]))], collection="inner")
    lib.build()
    before = (api.revision(lib.data, OUT), api.visible_state(lib.data, OUT), api.shelf_list(lib.data, OUT))
    in_before = api.revision(lib.data, IN)
    assert before[1]["state"] == "fresh"
    # correct the hidden source and add another hidden one, WITHOUT rebuilding: the interval Kimi named
    lib.file("hid.pdf", pdfs.text_pdf(["hidden v2"]))
    lib.source("hid2", [lib.file("hid2.pdf", pdfs.text_pdf(["another"]))], collection="inner")
    lib.ingest()
    after = (api.revision(lib.data, OUT), api.visible_state(lib.data, OUT), api.shelf_list(lib.data, OUT))
    assert after == before
    assert api.visible_state(lib.data, IN)["state"] == "stale" and api.revision(lib.data, IN) != in_before
    lib.extract()
    lib.rebuild()
    assert (api.revision(lib.data, OUT), api.visible_state(lib.data, OUT)) == before[:2]


def test_local_imports_are_restricted_to_admins_and_granted_swarms(lib, monkeypatch):
    lib.collections = {"public": {"visibility": "public"},
                       "local": {"visibility": "swarms", "swarms": [], "admins": True}}
    lib.source("jons-pdf", [lib.file("j.pdf", pdfs.text_pdf(["capybara notes"]))], collection="local")
    lib.build()
    sha = shelfmod.Shelf(lib.data).manifest()["entries"]["jons-pdf"]["sha256"]
    assert api.search(lib.data, "s_any", "capybara")["total"] == 0
    assert api.search(lib.data, {"swarm_id": "s_any", "admin": True}, "capybara")["total"] == 1
    prog = load_program(monkeypatch, lib.data)
    admin_view = {"kind": "person", "role": "admin", "swarm_id": "s_any", "principal": "user:u_a"}
    member_view = dict(admin_view, role="member")
    assert prog.read(Ctx("s_any", viewer=admin_view), "search", {"query": "capybara"})["total"] == 1
    assert prog.read(Ctx("s_any", viewer=member_view), "search", {"query": "capybara"})["total"] == 0
    # a mind is never an admin, whatever its actor says
    mind = {"kind": "mind", "id": "a_1", "swarm_id": "s_any", "role": "admin"}
    r = asyncio.run(prog.act(Ctx("s_any", mind, viewer=admin_view), "library_search", [], {"query": "capybara"}))
    assert r["detail"]["total"] == 0
    # an explicit, audited grant opens it to one swarm; a revoke closes it on the next call
    collections.grant(lib.data, "local", "s_granted", by="test: lead-approved grant")
    assert api.search(lib.data, "s_granted", "capybara")["total"] == 1
    assert api.read(lib.data, "s_granted", sha, 1)["text"]
    collections.revoke(lib.data, "local", "s_granted", by="test")
    assert api.search(lib.data, "s_granted", "capybara")["total"] == 0
    audit = [json.loads(l) for l in (lib.data / "collections-audit.jsonl").read_text().splitlines()]
    assert [list(a["change"])[0] for a in audit][-2:] == ["grant", "revoke"] and audit[-1]["before"] != audit[-1]["after"]
    with pytest.raises(ValueError):
        collections.grant(lib.data, "public", "s_x", by="test")


def test_the_seed_keeps_local_pdfs_out_of_public():
    seed = json.loads((ROOT / "seed.json").read_text())
    assert seed["collections"]["local"] == {**seed["collections"]["local"], "visibility": "swarms", "swarms": [],
                                            "admins": True}
    for s in seed["sources"]:
        local = any("path" in l for l in s["locations"])
        assert s["collection"] == ("local" if local else "public"), s["id"]


def test_each_citation_names_a_host_authorised_download_of_the_exact_original(lib, monkeypatch):
    _two_collections(lib)
    blob = pdfs.text_pdf(["one", "two", "three"])
    lib.source("doc", [lib.file("doc.pdf", blob)])
    lib.source("hid", [lib.file("hid.pdf", pdfs.text_pdf(["h"]))], collection="inner")
    lib.build()
    m = shelfmod.Shelf(lib.data).manifest()["entries"]
    page = api.read(lib.data, OUT, m["doc"]["sha256"], 3)
    orig = page["citation"]["original"]
    assert orig["page"] == 3 and orig["download"]["query"] == "original" and orig["download"]["params"] == {
        "sha256": m["doc"]["sha256"]}
    prog = load_program(monkeypatch, lib.data)
    got = prog.read(Ctx(OUT), "original", {"sha256": m["doc"]["sha256"]})
    assert got["status"] == "ok" and got["mime"] == "application/pdf" and base64.b64decode(got["content_base64"]) == blob
    assert prog.read(Ctx(OUT), "original", {"sha256": m["hid"]["sha256"]}) == {"status": "not_available"}
    assert "physical page 3" in json.dumps(prog.view(Ctx(OUT), f"page:{m['doc']['sha256']}:3")["notes"])
    assert got["next_offset"] is None and got["offset"] == 0      # one chunk; larger originals: test_review_fixes_r3


class _CountingSandbox(extract.DirectTestRunner):
    kind = "host-sandbox"     # stands in for the production runner (the real one is tested in test_network_and_app)

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def run(self, *a):
        self.calls += 1
        return await super().run(*a)


def test_production_extraction_never_reuses_test_only_records(lib):
    lib.source("a", [lib.file("a.pdf", pdfs.text_pdf(["a"]))])
    lib.source("b", [lib.file("b.pdf", pdfs.text_pdf(["b"]))])
    lib.ingest()
    lib.extract()                                     # test-only direct records
    prod = _CountingSandbox()
    asyncio.run(extract.extract_all(lib.data, prod))
    assert prod.calls == 2                            # both redone by the production runner
    sha = shelfmod.Shelf(lib.data).manifest()["entries"]["a"]["sha256"]
    assert extract.load_record(lib.data, sha, extract.bundle_digest())["runner"] == "host-sandbox"
    again = _CountingSandbox()
    asyncio.run(extract.extract_all(lib.data, again))
    assert again.calls == 0                           # production records are reused


# ---- round 2 (Kimi + stand-in FIX-FIRST, 2026-10-07) -------------------------------------------------------------

def _rank_library(lib, hidden_pages):
    lib.collections = {"public": {"visibility": "public"}, "inner": {"visibility": "swarms", "swarms": [IN]}}
    # page A says alpha often, page B says beta often: which comes first depends on BM25's corpus statistics
    lib.source("pa", [lib.file("pa.pdf", pdfs.text_pdf(["alpha alpha alpha beta filler words here"]))], title="A")
    lib.source("pb", [lib.file("pb.pdf", pdfs.text_pdf(["beta beta beta alpha other filler text"]))], title="B")
    # the hidden source is added, grown, shrunk: its pages come and go (a shelf never deletes an entry; a corrected
    # source's old pages leave the index)
    lib.source("flood", [lib.file("flood.pdf", pdfs.text_pdf(hidden_pages or ["zzz"]))], collection="inner",
               title="Flood")
    lib.build()
    return {q: api.search(lib.data, OUT, q) for q in ("alpha beta", "alpha", "beta", "filler")}


def test_hidden_pages_never_change_visible_results_or_their_order(lib):
    base = _rank_library(lib, None)
    with_alpha = _rank_library(lib, ["alpha"] * 40 + ["alpha beta"] * 5)     # floods one term's statistics
    with_beta = _rank_library(lib, ["beta beta"] * 60)
    removed = _rank_library(lib, None)
    assert base["alpha beta"]["hits"], "the probe query must match"
    for answers in (with_alpha, with_beta, removed):
        assert answers == base                       # hits, snippets, order, totals, state: identical
    assert not any(h["title"] == "Flood" for h in api.search(lib.data, IN, "beta")["hits"])   # shrunk away
    _rank_library(lib, ["beta beta"] * 60)
    assert any(h["title"] == "Flood" for h in api.search(lib.data, IN, "beta")["hits"])     # insiders see it


def test_search_serves_no_citation_whose_original_fails_verification(lib):
    lib.source("good", [lib.file("good.pdf", pdfs.text_pdf(["pangolin one"]))], title="Good")
    lib.source("bad", [lib.file("bad.pdf", pdfs.text_pdf(["pangolin two"]))], title="Bad")
    lib.build()
    m = shelfmod.Shelf(lib.data).manifest()["entries"]
    assert api.search(lib.data, OUT, "pangolin")["total"] == 2
    blob = shelfmod.Shelf(lib.data).blob_path(m["bad"]["sha256"])
    blob.chmod(0o644)
    blob.write_bytes(pdfs.text_pdf(["pangolin tampered"]))           # tampered in place
    res = api.search(lib.data, OUT, "pangolin")
    assert [h["title"] for h in res["hits"]] == ["Good"] and res["total"] == 1
    assert res["withheld"] == [{"source_sha256": m["bad"]["sha256"], "reason": "original_unverified",
                                "message": res["withheld"][0]["message"]}]
    blob.unlink()                                                     # missing altogether
    res = api.search(lib.data, OUT, "pangolin")
    assert [h["title"] for h in res["hits"]] == ["Good"] and res["withheld"][0]["reason"] == "original_unverified"
    for h in res["hits"]:                                             # every served hit resolves
        assert api.read(lib.data, OUT, h["source_sha256"], h["page"])["original_verified"]


def test_a_grant_does_not_move_a_revision_through_index_freshness(lib):
    lib.collections = {"public": {"visibility": "public"}, "inner": {"visibility": "swarms", "swarms": []}}
    lib.source("pub", [lib.file("pub.pdf", pdfs.text_pdf(["public"]))])
    lib.source("hid", [lib.file("hid.pdf", pdfs.text_pdf(["hidden v1"]))], collection="inner")
    lib.build()
    lib.file("hid.pdf", pdfs.text_pdf(["hidden v2"]))                # a hidden change, extracted, not yet indexed
    lib.ingest()
    lib.extract()
    collections.grant(lib.data, "inner", OUT, by="test approver")
    assert api.visible_state(lib.data, OUT)["state"] == "stale"
    stale_rev = api.revision(lib.data, OUT)                          # granted while the index is stale
    lib.rebuild()
    assert api.visible_state(lib.data, OUT)["state"] == "fresh"
    assert api.revision(lib.data, OUT) == stale_rev                   # freshness never moves the revision


def test_ingest_creates_seed_collections_closed(tmp_path):
    from olib import fetch, grants, shelf
    f = tmp_path / "x.pdf"
    f.write_bytes(pdfs.text_pdf(["okapi"]))
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"schema": "omegalibrary.seed/1",
                                "collections": {"pre-granted": {"visibility": "swarms", "swarms": ["s_x"],
                                                                "admins": True, "label": "L"},
                                                "web": {"visibility": "public"}},
                                "sources": [{"id": "x", "kind": "pdf", "title": "x", "collection": "pre-granted",
                                             "locations": [{"path": str(f)}]}]}))
    data = tmp_path / "data"
    shelf.ingest(seed, data, fetch.Fetcher(offline=True))
    pol = grants.load(data)
    closed = {"visibility": "swarms", "swarms": [], "admins": False}
    assert pol == {"pre-granted": {**closed, "label": "L"}, "web": closed}
    asyncio.run(extract.extract_all(data, extract.DirectTestRunner()))
    from olib import index
    index.rebuild(data)
    for who in ("s_x", {"swarm_id": "s_x", "admin": True}, None):
        assert api.search(data, who, "okapi")["total"] == 0
    with pytest.raises(ValueError):
        grants.set_policy(data, "web", "public", False, by=" ")
    grants.set_policy(data, "pre-granted", "public", False, by="test approver")
    assert api.search(data, None, "okapi")["total"] == 1
    audit = [json.loads(l) for l in (data / "collections-audit.jsonl").read_text().splitlines()]
    assert audit[0]["change"] == {"created_closed": ["pre-granted", "web"]} and audit[-1]["by"] == "test approver"
