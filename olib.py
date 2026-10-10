#!/usr/bin/env python3
"""OmegaLibrary operator command line (slice 1).

    python olib.py ingest  [--seed seed.json] [--refetch] [--offline] [--only ID,...]
    python olib.py extract --runner host-sandbox --host-src <hive checkout> [--python <exe>] [--only ID,...] [--force]
    python olib.py extract --runner test-only-direct      # TEST ONLY: needs OMEGALIBRARY_TEST_ONLY_DIRECT_EXTRACT=1
    python olib.py rebuild
    python olib.py status
    python olib.py search "<words>" [--swarm S] [--admin]   # --admin: answer as a hive admin would see it
    python olib.py read <sha256> <page> [--swarm S] [--admin]
    python olib.py grant <collection> <swarm id> --by "<who approved>"     # audited in data/collections-audit.jsonl
    python olib.py revoke <collection> <swarm id> --by "<who>"
    python olib.py set-policy <collection> --visibility public|swarms [--admins] --by "<who approved>"   # audited
    (ingest creates any new seed collection CLOSED; opening it is always one of these audited commands)
    python olib.py lock-bundle                            # rewrite app/tools/bundle.lock.json after a reviewed change

The data folder is ``--data`` or ``OMEGALIBRARY_DATA`` (default ``./data``).
"""

import argparse
import asyncio
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

from olib import api, extract, grants, index, shelf   # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog="olib")
    ap.add_argument("--data", default=os.environ.get("OMEGALIBRARY_DATA", str(ROOT / "data")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest")
    p.add_argument("--seed", default=str(ROOT / "seed.json"))
    p.add_argument("--refetch", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--only")
    p = sub.add_parser("extract")
    p.add_argument("--runner", choices=["host-sandbox", "test-only-direct"], required=True)
    p.add_argument("--host-src")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--only")
    p.add_argument("--force", action="store_true")
    sub.add_parser("rebuild")
    sub.add_parser("status")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--swarm")
    p.add_argument("--admin", action="store_true")
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("read")
    p.add_argument("sha256")
    p.add_argument("page", type=int)
    p.add_argument("--swarm")
    p.add_argument("--admin", action="store_true")
    for name in ("grant", "revoke"):
        p = sub.add_parser(name)
        p.add_argument("collection")
        p.add_argument("swarm_id")
        p.add_argument("--by", required=True)
    p = sub.add_parser("set-policy")
    p.add_argument("collection")
    p.add_argument("--visibility", choices=["public", "swarms"], required=True)
    p.add_argument("--admins", action="store_true")
    p.add_argument("--by", required=True)
    sub.add_parser("lock-bundle")
    a = ap.parse_args(argv)
    data = pathlib.Path(a.data)
    data.mkdir(parents=True, exist_ok=True)
    only = set(a.only.split(",")) if getattr(a, "only", None) else None

    if a.cmd == "ingest":
        from olib.fetch import Fetcher
        out = shelf.ingest(a.seed, data, Fetcher(offline=a.offline), refetch=a.refetch, only=only)
    elif a.cmd == "extract":
        if a.runner == "host-sandbox":
            if not a.host_src:
                ap.error("--host-src is the read-only hive checkout whose hive/core/sandbox_run.py runs the bundle")
            runner = extract.HostSandboxRunner.from_host_source(a.host_src, a.python, data / "shelf")
        else:
            runner = extract.DirectTestRunner()
        out = {"runner": runner.kind, "host_module": getattr(runner, "host_module", None),
               "bundle_digest": extract.bundle_digest(),
               "results": asyncio.run(extract.extract_all(data, runner, only=only, force=a.force))}
    elif a.cmd == "rebuild":
        out = index.rebuild(data)
    elif a.cmd == "status":
        s = shelf.Shelf(data)
        m = s.manifest()
        blobs = sorted(p for p in s.blobs.glob("*") if p.is_file()) if s.blobs.is_dir() else []
        out = {"entries": len(m["entries"]), "blobs": len(blobs), "blob_bytes": sum(p.stat().st_size for p in blobs),
               "manifest_sha256": s.manifest_sha256(), "bundle_digest": extract.bundle_digest(),
               "index": index.state(data)}
    elif a.cmd == "search":
        out = api.search(data, {"swarm_id": a.swarm, "admin": a.admin}, a.query, a.limit)
    elif a.cmd == "read":
        try:
            out = api.read(data, {"swarm_id": a.swarm, "admin": a.admin}, a.sha256, a.page)
        except api.NotAvailable:
            out = {"status": "not_available"}
    elif a.cmd in ("grant", "revoke"):
        out = getattr(grants, a.cmd)(data, a.collection, a.swarm_id, a.by)
    elif a.cmd == "set-policy":
        out = grants.set_policy(data, a.collection, a.visibility, a.admins, a.by)
    elif a.cmd == "lock-bundle":
        out = {"bundle_digest": extract.write_lock()}
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
