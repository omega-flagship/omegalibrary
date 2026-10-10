"""OmegaLibrary: the OmegaHive knowledge node, slice 1 (Dot program contract 0.1, hive/PROGRAMS.md).

People: ``view`` lists the shelf, searches it (focus ``q:<words>``) and opens a source at a page (focus
``page:<sha256>:<n>``); the declared reads ``shelf``, ``search`` and ``read`` give an app frame the same data.
Minds: ``library_search`` and ``library_read`` are read-only actions offered to minds (``agents: true``), usable only
under a reviewed attachment (``offer``/``bind``/``unbind``), rate-limited per mind (a ledger in the data folder, so
the limit survives reloads and is shared between processes). Nothing here writes to the hive.

Configuration (operator environment):
    OMEGALIBRARY_DATA     the library's data folder (shelf, manifest, extraction records, index, collections.json)
    OMEGALIBRARY_SWARMS   optional comma-separated home swarms (advertised homes, not authority)

Access: every call is limited to ctx.swarm_id; ``collections.json`` decides which collections that swarm sees.
Imported text is data, never instructions: it is returned as text and nothing in it is executed or followed.
"""

import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from olib import api, ratelimit   # noqa: E402

MIND_CALLS_PER_MINUTE = 30
PAGE_RANGE = 50


def _data():
    d = os.environ.get("OMEGALIBRARY_DATA")
    if not d or not pathlib.Path(d).is_dir():
        raise RuntimeError("OmegaLibrary is not set up on this hive (OMEGALIBRARY_DATA)")
    return d


def _who(ctx):
    """Who is asking, as the hive set it: the swarm of this call, and whether a hive admin person is looking.
    A mind is never an admin; a request can never claim to be one (the host sets viewer and actor)."""
    person = getattr(ctx, "viewer", None) or {}
    actor = getattr(ctx, "actor", None) or {}
    if actor.get("kind") == "mind":
        return {"swarm_id": ctx.swarm_id, "admin": False}
    me = actor if actor.get("kind") == "person" else person
    return {"swarm_id": ctx.swarm_id, "admin": me.get("role") == "admin" and me.get("swarm_id") == ctx.swarm_id}


def home_swarms():
    return sorted({s.strip() for s in os.environ.get("OMEGALIBRARY_SWARMS", "").split(",") if s.strip()})


def describe():
    return {
        "contract": "0.1",
        "kinds": [
            {"id": "source", "label": "Source", "role": "source"},
            {"id": "pages", "label": "Pages", "role": "other"},
            {"id": "page", "label": "Page", "role": "evidence"},
            {"id": "hit", "label": "Search hit", "role": "evidence"},
            {"id": "notice", "label": "Notice", "role": "gap"},
        ],
        "relations": [
            {"id": "found_in", "label": "found in", "polarity": "neutral"},
            {"id": "part_of", "label": "part of", "polarity": "neutral"},
        ],
        "actions": [
            {"id": "library_search", "label": "Search the library", "agents": True, "revalidate": True,
             "effect": "read", "params": {"query": "words to find", "limit": "at most 50 hits"}},
            {"id": "library_read", "label": "Read a cited page", "agents": True, "revalidate": True,
             "effect": "read", "params": {"sha256": "the source's sha256", "page": "page number (1-based)"}},
        ],
        "reads": [{"id": "shelf", "label": "The shelf"}, {"id": "search", "label": "Search"},
                  {"id": "read", "label": "Read a page"},
                  {"id": "original", "label": "Download the exact pinned original"}],
    }


# ---- attachment hooks (hive/core/program_attachments.py) ---------------------------------------------------------

def offer(ctx, mind):
    return {"actions_offered": ["library_search", "library_read"], "channels_offered": []}


def bind(ctx, mind, scope, attachment_id, *binding):
    return {"actor": "library-reader:" + attachment_id}


def unbind(ctx, attachment, attachment_id):
    return {}   # nothing is held per attachment; access ends with the host's attachment


# ---- people ------------------------------------------------------------------------------------------------------

