"""The content-addressed shelf and its manifest.

- Each original is stored once at ``shelf/<sha256>`` (read-only), however many locations or entries share it.
- An entry is one edition of one source. All of an entry's locations must hold identical bytes; if they differ the
  entry is refused with a clear error (list the other bytes as their own edition), never resolved by picking one.
- Two different entries with identical bytes share one blob and are noted ``same_bytes_as`` each other.
- When an entry's bytes change (a corrected or replaced source), the old sha256 moves to ``history`` as
  ``superseded``; its blob stays on the shelf so old citations still resolve to the exact original, but every
  derived page from it leaves the index at the next rebuild and is never served as current.
"""

import os
import pathlib

from . import fetch as fetchmod
from .util import is_sha, now_iso, read_json, sha256_bytes, sha256_file, write_atomic, write_json, digest

SEED_SCHEMA = "omegalibrary.seed/1"
MANIFEST_SCHEMA = "omegalibrary.manifest/1"
META_FIELDS = ("title", "authors", "date", "edition", "licence", "access", "collection", "kind", "notes",
               "metadata_source", "historical")
KINDS = ("pdf", "html")


class Shelf:
    def __init__(self, data_dir):
        self.data = pathlib.Path(data_dir).resolve()
        self.blobs = self.data / "shelf"
        self.manifest_path = self.data / "manifest.json"

    # ---- blobs -------------------------------------------------------------------------------------------------

    def blob_path(self, sha):
        if not is_sha(sha):
            raise ValueError("not a sha256")
        return self.blobs / sha

    def has(self, sha):
        return self.blob_path(sha).is_file()

    def put(self, data):
        """Store bytes once. Returns (sha256, newly_stored)."""
        sha = sha256_bytes(data)
        path = self.blob_path(sha)
        if path.is_file():
            if sha256_file(path) != sha:
                raise RuntimeError(f"shelf blob {sha} is corrupt; refusing to continue")
            return sha, False
        self.blobs.mkdir(parents=True, exist_ok=True)
        write_atomic(path, data, mode=0o444)
        return sha, True

    def verify(self, sha):
        path = self.blob_path(sha)
        return path.is_file() and sha256_file(path) == sha

    # ---- manifest ------------------------------------------------------------------------------------------------

    def manifest(self):
        return read_json(self.manifest_path, {"schema": MANIFEST_SCHEMA, "entries": {}})

    def save(self, manifest):
        write_json(self.manifest_path, manifest)

    def manifest_sha256(self):
        return sha256_file(self.manifest_path) if self.manifest_path.is_file() else None

    def current(self, manifest=None):
        """Entries whose current sha256 is on the shelf, sorted by id."""
        m = manifest or self.manifest()
        return [e for _, e in sorted(m["entries"].items()) if e.get("sha256")]


def load_seed(path):
    seed = read_json(path)
    if not isinstance(seed, dict) or seed.get("schema") != SEED_SCHEMA:
        raise ValueError(f"{path}: not an {SEED_SCHEMA} manifest")
    ids = [s.get("id") for s in seed.get("sources", [])]
    if len(ids) != len(set(ids)) or not all(isinstance(i, str) and i for i in ids):
        raise ValueError("every seed source needs a unique id")
    for s in seed["sources"]:
        if s.get("kind") not in KINDS:
            raise ValueError(f"{s['id']}: kind is one of {KINDS}")
        if s.get("collection") not in (seed.get("collections") or {}):
            raise ValueError(f"{s['id']}: collection {s.get('collection')!r} is not declared in the seed")
        if not s.get("locations"):
            raise ValueError(f"{s['id']}: at least one location (path or url)")
        for loc in s["locations"]:
            if set(loc) - {"path", "url", "linked_from"} or bool(loc.get("path")) == bool(loc.get("url")):
                raise ValueError(f"{s['id']}: each location is exactly one of path or url (plus linked_from)")
    return seed


