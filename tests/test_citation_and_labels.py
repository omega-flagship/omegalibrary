"""Citations resolve to the exact page of the pinned original; hard files are labelled, never skipped."""

import io

import pypdf
import pytest

import pdfs
from olib import api, extract, index, shelf as shelfmod


def test_citation_resolves_to_the_exact_page_text(lib):
    pages = ["alpha opening page", "the zanzibarite revision formula lives here", "omega closing page"]
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(pages))], title="Doc")
    lib.build()
    res = api.search(lib.data, None, "zanzibarite")
    assert res["total"] == 1
    (hit,) = res["hits"]
    assert set(hit) == {"source_sha256", "title", "page", "snippet"}
    assert hit["page"] == 2 and hit["title"] == "Doc" and "[zanzibarite]" in hit["snippet"]

    page = api.read(lib.data, None, hit["source_sha256"], hit["page"])
    blob = shelfmod.Shelf(lib.data).blob_path(hit["source_sha256"]).read_bytes()
    fresh = pypdf.PdfReader(io.BytesIO(blob)).pages[hit["page"] - 1].extract_text()   # independent of the index
    assert page["text"] == fresh and "zanzibarite" in page["text"]
    assert page["original_verified"] is True and page["status"] == "current"
    assert page["citation"]["source_sha256"] == hit["source_sha256"] and page["citation"]["page"] == 2
    for other in (1, 3):
        assert "zanzibarite" not in api.read(lib.data, None, hit["source_sha256"], other)["text"]
    with pytest.raises(api.NotAvailable):
        api.read(lib.data, None, hit["source_sha256"], 4)


def test_a_tampered_original_no_longer_resolves(lib):
    lib.source("doc", [lib.file("doc.pdf", pdfs.text_pdf(["quokka"]))])
    lib.build()
    sha = api.search(lib.data, None, "quokka")["hits"][0]["source_sha256"]
    blob = shelfmod.Shelf(lib.data).blob_path(sha)
    blob.chmod(0o644)
    blob.write_bytes(pdfs.text_pdf(["quokka but different"]))
    with pytest.raises(api.NotAvailable):
        api.read(lib.data, None, sha, 1)


def test_encrypted_image_only_and_malformed_files_are_labelled(lib):
    lib.source("enc", [lib.file("enc.pdf", pdfs.encrypted_pdf(["hidden words"]))])
    lib.source("img", [lib.file("img.pdf", pdfs.image_only_pdf(2))])
    lib.source("bad", [lib.file("bad.pdf", pdfs.malformed_pdf())])
    lib.source("png", [lib.file("png.pdf", pdfs.not_a_pdf())])
    lib.source("ok", [lib.file("ok.pdf", pdfs.text_pdf(["plain words"]))])
    lib.ingest()
    results = {r["id"]: r for r in lib.extract()}
    assert set(results) == {"enc", "img", "bad", "png", "ok"}                      # nothing skipped
    assert results["enc"]["status"] == "encrypted"
    assert results["img"]["status"] == "image_only" and results["img"]["page_count"] == 2
    assert any("OCR is not in slice 1" in n for n in results["img"]["notes"])
    assert results["bad"]["status"] == "malformed" and results["bad"]["notes"]
    assert results["png"]["status"] == "malformed" and "no %PDF- header" in results["png"]["notes"][0]
    assert results["ok"]["status"] == "ok"
    built = lib.rebuild()
    assert built["sources"] == 5 and built["pages"] == 1
    assert {k: len(v) for k, v in built["labelled"].items()} == {"encrypted": 1, "image_only": 1, "malformed": 2}
    listing = {s["id"]: s["extraction"]["status"] for s in api.shelf_list(lib.data, None)["sources"]}
    assert listing == {"enc": "encrypted", "img": "image_only", "bad": "malformed", "png": "malformed", "ok": "ok"}
    assert api.search(lib.data, None, "hidden")["total"] == 0   # the encrypted text was never extracted


def test_empty_user_password_encryption_is_noted_and_read(lib):
    lib.source("open", [lib.file("open.pdf", pdfs.encrypted_pdf(["readable anyway"], user_password=""))])
    lib.ingest()
    (r,) = lib.extract()
    assert r["status"] == "ok" and any("empty user password" in n for n in r["notes"])


def test_the_direct_runner_is_test_only_and_the_bundle_is_pinned(monkeypatch, tmp_path):
    monkeypatch.delenv("OMEGALIBRARY_TEST_ONLY_DIRECT_EXTRACT")
    with pytest.raises(PermissionError):
        extract.DirectTestRunner()
    extract.verify_bundle()                                  # the committed bundle matches its lock
    import shutil
    copy = tmp_path / "app"
    shutil.copytree(extract.APP_DIR / "tools", copy / "tools")
    (copy / "tools" / "extract_pages.py").write_text("print('changed')\n")
    with pytest.raises(ValueError, match="bundle file changed"):
        extract.verify_bundle(copy)
    (copy / "tools" / "extract_pages.py").unlink()
    with pytest.raises(ValueError, match="differ from its lock"):
        extract.verify_bundle(copy)


def test_index_rebuilds_identically(lib):
    lib.source("a", [lib.file("a.pdf", pdfs.text_pdf(["one", "two"]))])
    lib.source("b", [lib.file("b.pdf", pdfs.text_pdf(["three"]))])
    lib.source("c", [lib.file("c.pdf", pdfs.malformed_pdf())])
    lib.build()
    path = index.index_path(lib.data)
    first, first_bytes = index.state(lib.data)["content_digest"], path.read_bytes()
    second = lib.rebuild()
    assert second["content_digest"] == first
    assert path.read_bytes() == first_bytes                 # byte-identical file, not only the same rows
    assert index.state(lib.data)["state"] == "fresh"
