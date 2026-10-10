"""Hidden collections leak nothing; revocation is immediate; a corrected source invalidates what was derived from it."""

import importlib.util
import json

import pytest

import pdfs
from olib import api, extract, index, shelf as shelfmod
from conftest import ROOT

SECRET = "xylophonist"      # a word only the hidden source has (it is queried)
UNSEEN = "marimbaphone"     # another hidden word, never queried: it must never appear in any answer


def _hidden_library(lib):
    lib.collections = {"public": {"visibility": "public"},
                       "inner": {"visibility": "swarms", "swarms": ["s_inner"]}}
    lib.source("open-doc", [lib.file("open.pdf", pdfs.text_pdf(["public words about revision"]))], title="Open doc")
    lib.source("inner-doc", [lib.file("inner.pdf", pdfs.text_pdf([f"{SECRET} {UNSEEN} revision notes", "second"]))],
               collection="inner", title="Inner doc title")
    lib.build()
    return shelfmod.Shelf(lib.data).manifest()["entries"]["inner-doc"]["sha256"]


def load_program(monkeypatch, data):
    monkeypatch.setenv("OMEGALIBRARY_DATA", str(data))
    spec = importlib.util.spec_from_file_location("olib_program_under_test", ROOT / "app" / "program.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Ctx:
    def __init__(self, swarm, actor=None, viewer=None):
        self.swarm_id, self.actor, self.viewer = swarm, actor, viewer


def test_hidden_collection_reveals_no_snippets_or_counts(lib, monkeypatch):
    inner_sha = _hidden_library(lib)
    outsider, insider = "s_other", "s_inner"

    # search: no hit, no count, not even for words only the hidden source has
    for words in (SECRET, "revision"):
        res = api.search(lib.data, outsider, words)
        assert all(h["source_sha256"] != inner_sha for h in res["hits"])
        assert UNSEEN not in json.dumps(res) and SECRET not in json.dumps(res["hits"])
    assert api.search(lib.data, outsider, SECRET)["total"] == 0
    assert api.search(lib.data, outsider, "revision")["total"] == 1          # only the public page counts
    assert api.search(lib.data, insider, "revision")["total"] == 2
    assert api.search(lib.data, insider, SECRET)["hits"][0]["source_sha256"] == inner_sha

    # shelf: no entry, and the count is of visible sources only
    out_list = api.shelf_list(lib.data, outsider)
    assert out_list["count"] == 1 and "Inner doc title" not in json.dumps(out_list)
    assert api.shelf_list(lib.data, insider)["count"] == 2

    # read: a hidden source answers exactly like one that does not exist
    with pytest.raises(api.NotAvailable):
        api.read(lib.data, outsider, inner_sha, 1)
    with pytest.raises(api.NotAvailable):
        api.read(lib.data, outsider, "0" * 64, 1)
    assert SECRET in api.read(lib.data, insider, inner_sha, 1)["text"]

    # the app's views, reads and mind actions say nothing either
    prog = load_program(monkeypatch, lib.data)
    for graph in (prog.view(Ctx(outsider)), prog.view(Ctx(outsider), f"q:{SECRET}"),
                  prog.view(Ctx(outsider), f"page:{inner_sha}:1"), prog.view(Ctx(outsider), f"src:{inner_sha}"),
                  prog.view(Ctx(outsider), f"pages:{inner_sha}:1-2")):
        text = json.dumps(graph)
        assert UNSEEN not in text and "Inner doc title" not in text and inner_sha not in text
    assert prog.view(Ctx(outsider), f"page:{inner_sha}:1") == prog.view(Ctx(outsider), f"page:{'0' * 64}:1")
    assert prog.read(Ctx(outsider), "read", {"sha256": inner_sha, "page": 1}) == {"status": "not_available"}
    mind = {"kind": "mind", "id": "a_1", "swarm_id": outsider}
    import asyncio
    r = asyncio.run(prog.act(Ctx(outsider, mind), "library_search", [], {"query": SECRET}))
    assert r["detail"]["total"] == 0 and r["detail"]["hits"] == [] and UNSEEN not in json.dumps(r)
    r = asyncio.run(prog.act(Ctx(outsider, mind), "library_read", [], {"sha256": inner_sha, "page": 1}))
    assert r["status"] == "refused" and UNSEEN not in json.dumps(r) and SECRET not in json.dumps(r)

    # changing hidden content does not move an outsider's revision (no side channel through it)
    before = api.revision(lib.data, outsider)
    lib.source("inner-doc", [lib.file("inner.pdf", pdfs.text_pdf([f"{SECRET} corrected", "second"]))],
               collection="inner", title="Inner doc title")
    lib.build()
    assert api.revision(lib.data, outsider) == before
    assert api.revision(lib.data, insider) != before


def test_revoking_a_swarm_takes_effect_on_the_next_call(lib):
    inner_sha = _hidden_library(lib)
    assert api.search(lib.data, "s_inner", SECRET)["total"] == 1
    lib.set_collections({"public": {"visibility": "public"}, "inner": {"visibility": "swarms", "swarms": []}})
    # no rebuild, no restart: search, read and the shelf listing all stop at once
    assert api.search(lib.data, "s_inner", SECRET) ["total"] == 0
    with pytest.raises(api.NotAvailable):
        api.read(lib.data, "s_inner", inner_sha, 1)
    assert api.shelf_list(lib.data, "s_inner")["count"] == 1
    # an unknown or malformed policy fails closed
    lib.set_collections({"public": {"visibility": "public"}, "inner": "everyone"})
    assert api.search(lib.data, "s_inner", SECRET)["total"] == 0


def test_a_corrected_source_invalidates_its_derived_pages(lib):
    lib.source("paper", [lib.file("paper.pdf", pdfs.text_pdf(["the erratum gnomon value is 7"]))], title="Paper")
    lib.build()
    old = shelfmod.Shelf(lib.data).manifest()["entries"]["paper"]["sha256"]
    assert api.search(lib.data, None, "gnomon")["total"] == 1

    lib.file("paper.pdf", pdfs.text_pdf(["the corrected value is 9"]))           # the source is corrected in place
    report = lib.ingest()
    entry = shelfmod.Shelf(lib.data).manifest()["entries"]["paper"]
    new = entry["sha256"]
    assert report["superseded"] == [{"id": "paper", "old": old, "new": new}]
    assert entry["history"][0]["sha256"] == old and entry["history"][0]["status"] == "superseded"

    # before any rebuild: the index is stale and the old pages are no longer served as current
    assert index.state(lib.data)["state"] == "stale"
    assert api.search(lib.data, None, "gnomon")["total"] == 0
    lib.extract()
    lib.rebuild()
    assert api.search(lib.data, None, "gnomon")["total"] == 0
    assert api.search(lib.data, None, "corrected")["hits"][0]["source_sha256"] == new
    db = index.connect(lib.data)
    for tbl in index.tables(db).values():
        assert db.execute(f"SELECT count(*) FROM {tbl} WHERE sha256 = ?", (old,)).fetchone()[0] == 0
    db.close()
    # an old citation still resolves to the exact old original, marked superseded with its replacement
    page = api.read(lib.data, None, old, 1)
    assert page["status"] == "superseded" and page["superseded"]["superseded_by"] == new
    assert "gnomon" in page["text"] and page["original_verified"]


def test_a_changed_extractor_serves_no_old_text_and_citations_agree(lib, monkeypatch):
    lib.source("p", [lib.file("p.pdf", pdfs.text_pdf(["intro", "the ocelot clause"]))])
    lib.build()
    hit = api.search(lib.data, None, "ocelot")["hits"][0]
    assert api.read(lib.data, None, hit["source_sha256"], hit["page"])["text"].strip().endswith("ocelot clause")
    monkeypatch.setattr(extract, "bundle_digest", lambda *a, **k: "f" * 64)     # a new extractor generation
    res = api.search(lib.data, None, "ocelot")
    assert res["hits"] == [] and res["total"] == 0 and res["index"]["state"] == "needs_rebuild"
    with pytest.raises(api.NotAvailable):                                     # read agrees: nothing under the new extractor
        api.read(lib.data, None, hit["source_sha256"], hit["page"])
    assert index.state(lib.data)["state"] == "stale"
    lib.extract()
    lib.rebuild()
    res = api.search(lib.data, None, "ocelot")
    assert res["index"]["state"] == "fresh" and res["hits"][0]["page"] == 2
    page = api.read(lib.data, None, res["hits"][0]["source_sha256"], 2)
    assert page["citation"]["bundle_digest"] == "f" * 64 and "ocelot" in page["text"]


def test_a_hit_that_no_longer_resolves_is_never_served(lib):
    lib.source("p", [lib.file("p.pdf", pdfs.text_pdf(["the ocelot clause"]))])
    lib.build()
    sha = api.search(lib.data, None, "ocelot")["hits"][0]["source_sha256"]
    extract.record_path(lib.data, sha, extract.bundle_digest()).unlink()      # the extraction behind the index is gone
    res = api.search(lib.data, None, "ocelot")
    assert res["hits"] == [] and res["index"]["state"] in ("stale", "needs_rebuild")
    with pytest.raises(api.NotAvailable):
        api.read(lib.data, None, sha, 1)