def ingest(seed_path, data_dir, fetcher=None, refetch=False, seed_root=None, only=None):
    """Copy every seed source's original onto the shelf. Returns a report; never skips an entry silently."""
    seed = load_seed(seed_path)
    seed_root = pathlib.Path(seed_root or pathlib.Path(seed_path).resolve().parent)
    shelf = Shelf(data_dir)
    fetcher = fetcher or fetchmod.Fetcher()
    m = shelf.manifest()
    before = digest(m)
    entries = m["entries"]
    report = {"entries": [], "errors": [], "blobs_new": 0, "blobs_reused": 0, "duplicate_locations": 0,
              "superseded": [], "fetched": [], "started_at": now_iso()}
    collections_path = shelf.data / "collections.json"
    current_policy = read_json(collections_path, {}) if collections_path.is_file() else {}
    # A collection the seed declares and the policy lacks is created CLOSED (no swarms, no admins), whatever the seed
    # says (Kimi r2): every line that opens access is an operator's `olib.py set-policy|grant --by "<approver>"`.
    # An ingest never changes an existing rule.
    added = {k: {"visibility": "swarms", "swarms": [], "admins": False,
                 **({"label": v["label"]} if isinstance(v, dict) and isinstance(v.get("label"), str) else {})}
             for k, v in (seed.get("collections") or {}).items() if k not in current_policy}
    if added or not collections_path.is_file():
        from . import grants as colmod
        colmod.write(shelf.data, {**current_policy, **added}, by="ingest: new seed collections, created closed",
                     change={"created_closed": sorted(added)})
    # Seed pages first, so a linked PDF can be checked against the stored page that links it.
    order = sorted(seed["sources"], key=lambda s: (s["kind"] != "html", s["id"]))
    for src in order:
        if only and src["id"] not in only:
            continue
        sid = src["id"]
        try:
            got = []
            for loc in src["locations"]:
                got.append(_obtain(loc, src, seed, entries, shelf, fetcher, refetch, seed_root, report))
            shas = {g[0] for g in got}
            if len(shas) != 1:
                raise ValueError("locations hold different bytes ("
                                 + ", ".join(sorted(s[:12] for s in shas))
                                 + "); list each edition as its own entry")
            sha = shas.pop()
            report["duplicate_locations"] += len(got) - 1
            prev = entries.get(sid)
            meta = {k: src[k] for k in META_FIELDS if k in src}
            locations = [g[1] for g in got]
            if prev and prev.get("sha256") and prev["sha256"] != sha:
                hist = list(prev.get("history", []))
                hist.append({"sha256": prev["sha256"], "bytes": prev.get("bytes"), "status": "superseded",
                             "superseded_at": now_iso(), "superseded_by": sha,
                             "reason": src.get("correction_note") or "the source's bytes changed at re-ingest"})
                report["superseded"].append({"id": sid, "old": prev["sha256"], "new": sha})
            else:
                hist = list((prev or {}).get("history", []))
            entries[sid] = {"id": sid, **meta, "sha256": sha, "bytes": shelf.blob_path(sha).stat().st_size,
                            "locations": locations, "history": hist,
                            "ingested_at": (prev or {}).get("ingested_at") if prev and prev.get("sha256") == sha
                            else now_iso()}
            report["entries"].append({"id": sid, "sha256": sha})
        except Exception as exc:   # recorded, never silently dropped
            report["errors"].append({"id": sid, "error": f"{type(exc).__name__}: {exc}"})
    # Entries sharing bytes point at each other.
    by_sha = {}
    for sid, e in entries.items():
        if e.get("sha256"):
            by_sha.setdefault(e["sha256"], []).append(sid)
    for sid, e in entries.items():
        others = sorted(set(by_sha.get(e.get("sha256"), [])) - {sid})
        if others:
            e["same_bytes_as"] = others
        else:
            e.pop("same_bytes_as", None)
    m["seed_digest"] = digest(seed)
    if digest(m) != before or not shelf.manifest_path.is_file():   # an unchanged shelf keeps its manifest bytes
        m["updated_at"] = now_iso()
        shelf.save(m)
    report["manifest_sha256"] = shelf.manifest_sha256()
    report["finished_at"] = now_iso()
    report["fetch_attempts"] = list(getattr(fetcher, "log", []))
    write_json(shelf.data / "ingest-reports" / (report["finished_at"].replace(":", "") + ".json"), report)
    return report


def _obtain(loc, src, seed, entries, shelf, fetcher, refetch, seed_root, report):
    """(sha256, provenance) for one location."""
    if loc.get("path"):
        path = pathlib.Path(os.path.expanduser(loc["path"]))
        if not path.is_absolute():
            path = (seed_root / path).resolve()
        data = path.read_bytes()
        sha, new = shelf.put(data)
        report["blobs_new" if new else "blobs_reused"] += 1
        prev = next((l for l in (entries.get(src["id"]) or {}).get("locations", [])
                     if l.get("path") == str(path) and l.get("sha256") == sha), None)
        return sha, prev or {"path": str(path), "sha256": sha, "bytes": len(data), "first_seen_at": now_iso()}
    url = loc["url"]
    prev = entries.get(src["id"]) or {}
    old = next((l for l in prev.get("locations", []) if l.get("url") == url), None)
    if old and not refetch and shelf.has(old["sha256"]):
        report["blobs_reused"] += 1
        return old["sha256"], old           # fetched before: keep the record of that fetch, no new request
    if loc.get("linked_from"):
        parent = entries.get(loc["linked_from"])
        if not parent or not parent.get("sha256") or parent.get("kind") != "html":
            raise fetchmod.FetchRefused(f"{url}: linked_from {loc['linked_from']!r} is not a stored seed page")
        purl = next(l["url"] for l in parent["locations"] if l.get("url"))
        fetchmod.check_linked(url, purl, shelf.blob_path(parent["sha256"]).read_bytes())
    else:
        fetchmod.check_seed_page(url)
    data, info = fetcher.get(url)
    sha, new = shelf.put(data)
    report["blobs_new" if new else "blobs_reused"] += 1
    report["fetched"].append(url)
    prov = {"url": url, "sha256": sha, **{k: info[k] for k in ("final_url", "http_status", "content_type",
                                                                    "fetched_at", "bytes") if k in info}}
    if loc.get("linked_from"):
        prov["linked_from"] = loc["linked_from"]
    return sha, prov
