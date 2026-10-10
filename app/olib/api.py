"""Read functions shared by the app (people and attached minds) and the command line.

Access is decided on every call from ``collections.json`` and the live manifest; nothing is cached, so revoking a
swarm's access or superseding a source takes effect on the next search or read without a rebuild.

Who is asking (``who``): a swarm id, or ``{"swarm_id": ..., "admin": bool}``. ``admin`` is true only for a person the
hive itself calls a hive admin (``ctx.viewer``/``ctx.actor`` role ``admin``, set by the host, never by a request);
a mind is never an admin.

A collection is visible when its policy is:
- ``{"visibility": "public"}``; or
- ``{"visibility": "swarms", "swarms": [...], "admins": true|false}`` and the swarm is listed, or ``admins`` is true and
  the caller is a hive admin.
Anything else (unknown collection, malformed policy) is visible to no one.

Nothing about a source the caller may not see is ever disclosed or allowed to influence the answer: no title, id,
snippet, page count, hit count, duplicate reference, index freshness or view revision.
"""

import base64
import hashlib
import os
import pathlib

from . import extract as ex
from . import index as idx
from .shelf import Shelf
from .util import digest, is_sha, read_json

MAX_HITS = 50
SNIPPET_TOKENS = 24
ORIGINAL_CHUNK_MAX = 3_000_000    # one chunk's base64 fits the host's 4 MB read answer (programs.py READ_MAX_BYTES)


class NotAvailable(Exception):
    """Unknown, hidden or unreadable: deliberately one answer for all three."""


def _who(who):
    if isinstance(who, dict):
        return who.get("swarm_id"), who.get("admin") is True
    return who, False


def policy(data_dir):
    p = read_json(pathlib.Path(data_dir) / "collections.json", {})
    return p if isinstance(p, dict) else {}


def visible_collections(data_dir, who):
    swarm_id, admin = _who(who)
    out = set()
    for name, rule in policy(data_dir).items():
        if not isinstance(rule, dict):
            continue
        if rule.get("visibility") == "public":
            out.add(name)
        elif rule.get("visibility") == "swarms" and isinstance(rule.get("swarms"), list):
            if (swarm_id is not None and swarm_id in rule["swarms"]) or (admin and rule.get("admins") is True):
                out.add(name)
    return out


def _visible_entries(data_dir, who):
    shelf = Shelf(data_dir)
    allowed = visible_collections(data_dir, who)
    return [e for e in shelf.current() if e.get("collection") in allowed]


def visible_state(data_dir, who, entries=None):
    """Index freshness computed only over what this caller may see.

    - ``needs_rebuild``: the index was built by another extractor bundle (global, says nothing about any source), or
      is missing; search serves nothing until it is rebuilt, so search and page reads always agree.
    - ``stale``: a visible source changed (new bytes, new extraction status) since the build.
    - ``fresh``: everything visible is indexed as it is now. Changes to hidden sources never move this."""
    entries = _visible_entries(data_dir, who) if entries is None else entries
    db = idx.connect(data_dir)
    if db is None:
        return {"state": "needs_rebuild", "why": "no index yet"}
    try:
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("schema") != idx.INDEX_SCHEMA:
            return {"state": "needs_rebuild", "why": "the index was built in an older layout"}
        rows = {(sha, col): (ids.split(","), status) for sha, col, ids, status in
                db.execute("SELECT sha256, collection, entry_ids, extract_status FROM sources").fetchall()}
    finally:
        db.close()
    bd = ex.bundle_digest()
    if meta.get("bundle_digest") != bd:
        return {"state": "needs_rebuild", "why": "the extractor changed since the index was built"}
    for e in entries:
        row = rows.get((e["sha256"], e.get("collection")))
        rec = ex.load_record(data_dir, e["sha256"], bd)
        if row is None or e["id"] not in row[0] or row[1] != (rec["status"] if rec else "not_extracted"):
            return {"state": "stale", "why": "a source you can see changed since the index was built"}
    return {"state": "fresh"}


