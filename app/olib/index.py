"""The derived, rebuildable SQLite FTS5 index of current page text.

It is built only from the manifest (current sha256 of each entry) and the extraction records for the current
bundle digest, in a fixed order, so a rebuild from the same inputs gives the same content (``index_digest``).
Superseded sources and stale extractions never enter it. Reads never trust it alone: every hit is filtered again
against the live manifest and the caller's collections at query time.
"""

import os
import pathlib
import sqlite3

from . import extract as ex
from .shelf import Shelf
from .util import digest, now_iso

INDEX_SCHEMA = "omegalibrary.index/2"


def index_path(data_dir):
    return pathlib.Path(data_dir) / "index.sqlite"


def rebuild(data_dir):
    """One FTS5 table per collection (``pages_1``, ``pages_2``, … named in ``fts_tables``), so the ranking statistics
    of a collection come only from that collection's own pages: a collection the caller cannot see never changes which
    visible hits come back or their order (Review r2: BM25 corpus statistics)."""
    shelf = Shelf(data_dir)
    manifest = shelf.manifest()
    bdigest = ex.bundle_digest()
    target = index_path(data_dir)
    tmp = target.with_name(".index-building.sqlite")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE fts_tables(collection TEXT PRIMARY KEY, tbl TEXT NOT NULL UNIQUE);
        CREATE TABLE sources(sha256 TEXT NOT NULL, collection TEXT NOT NULL, entry_ids TEXT NOT NULL,
                             title TEXT NOT NULL, kind TEXT NOT NULL, extract_status TEXT NOT NULL,
                             page_count INTEGER, PRIMARY KEY (sha256, collection));
    """)
    groups = {}
    for e in shelf.current(manifest):
        groups.setdefault(str(e.get("collection")), {}).setdefault(e["sha256"], []).append(e)
    report = {"sources": 0, "pages": 0, "not_extracted": [], "labelled": {}, "collections": {}}
    shas, page_keys = set(), set()
    for n, collection in enumerate(sorted(groups), start=1):
        tbl = f"pages_{n}"
        db.execute("INSERT INTO fts_tables VALUES (?,?)", (collection, tbl))
        db.execute(f"CREATE VIRTUAL TABLE {tbl} USING fts5(text, sha256 UNINDEXED, page UNINDEXED, "
                   f"tokenize = 'unicode61 remove_diacritics 2')")
        rows = 0
        for sha in sorted(groups[collection]):
            entries = groups[collection][sha]
            rec = ex.load_record(data_dir, sha, bdigest)
            status = rec["status"] if rec else "not_extracted"
            if sha not in shas:
                if not rec:
                    report["not_extracted"].append(sha)
                elif status not in ("ok", "partial"):
                    report["labelled"].setdefault(status, []).append(sha)
            shas.add(sha)
            db.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?)",
                       (sha, collection, ",".join(e["id"] for e in entries), entries[0].get("title", ""),
                        entries[0]["kind"], status, rec.get("page_count") if rec else None))
            if rec and status in ("ok", "partial"):
                for p in sorted(rec["pages"], key=lambda p: p["page"]):
                    if p.get("text", "").strip() and not p.get("error"):   # search resolves only such pages
                        db.execute(f"INSERT INTO {tbl}(text, sha256, page) VALUES (?,?,?)", (p["text"], sha, p["page"]))
                        page_keys.add((sha, p["page"]))
                        rows += 1
        report["collections"][collection] = rows
    report["sources"], report["pages"] = len(shas), len(page_keys)
    content = _content_digest(db)
    meta = {"schema": INDEX_SCHEMA, "manifest_sha256": shelf.manifest_sha256() or "", "bundle_digest": bdigest,
            "content_digest": content}
    db.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))
    db.commit()
    db.execute("VACUUM")
    db.close()
    os.replace(tmp, target)
    report.update(meta)
    report["built_at"] = now_iso()
    return report


def _content_digest(db):
    tables = db.execute("SELECT collection, tbl FROM fts_tables ORDER BY collection").fetchall()
    sources = db.execute("SELECT * FROM sources ORDER BY sha256, collection").fetchall()
    pages = {c: db.execute(f"SELECT sha256, page, text FROM {t} ORDER BY sha256, page").fetchall() for c, t in tables}
    return digest({"sources": sources, "pages": pages})


def tables(db):
    """{collection: fts table name}."""
    return dict(db.execute("SELECT collection, tbl FROM fts_tables").fetchall())


def connect(data_dir):
    path = index_path(data_dir)
    if not path.is_file():
        return None
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return db


def state(data_dir):
    """fresh | stale (the manifest or the extractor changed since the build) | missing."""
    db = connect(data_dir)
    if db is None:
        return {"state": "missing"}
    try:
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
    finally:
        db.close()
    shelf = Shelf(data_dir)
    stale = []
    if meta.get("manifest_sha256") != (shelf.manifest_sha256() or ""):
        stale.append("manifest changed since the build")
    try:
        if meta.get("bundle_digest") != ex.bundle_digest():
            stale.append("extractor bundle changed since the build")
    except Exception as exc:
        stale.append(f"extractor bundle unverifiable: {exc}")
    return {"state": "stale" if stale else "fresh", "why": stale, "content_digest": meta.get("content_digest")}


def fts_query(text):
    """A user's words as a safe FTS5 query: each word quoted, all required. Never FTS syntax from the caller."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in str(text)).split() if w][:16]
    return " ".join('"' + w.replace('"', "") + '"' for w in words)
