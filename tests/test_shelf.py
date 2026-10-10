"""Identical bytes are stored once; distinct editions stay separate entries with their own metadata."""

import pdfs
from olib import shelf as shelfmod


def test_identical_bytes_stored_once_and_editions_kept(lib):
    v1 = pdfs.text_pdf(["first edition text"])
    v2 = pdfs.text_pdf(["second edition text, revised"])
    a = lib.file("a.pdf", v1)
    a_copy = lib.file("copy-of-a.pdf", v1)          # the same document in a second folder
    b = lib.file("b.pdf", v2)
    lib.source("paper-ed1", [a, a_copy], title="Paper", edition="1", date="2025-01-01")
    lib.source("paper-ed2", [b], title="Paper", edition="2", date="2026-01-01")
    lib.source("paper-ed1-mirror", [lib.file("mirror.pdf", v1)], title="Paper (mirror)", edition="1")
    report = lib.ingest()

    assert report["errors"] == []
    s = shelfmod.Shelf(lib.data)
    blobs = sorted(p.name for p in s.blobs.iterdir())
    assert len(blobs) == 2                            # four locations, two distinct byte strings
    assert report["blobs_new"] == 2 and report["blobs_reused"] == 2
    assert report["duplicate_locations"] == 1         # within one entry
    m = s.manifest()["entries"]
    assert m["paper-ed1"]["sha256"] == m["paper-ed1-mirror"]["sha256"] != m["paper-ed2"]["sha256"]
    assert m["paper-ed1"]["edition"] == "1" and m["paper-ed2"]["edition"] == "2"
    assert [l["path"] for l in m["paper-ed1"]["locations"]] == [str(a), str(a_copy)]   # provenance of both copies
    assert m["paper-ed1"]["same_bytes_as"] == ["paper-ed1-mirror"]
    assert "same_bytes_as" not in m["paper-ed2"]
    for sha in blobs:
        assert s.verify(sha)
        assert not (s.blob_path(sha).stat().st_mode & 0o222)     # stored read-only


def test_locations_with_different_bytes_are_refused_not_resolved(lib):
    lib.source("confused", [lib.file("x.pdf", pdfs.text_pdf(["x"])), lib.file("y.pdf", pdfs.text_pdf(["y"]))])
    lib.source("fine", [lib.file("z.pdf", pdfs.text_pdf(["z"]))])
    report = lib.ingest()
    assert [e["id"] for e in report["errors"]] == ["confused"]
    assert "different bytes" in report["errors"][0]["error"]
    assert "confused" not in shelfmod.Shelf(lib.data).manifest()["entries"]
    assert [e["id"] for e in report["entries"]] == ["fine"]


def test_reingest_is_idempotent(lib):
    lib.source("p", [lib.file("p.pdf", pdfs.text_pdf(["p"]))])
    lib.ingest()
    s = shelfmod.Shelf(lib.data)
    first, first_sha = s.manifest()["entries"]["p"], s.manifest_sha256()
    report = lib.ingest()
    assert s.manifest_sha256() == first_sha            # nothing changed, so the derived index stays fresh
    assert report["blobs_new"] == 0 and report["superseded"] == []
    assert s.manifest()["entries"]["p"]["ingested_at"] == first["ingested_at"]
