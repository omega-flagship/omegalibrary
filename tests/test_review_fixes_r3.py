"""Round 3 review fixes, 2026-10-07 (stand-in FIX-FIRST r3, Kimi SHIP r3 hardening). Each test fails on f34c0f5."""

import asyncio
import base64
import hashlib
import json
import socket
import sys
import textwrap

import pytest

import pdfs
from conftest import ROOT
from olib import api, extract, index, shelf as shelfmod
from test_access_and_corrections import Ctx, load_program

OUT = "s_outside"


# ---- stand-in r3 item 1 (S8): every original, of any size, is delivered exactly ------------------------------------

def test_an_original_over_one_read_answer_is_delivered_exactly_in_verified_chunks(lib, monkeypatch):
    blob = pdfs.text_pdf(["alpha", "beta", "gamma", "delta"])
    lib.source("doc", [lib.file("doc.pdf", blob)])
    lib.build()
    sha = shelfmod.Shelf(lib.data).manifest()["entries"]["doc"]["sha256"]
    chunk = 100
    assert len(blob) > 3 * chunk
    # the same small cap under either name, so the pre-fix code meets the same "too large for one answer" original
    monkeypatch.setattr(api, "ORIGINAL_CHUNK_MAX", chunk, raising=False)
    monkeypatch.setattr(api, "ORIGINAL_INLINE_MAX", chunk, raising=False)
    orig = api.read(lib.data, OUT, sha, 3)["citation"]["original"]
    assert orig["download"] is not None and orig["download"]["params"] == {"sha256": sha}
    assert orig["download"]["chunks"] == -(-len(blob) // chunk) and "page 3" in orig["note"]
    prog = load_program(monkeypatch, lib.data)
    joined, offset, calls = b"", 0, 0
    while offset is not None:
        params = {"sha256": sha} if offset == 0 else {"sha256": sha, "offset": offset}
        got = prog.read(Ctx(OUT), "original", params)
        assert got["status"] == "ok" and got["bytes"] == len(blob) and got["offset"] == offset
        part = base64.b64decode(got["content_base64"])
        assert 0 < len(part) <= chunk and hashlib.sha256(part).hexdigest() == got["chunk_sha256"]
        joined += part
        offset, calls = got["next_offset"], calls + 1
    assert joined == blob and hashlib.sha256(joined).hexdigest() == sha and calls == orig["download"]["chunks"]
    # a bad offset is refused; a changed original serves no further chunk
    assert prog.read(Ctx(OUT), "original", {"sha256": sha, "offset": len(blob)})["status"] == "bad_offset"
    assert prog.read(Ctx(OUT), "original", {"sha256": sha, "offset": -1})["status"] == "bad_offset"
    blob_path = shelfmod.Shelf(lib.data).blob_path(sha)
    blob_path.chmod(0o644)
    blob_path.write_bytes(blob[:-1] + b"X")
    assert prog.read(Ctx(OUT), "original", {"sha256": sha, "offset": chunk}) == {"status": "not_available"}


# ---- stand-in r3 item 6 (S2, app side): the mind rate limit survives a reload and is shared -----------------------

def test_the_mind_rate_limit_survives_a_reload_and_is_shared_between_processes(lib, monkeypatch):
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(["kumquat"]))])
    lib.build()
    mind = {"kind": "mind", "id": "a_9", "swarm_id": "s_a"}
    first = load_program(monkeypatch, lib.data)
    for _ in range(first.MIND_CALLS_PER_MINUTE):
        assert asyncio.run(first.act(Ctx("s_a", mind), "library_search", [], {"query": "kumquat"}))["status"] == "done"
    # a reload (or a second process on the same data folder) starts with no memory of its own: the ledger decides
    second = load_program(monkeypatch, lib.data)
    r = asyncio.run(second.act(Ctx("s_a", mind), "library_search", [], {"query": "kumquat"}))
    assert r["status"] == "refused" and "rate limit" in r["message"]
    other = dict(mind, id="a_10")
    assert asyncio.run(second.act(Ctx("s_a", other), "library_search", [], {"query": "kumquat"}))["status"] == "done"
    # the window still expires
    from olib import ratelimit
    import time
    assert ratelimit.allow(lib.data, "a_9", second.MIND_CALLS_PER_MINUTE, now=time.time() + 61)