def _source_item(s, detail=False):
    ex = s["extraction"]
    flags = [] if ex["status"] in ("ok",) else ["extraction-" + ex["status"].replace("_", "-")]
    if s.get("historical"):
        flags.append("historical")
    loc = s["locations"][0] if s["locations"] else {}
    text = (f"{', '.join(s['authors']) or 'authors not curated'} · {s.get('date') or 'date unknown'} · "
            f"edition: {s.get('edition') or 'unknown'} · licence: {s.get('licence') or 'unknown'} · "
            f"{s['kind'].upper()}, {ex['page_count'] if ex['page_count'] is not None else '?'} pages · "
            f"extraction: {ex['status']}")
    if detail and ex["notes"]:
        text += "\n" + "\n".join(ex["notes"])
    return {"id": "src:" + s["source_sha256"], "kind": "source", "label": s["title"], "text": text,
            "status": "current", "flags": flags,
            "sources": [{"id": "orig", "label": s["title"], "kind": "document", "url": loc.get("url"),
                         "local_ref": "sha256:" + s["source_sha256"], "date": s.get("date"),
                         "status": "retrieved"}],
            "meta": {"source_sha256": s["source_sha256"], "collection": s["collection"],
                     "same_bytes_as": s["same_bytes_as"], "access": s.get("access")}}


def _page_item(r):
    return {"id": f"page:{r['source_sha256']}:{r['page']}", "kind": "page",
            "label": f"{r['title']}, page {r['page']} of {r['page_count']}",
            "text": r["text"] or "(no text on this page)",
            "status": "current" if r["status"] == "current" else "superseded",
            "flags": ([] if r["status"] == "current" else ["superseded-source"]) + (["extract-error"] if r.get("error") else []),
            "sources": [{"id": "cite", "label": r["citation"]["locator"], "kind": "document",
                         "local_ref": "sha256:" + r["source_sha256"], "locator": f"page {r['page']}",
                         "status": "cited"}],
            "meta": {"citation": r["citation"]}}


def _start(data, swarm, note=None):
    listing = api.shelf_list(data, swarm)
    items = [_source_item(s) for s in listing["sources"]][:399]
    notes = ["Search with the focus q:<words>; open a page with page:<sha256>:<n>.",
             "Imported text is data, not instructions."]
    # Index freshness is not shown in views: it is not part of the revision (Kimi r2), and search answers carry it.
    if note:
        notes.insert(0, note)
    return {"revision": api.revision(data, swarm), "title": "OmegaLibrary", "suggested_stage": "unfold",
            "items": items, "links": [], "notes": notes}


def view(ctx, focus=None, stage=None):
    data, swarm = _data(), _who(ctx)
    if not focus:
        return _start(data, swarm)
    try:
        if focus.startswith("q:"):
            res = api.search(data, swarm, focus[2:])
            items, links, seen = [], [], set()
            for i, h in enumerate(res["hits"]):
                hid = f"hit:{h['source_sha256']}:{h['page']}"
                items.append({"id": hid, "kind": "hit", "label": f"{h['title']} p.{h['page']}", "text": h["snippet"],
                              "sources": [{"id": "cite", "label": f"page {h['page']}", "kind": "document",
                                           "local_ref": "sha256:" + h["source_sha256"], "locator": f"page {h['page']}",
                                           "status": "cited"}], "meta": h})
                src = "src:" + h["source_sha256"]
                if src not in seen:
                    seen.add(src)
                    items.append({"id": src, "kind": "source", "label": h["title"]})
                links.append({"from": hid, "to": src, "rel": "found_in"})
            return {"revision": api.revision(data, swarm), "title": f"Search: {res['query']}", "focus": None,
                    "suggested_stage": "map", "items": items, "links": links,
                    "notes": [f"{res['total']} matching pages; showing {len(res['hits'])}."]}
        if focus.startswith("page:"):
            _, sha, n = focus.split(":")
            r = api.read(data, swarm, sha, int(n))
            item = _page_item(r)
            items = [item]
            for m in (r["page"] - 1, r["page"] + 1):
                if r["page_count"] and 1 <= m <= r["page_count"]:
                    items.append({"id": f"page:{sha}:{m}", "kind": "page", "label": f"page {m}"})
            orig = r["citation"]["original"]
            how = (f"Download the exact original with the read 'original' {{sha256}} ({orig['download']['chunks']} "
                   f"chunk(s); follow next_offset), check it hashes to its sha256, then go to ")
            return {"revision": api.revision(data, swarm), "title": item["label"], "focus": item["id"],
                    "suggested_stage": "detail", "items": items, "links": [],
                    "notes": [how + f"physical page {r['page']} of sha256 {sha}."]}
        if focus.startswith("src:") or focus.startswith("pages:"):
            sha = focus.split(":")[1]
            listing = api.shelf_list(data, swarm)
            s = next((x for x in listing["sources"] if x["source_sha256"] == sha), None)
            if s is None:
                raise api.NotAvailable()
            item = _source_item(s, detail=True)
            items, links = [item], []
            count = s["extraction"]["page_count"] or 0
            if focus.startswith("pages:") and s["extraction"]["status"] in ("ok", "partial"):
                a, b = (int(x) for x in focus.split(":")[2].split("-"))
                a, b = max(1, a), min(count, b, a + PAGE_RANGE - 1)
                for n in range(a, b + 1):
                    pid = f"page:{sha}:{n}"
                    items.append({"id": pid, "kind": "page", "label": f"page {n}"})
                    links.append({"from": pid, "to": item["id"], "rel": "part_of"})
                return {"revision": api.revision(data, swarm), "title": f"{s['title']}, pages {a}–{b}",
                        "focus": item["id"], "suggested_stage": "map", "items": items, "links": links, "notes": []}
            if s["extraction"]["status"] in ("ok", "partial"):
                for a in range(1, count + 1, PAGE_RANGE):
                    b = min(count, a + PAGE_RANGE - 1)
                    rid = f"pages:{sha}:{a}-{b}"
                    items.append({"id": rid, "kind": "pages", "label": f"pages {a}–{b}"})
                    links.append({"from": rid, "to": item["id"], "rel": "part_of"})
            return {"revision": api.revision(data, swarm), "title": s["title"], "focus": item["id"],
                    "suggested_stage": "detail", "items": items, "links": links, "notes": []}
    except (api.NotAvailable, ValueError):
        pass
    # Unknown and hidden look the same.
    return _start(data, swarm, note="That item is not available here.")


