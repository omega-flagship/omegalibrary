"""No network in tests; the fetch scope is enforced; the app's contract, mind interface and limits."""

import asyncio
import json
import socket
import urllib.request

import pytest

import pdfs
from conftest import HOST_SRC, NetworkUsed
from olib import fetch, shelf as shelfmod
from test_access_and_corrections import Ctx, load_program


def test_no_network_is_used_in_tests():
    with pytest.raises(NetworkUsed):
        socket.create_connection(("hyperon.opencog.org", 443), timeout=1)
    with pytest.raises((NetworkUsed, Exception)):
        urllib.request.urlopen("https://hyperon.opencog.org/", timeout=1)
    with pytest.raises(fetch.FetchRefused):
        fetch.Fetcher(offline=True).get("https://hyperon.opencog.org/")


class FakeFetcher:
    """Serves canned bytes; records what was asked. Stands in for the network."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url):
        self.asked.append(url)
        return self.pages[url], {"url": url, "final_url": url, "http_status": 200, "content_type": "x",
                                 "fetched_at": "2026-10-06T00:00:00Z", "bytes": len(self.pages[url])}


def _seed(tmp_path, sources):
    p = tmp_path / "seed.json"
    p.write_text(json.dumps({"schema": "omegalibrary.seed/1", "collections": {"public": {"visibility": "public"}},
                             "sources": sources}))
    return p


def test_fetch_scope_is_only_seed_pages_and_pdfs_they_link(tmp_path):
    page = b'<html><a href="/papers/one.pdf">one</a> <a href="https://example.org/x.pdf">x</a></html>'
    ff = FakeFetcher({"https://hyperon.opencog.org/": page,
                      "https://hyperon.opencog.org/papers/one.pdf": pdfs.text_pdf(["one"]),
                      "https://example.org/x.pdf": pdfs.text_pdf(["x"]),
                      "https://example.org/not-linked.pdf": b"%PDF-"})
    sources = [
        {"id": "site", "kind": "html", "title": "s", "collection": "public",
         "locations": [{"url": "https://hyperon.opencog.org/"}]},
        {"id": "one", "kind": "pdf", "title": "1", "collection": "public",
         "locations": [{"url": "https://hyperon.opencog.org/papers/one.pdf", "linked_from": "site"}]},
        {"id": "x", "kind": "pdf", "title": "x", "collection": "public",
         "locations": [{"url": "https://example.org/x.pdf", "linked_from": "site"}]},
        {"id": "sneaky", "kind": "pdf", "title": "n", "collection": "public",
         "locations": [{"url": "https://example.org/not-linked.pdf", "linked_from": "site"}]},
        {"id": "other-site", "kind": "html", "title": "o", "collection": "public",
         "locations": [{"url": "https://example.org/"}]},
    ]
    report = shelfmod.ingest(_seed(tmp_path, sources), tmp_path / "data", ff)
    assert sorted(e["id"] for e in report["entries"]) == ["one", "site", "x"]
    assert sorted(e["id"] for e in report["errors"]) == ["other-site", "sneaky"]
    assert "https://example.org/not-linked.pdf" not in ff.asked and "https://example.org/" not in ff.asked
    m = shelfmod.Shelf(tmp_path / "data").manifest()["entries"]
    assert m["one"]["locations"][0]["linked_from"] == "site" and m["one"]["locations"][0]["fetched_at"]
    # a second ingest does not fetch again
    ff.asked.clear()
    shelfmod.ingest(_seed(tmp_path, sources[:3]), tmp_path / "data", ff)
    assert ff.asked == []


def test_describe_is_valid_for_the_host(monkeypatch, tmp_path):
    prog = load_program(monkeypatch, tmp_path)
    d = prog.describe()
    agents = {a["id"] for a in d["actions"] if a.get("agents") is True}
    assert agents == {"library_search", "library_read"}
    assert prog.offer(None, {"id": "a_1"}) == {"actions_offered": ["library_search", "library_read"],
                                               "channels_offered": []}
    assert prog.bind(None, {"id": "a_1"}, {}, "at_1") == {"actor": "library-reader:at_1"}
    assert prog.unbind(None, {}, "at_1") == {}
    if (HOST_SRC / "hive" / "core" / "programs.py").is_file():
        import sys
        sys.path.insert(0, str(HOST_SRC))
        from hive.core.programs import Programs
        Programs._check_description(d)       # the host's own load-time check


def test_views_validate_against_the_host_contract(lib, monkeypatch):
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(["a page about truth revision", "next"]))], title="Doc")
    lib.build()
    prog = load_program(monkeypatch, lib.data)
    sha = shelfmod.Shelf(lib.data).manifest()["entries"]["doc"]["sha256"]
    graphs = [prog.view(Ctx("s_x")), prog.view(Ctx("s_x"), "q:revision"), prog.view(Ctx("s_x"), f"src:{sha}"),
              prog.view(Ctx("s_x"), f"pages:{sha}:1-2"), prog.view(Ctx("s_x"), f"page:{sha}:1"),
              prog.view(Ctx("s_x"), "page:nonsense")]
    assert graphs[0] == prog.view(Ctx("s_x"))                    # stable ids and revision across views
    if not (HOST_SRC / "hive" / "core" / "programs.py").is_file():
        pytest.skip("host source not present")
    import sys
    sys.path.insert(0, str(HOST_SRC))
    from hive.core.programs import Programs, validate_graph
    desc = Programs._check_description(prog.describe())
    for g in graphs:
        validate_graph("omegalibrary", desc, json.loads(json.dumps(g)), "unfold", AssertionError)


def test_mind_actions_are_scoped_and_rate_limited(lib, monkeypatch):
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(["kumquat"]))])
    lib.build()
    prog = load_program(monkeypatch, lib.data)
    mind = {"kind": "mind", "id": "a_9", "swarm_id": "s_a"}
    r = asyncio.run(prog.act(Ctx("s_a", mind), "library_search", [], {"query": "kumquat"}))
    assert r["status"] == "done" and r["detail"]["hits"][0]["page"] == 1
    hit = r["detail"]["hits"][0]
    r = asyncio.run(prog.act(Ctx("s_a", mind), "library_read", [], {"sha256": hit["source_sha256"], "page": 1}))
    assert r["status"] == "done" and "kumquat" in r["detail"]["text"]
    assert asyncio.run(prog.act(Ctx("s_b", mind), "library_search", [], {"query": "kumquat"}))["status"] == "refused"
    assert asyncio.run(prog.act(Ctx("s_a", mind), "library_search", [], {"query": " "}))["status"] == "needs_input"
    for _ in range(prog.MIND_CALLS_PER_MINUTE):
        asyncio.run(prog.act(Ctx("s_a", mind), "library_search", [], {"query": "kumquat"}))
    r = asyncio.run(prog.act(Ctx("s_a", mind), "library_search", [], {"query": "kumquat"}))
    assert r["status"] == "refused" and "rate limit" in r["message"]


def test_host_sandbox_extraction_matches_the_direct_run(lib):
    """The production path: the host's own sandbox_run (sandbox-exec on macOS) runs the pinned bundle."""
    if not (HOST_SRC / "hive" / "core" / "sandbox_run.py").is_file():
        pytest.skip("host source not present")
    import sys
    from olib import extract
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(["sandboxed words", "page two"]))])
    lib.source("bad", [lib.file("bad.pdf", pdfs.malformed_pdf())])
    lib.ingest()
    host = extract.HostSandboxRunner.from_host_source(HOST_SRC, sys.executable, lib.data / "shelf")
    via_host = {r["id"]: r for r in asyncio.run(extract.extract_all(lib.data, host, force=True))}
    assert via_host["doc"]["status"] == "ok" and via_host["bad"]["status"] == "malformed"
    sha = shelfmod.Shelf(lib.data).manifest()["entries"]["doc"]["sha256"]
    host_text = extract.load_record(lib.data, sha, extract.bundle_digest())["pages"]
    direct = {r["id"]: r for r in asyncio.run(extract.extract_all(lib.data, extract.DirectTestRunner(), force=True))}
    assert direct["doc"]["status"] == "ok"
    assert extract.load_record(lib.data, sha, extract.bundle_digest())["pages"] == host_text