def test_an_unusable_rate_ledger_refuses_rather_than_allows(lib, monkeypatch):
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(["kumquat"]))])
    lib.build()
    (lib.data / "rate.sqlite").mkdir()      # not a database: the ledger cannot be opened or written
    prog = load_program(monkeypatch, lib.data)
    mind = {"kind": "mind", "id": "a_9", "swarm_id": "s_a"}
    r = asyncio.run(prog.act(Ctx("s_a", mind), "library_search", [], {"query": "kumquat"}))
    assert r["status"] == "refused" and "rate limit" in r["message"]


# ---- stand-in r3 item 8: the test-only extractor's child process has no network ---------------------------------

def _probe_bundle(tmp_path, port):
    root = tmp_path / "probe-bundle"
    root.mkdir()
    (root / extract.ENTRY).write_text(textwrap.dedent(f"""
        import json, socket
        out = []
        try:
            socket.create_connection(("127.0.0.1", {port}), timeout=3).close()
            out.append("connected")
        except Exception as exc:
            out.append("refused: " + type(exc).__name__)
        try:
            import _socket
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(("127.0.0.1", {port}))
            out.append("raw connected")
        except Exception as exc:
            out.append("raw refused: " + type(exc).__name__)
        open("pages.json", "w").write(json.dumps({{"status": "ok", "pages": [], "page_count": 0, "range": None,
                                                   "info": {{}}, "notes": out}}))
    """))
    return root


def test_the_test_only_extractor_child_cannot_reach_the_network(lib, tmp_path, monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   # bind/listen only; the parent never connects
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    try:
        port = listener.getsockname()[1]
        monkeypatch.setattr(extract, "verify_bundle", lambda *a, **k: _probe_bundle(tmp_path, port))
        src = lib.file("x.pdf", b"%PDF-1.4 probe")
        out = asyncio.run(extract.DirectTestRunner().run("0" * 64, src, "pdf", 1, None))
    finally:
        listener.close()
    notes = out["notes"]
    assert notes[0].startswith("refused"), notes
    if sys.platform == "darwin":      # the OS layer holds even below Python's socket module
        assert notes[1].startswith("raw refused"), notes


def test_the_test_only_extractor_refuses_to_run_without_os_network_isolation(monkeypatch):
    monkeypatch.setattr(extract, "_os_network_cut", lambda: None)   # e.g. Linux with unshare denied
    with pytest.raises(PermissionError, match="OS network isolation"):
        extract.DirectTestRunner()


# ---- Kimi r3 (sharpest): a page the extraction marks failed or empty never yields a hit ---------------------------

def test_an_indexed_page_whose_record_carries_an_error_yields_no_hit(lib):
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(["pangolin one", "pangolin two"]))])
    lib.build()
    sha = shelfmod.Shelf(lib.data).manifest()["entries"]["doc"]["sha256"]
    assert {h["page"] for h in api.search(lib.data, OUT, "pangolin")["hits"]} == {1, 2}
    path = extract.record_path(lib.data, sha, extract.bundle_digest())
    rec = json.loads(path.read_text())
    rec["pages"][0]["error"] = "extract_failed: synthetic"      # the record now marks page 1 failed, text kept
    path.write_text(json.dumps(rec))
    res = api.search(lib.data, OUT, "pangolin")
    assert all(h["page"] != 1 for h in res["hits"]), res
    index.rebuild(lib.data)                                     # a rebuild never indexes a failed page
    res = api.search(lib.data, OUT, "pangolin")
    assert [h["page"] for h in res["hits"]] == [2] and res["index"]["state"] == "fresh"


# ---- Kimi r3 (second seam): the app cannot reach the host sandbox at all ----------------------------------------

def test_the_app_declares_no_sandbox_capability_and_offers_no_extraction(lib, monkeypatch):
    plugin = json.loads((ROOT / "app" / "plugin.json").read_text())
    assert plugin["capabilities"] == []
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(["x"]))])
    lib.build()
    prog = load_program(monkeypatch, lib.data)
    assert not hasattr(prog, "extract_pending")
    assert {a["id"] for a in prog.describe()["actions"]} == {"library_search", "library_read"}