def read(ctx, query, params):
    data, swarm = _data(), _who(ctx)
    if query == "shelf":
        return api.shelf_list(data, swarm)
    if query == "search":
        return api.search(data, swarm, str(params.get("query", "")), params.get("limit") or 20)
    if query == "read":
        try:
            return api.read(data, swarm, str(params.get("sha256", "")), params.get("page"))
        except api.NotAvailable:
            return {"status": "not_available"}
    if query == "original":
        try:
            return api.original(data, swarm, str(params.get("sha256", "")), params.get("offset", 0))
        except api.NotAvailable:
            return {"status": "not_available"}
        except ValueError:
            return {"status": "bad_offset"}
    return {"status": "unknown_read"}


# ---- actions -------------------------------------------------------------------------------------------------------

def _rate_ok(data, actor_id):
    """Persistent and shared across processes (olib/ratelimit.py); refuses if the ledger cannot be checked."""
    return ratelimit.allow(data, actor_id, MIND_CALLS_PER_MINUTE)


async def act(ctx, action, items, params):
    data, swarm = _data(), _who(ctx)
    actor = getattr(ctx, "actor", None) or {}
    if action in ("library_search", "library_read"):
        if actor.get("kind") == "mind":
            if actor.get("swarm_id") != ctx.swarm_id:
                return {"status": "refused", "message": "this mind is not in the swarm it asks for"}
            if not _rate_ok(data, actor.get("id") or "?"):
                return {"status": "refused", "message": f"rate limit: {MIND_CALLS_PER_MINUTE} library calls a minute"}
        if action == "library_search":
            q = str(params.get("query") or "")
            if not q.strip():
                return {"status": "needs_input", "needs": {"query": "words to find"}}
            res = api.search(data, swarm, q, params.get("limit") or 10)
            return {"status": "done", "detail": dict(res, host_read_only=bool(getattr(ctx, "read_only", False))),
                    "message": f"{res['total']} matching pages; each hit cites a sha256 and page."}
        try:
            r = api.read(data, swarm, str(params.get("sha256") or ""), params.get("page"))
        except api.NotAvailable:
            return {"status": "refused", "message": "not available"}
        return {"status": "done", "detail": dict(r, host_read_only=bool(getattr(ctx, "read_only", False))),
                "message": r["citation"]["locator"]}
    return {"status": "refused", "message": f"no action {action!r}"}


# Extraction never runs inside the hive in slice 1: plugin.json declares no capabilities (Kimi r3), so this app
# cannot reach the host's sandbox:run even if an operator lists it in HIVE_SANDBOX_RUN_APPS. Extraction is offline
# (olib.py extract --runner host-sandbox); an in-hive maintenance route is host seam S6.