def _original(entry_bytes, sha, page):
    """How a person gets the exact pinned original, of any size, through the read route the host already authorises:
    the read ``original`` in chunks of at most ``ORIGINAL_CHUNK_MAX`` bytes (README S8)."""
    chunks = -(-entry_bytes // ORIGINAL_CHUNK_MAX) if entry_bytes else 1
    return {"source_sha256": sha, "page": page, "bytes": entry_bytes,
            "download": {"route": "POST /api/programs/omegalibrary/read", "query": "original",
                         "params": {"sha256": sha}, "chunk_bytes": ORIGINAL_CHUNK_MAX, "chunks": chunks},
            "note": (f"fetch the read 'original' from offset 0, following next_offset until it is null; the joined "
                     f"bytes must hash to sha256 {sha}; then open the PDF at page {page} (physical page, 1-based)")}


def shelf_list(data_dir, who):
    bd = ex.bundle_digest()
    entries = _visible_entries(data_dir, who)
    visible_ids = {e["id"] for e in entries}
    out = []
    for e in entries:
        rec = ex.load_record(data_dir, e["sha256"], bd)
        out.append({"id": e["id"], "source_sha256": e["sha256"], "title": e.get("title", e["id"]),
                    "authors": e.get("authors", []), "date": e.get("date"), "edition": e.get("edition"),
                    "kind": e["kind"], "collection": e["collection"], "licence": e.get("licence"),
                    "access": e.get("access"), "historical": bool(e.get("historical")),
                    "locations": [{k: l[k] for k in ("path", "url", "final_url", "fetched_at", "linked_from")
                                   if k in l} for l in e.get("locations", [])],
                    # only duplicates the caller may also see; a hidden twin is never named
                    "same_bytes_as": [i for i in e.get("same_bytes_as", []) if i in visible_ids],
                    "bytes": e.get("bytes"),
                    "extraction": {"status": rec["status"] if rec else "not_extracted",
                                   "page_count": rec.get("page_count") if rec else None,
                                   "notes": rec.get("notes", [])[:5] if rec else []}})
    return {"sources": out, "count": len(out), "index": visible_state(data_dir, who, entries)}


_verified = {}   # sha256 -> file identity (inode, size, mtime_ns, ctime_ns) at which its bytes last hashed to sha256


def _stat_ident(st):
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _ident(fd):
    return _stat_ident(os.fstat(fd))


def _path_ident(path):
    try:
        return _stat_ident(path.stat())
    except FileNotFoundError:
        return None


def _original_ok(shelf, sha):
    """The pinned original is present and still hashes to its sha256. The full hash is cached against the file's
    identity; any write, replacement or metadata change (ctime) forces a fresh hash."""
    path = shelf.blob_path(sha)
    try:
        st = path.stat()
    except FileNotFoundError:
        _verified.pop(sha, None)
        return False
    ident = _stat_ident(st)
    if _verified.get(sha) == ident:
        return True
    ok = shelf.verify(sha)
    if ok:
        _verified[sha] = ident
    else:
        _verified.pop(sha, None)
    return ok


def search(data_dir, who, query, limit=20):
    """Hits ranked only within the collections the caller may see (each collection has its own FTS table and BM25
    statistics), from originals verified unchanged, resolving under the current extraction."""
    q = idx.fts_query(query)
    limit = max(1, min(int(limit or 20), MAX_HITS))
    entries = _visible_entries(data_dir, who)
    state = visible_state(data_dir, who, entries)
    result = {"query": str(query)[:200], "hits": [], "total": 0, "index": state, "withheld": []}
    if not q or not entries or state["state"] == "needs_rebuild":
        return result     # never old text as current: an index from another extractor serves nothing
    shelf = Shelf(data_dir)
    titles, by_collection, bad = {}, {}, set()
    for e in entries:
        if e["sha256"] in bad:
            continue
        if not _original_ok(shelf, e["sha256"]):
            bad.add(e["sha256"])     # visible to this caller, so naming it is no leak; it yields no hit
            continue
        titles.setdefault(e["sha256"], e.get("title", e["id"]))
        by_collection.setdefault(e["collection"], set()).add(e["sha256"])
    result["withheld"] = [{"source_sha256": sha, "reason": "original_unverified",
                           "message": "the pinned original is missing or its bytes changed; no citation is served"}
                          for sha in sorted(bad)]
    db = idx.connect(data_dir)
    try:
        tables = idx.tables(db)
        found = {}
        for collection in sorted(by_collection):
            tbl = tables.get(collection)
            if tbl is None:
                continue
            shas = sorted(by_collection[collection])
            marks = ",".join("?" * len(shas))
            # Visibility is applied inside the query, before snippets and counts exist; BM25 statistics are this
            # collection's own, so no hidden collection can move a visible result.
            for sha, page, snip, score in db.execute(
                    f"SELECT sha256, page, snippet({tbl}, 0, '[', ']', '…', {SNIPPET_TOKENS}), bm25({tbl}) "
                    f"FROM {tbl} WHERE {tbl} MATCH ? AND sha256 IN ({marks})", [q, *shas]).fetchall():
                key = (sha, int(page))
                if key not in found or score < found[key][1]:
                    found[key] = (snip, score)     # a page in two visible collections counts once
    finally:
        db.close()
    ordered = sorted(found.items(), key=lambda kv: (kv[1][1], kv[0][0], kv[0][1]))
    # Every hit must resolve under the current extraction, or nothing is served (search and read always agree).
    bd = ex.bundle_digest()
    records = {}
    for (sha, page), _ in ordered[:limit]:
        rec = records.setdefault(sha, ex.load_record(data_dir, sha, bd))
        # A page resolves only if the current extraction has it without an error and with text (Kimi r3): a hit is
        # never served from a page the record marks failed or empty.
        if not rec or rec["status"] not in ("ok", "partial") or not any(
                p["page"] == page and not p.get("error") and str(p.get("text") or "").strip() for p in rec["pages"]):
            result["index"] = {"state": "needs_rebuild", "why": "an indexed page no longer resolves; rebuild"}
            return result
    result["total"] = len(ordered)
    result["hits"] = [{"source_sha256": sha, "title": titles[sha], "page": page, "snippet": snip}
                      for (sha, page), (snip, _) in ordered[:limit]]
    return result


def _find(data_dir, who, sha):
    shelf = Shelf(data_dir)
    allowed = visible_collections(data_dir, who)
    entry, status = None, None
    for e in (v for _, v in sorted(shelf.manifest()["entries"].items())):
        if e.get("collection") not in allowed:
            continue
        if e.get("sha256") == sha:
            return e, "current"
        for h in e.get("history", []):
            if h["sha256"] == sha and entry is None:
                entry, status = e, {"status": "superseded", "superseded_by": h.get("superseded_by"),
                                    "superseded_at": h.get("superseded_at"), "reason": h.get("reason")}
    return entry, status


def read(data_dir, who, sha, page):
    """The exact text of one page of a pinned original, with its citation. Superseded originals stay readable
    (old citations must resolve) and say so; hidden or unknown ones raise NotAvailable."""
    if not is_sha(sha):
        raise NotAvailable()
    try:
        page = int(page)
    except (TypeError, ValueError):
        raise NotAvailable()
    shelf = Shelf(data_dir)
    entry, status = _find(data_dir, who, sha)
    if entry is None or not shelf.has(sha):
        raise NotAvailable()
    rec = ex.load_record(data_dir, sha, ex.bundle_digest())
    if not rec or rec["status"] not in ("ok", "partial"):
        raise NotAvailable()
    pg = next((p for p in rec["pages"] if p["page"] == page), None)
    if pg is None:
        raise NotAvailable()
    if not shelf.verify(sha):          # the pinned original is still exactly these bytes
        raise NotAvailable()
    size = shelf.blob_path(sha).stat().st_size
    citation = {"source_sha256": sha, "page": page, "title": entry.get("title", entry["id"]),
                "bundle_digest": rec["bundle_digest"], "extracted_by": rec.get("runner"),
                "locator": f"sha256:{sha} page {page} of {rec.get('page_count')} (physical PDF page)",
                "locations": [l.get("url") or l.get("path") for l in entry.get("locations", [])],
                "original": _original(size, sha, page)}
    return {"source_sha256": sha, "title": entry.get("title", entry["id"]), "page": page,
            "page_count": rec.get("page_count"), "text": pg.get("text", ""), "error": pg.get("error"),
            "status": status if status == "current" else status["status"],
            **({"superseded": status} if status != "current" else {}),
            "original_verified": True, "citation": citation}


def original(data_dir, who, sha, offset=0):
    """One chunk of the exact pinned original's bytes, for a person's download (host read route; same access as read).

    Any size is delivered: ``offset`` starts at 0 and follows ``next_offset`` until it is null. Every chunk is served
    only while the whole file still hashes to its sha256, and only if the file did not change while the chunk was
    read; ``chunk_sha256`` lets the caller check each chunk, and the joined bytes hash to ``source_sha256``."""
    if not is_sha(sha):
        raise NotAvailable()
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset is a whole number of bytes from 0")
    shelf = Shelf(data_dir)
    entry, status = _find(data_dir, who, sha)
    if entry is None or not _original_ok(shelf, sha):
        raise NotAvailable()
    path = shelf.blob_path(sha)
    try:
        with path.open("rb") as f:
            before = _ident(f.fileno())
            size = before[1]
            if offset > size or (offset == size and size > 0):
                raise ValueError("offset is past the end of the original")
            f.seek(offset)
            data = f.read(ORIGINAL_CHUNK_MAX)
            after = _ident(f.fileno())
    except FileNotFoundError:
        raise NotAvailable()
    if before != after or _verified.get(sha) != after or _path_ident(path) != after:
        _verified.pop(sha, None)      # changed while it was read: nothing is served from it
        raise NotAvailable()
    end = offset + len(data)
    mime = "application/pdf" if entry["kind"] == "pdf" else "text/html"
    name = f"{entry['id']}.{'pdf' if entry['kind'] == 'pdf' else 'html'}"
    return {"status": "ok", "source_sha256": sha, "name": name, "mime": mime, "bytes": size,
            "offset": offset, "length": len(data), "next_offset": end if end < size else None,
            "chunk_sha256": hashlib.sha256(data).hexdigest(),
            "superseded": status != "current", "content_base64": base64.b64encode(data).decode("ascii")}


def revision(data_dir, who):
    """Changes whenever what this caller can see changes, and never because of a collection it cannot see."""
    bd = ex.bundle_digest()
    entries = _visible_entries(data_dir, who)
    seen = []
    for e in entries:
        rec = ex.load_record(data_dir, e["sha256"], bd)
        seen.append([e["id"], e["sha256"], [h["sha256"] for h in e.get("history", [])],
                     {k: e.get(k) for k in ("title", "authors", "date", "edition", "collection")},
                     rec["status"] if rec else None])
    # Index freshness is deliberately not part of the revision (Kimi r2): it is reported in search and shelf answers
    # about the sources the caller sees now, never folded into a value a swarm can watch across grants.
    return "lib-" + digest([bd, seen])[:16]
