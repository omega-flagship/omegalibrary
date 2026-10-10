"""Collection policy changes, each with an audit line.

``collections.json`` is read on every call. Changing it through these functions (``olib.py set-policy``/``grant``/
``revoke``) appends ``collections-audit.jsonl``: when, who (the OS user running the command), the policy digest before
and after, and the change. A direct edit of the file bypasses the audit; that is host seam S7 (no host-managed store).
"""

import getpass
import json
import pathlib

from .util import digest, now_iso, read_json, write_json


def load(data_dir):
    p = read_json(pathlib.Path(data_dir) / "collections.json", {})
    return p if isinstance(p, dict) else {}


def write(data_dir, new, by, change=None):
    data_dir = pathlib.Path(data_dir)
    old = load(data_dir)
    write_json(data_dir / "collections.json", new)
    line = {"at": now_iso(), "os_user": getpass.getuser(), "by": by, "before": digest(old), "after": digest(new),
            "change": change or {"set": new}}
    with open(data_dir / "collections-audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
    return line


def set_policy(data_dir, collection, visibility, admins, by):
    """Open or close a whole collection: ``public``, or ``swarms`` (keeping its swarm list) with or without admins."""
    if not isinstance(by, str) or not by.strip():
        raise ValueError("--by names who approved this access change")
    if visibility not in ("public", "swarms"):
        raise ValueError("visibility is public or swarms")
    pol = load(data_dir)
    rule = pol.get(collection) if isinstance(pol.get(collection), dict) else {}
    new_rule = {k: v for k, v in rule.items() if k == "label"}
    new_rule["visibility"] = visibility
    if visibility == "swarms":
        new_rule.update(swarms=sorted(rule.get("swarms") or []), admins=bool(admins))
    return write(data_dir, {**pol, collection: new_rule}, by,
                 {"set_policy": {"collection": collection, "visibility": visibility, "admins": bool(admins)}})


def grant(data_dir, collection, swarm_id, by):
    if not isinstance(by, str) or not by.strip():
        raise ValueError("--by names who approved this grant")
    pol = load(data_dir)
    rule = pol.get(collection)
    if not isinstance(rule, dict) or rule.get("visibility") != "swarms":
        raise ValueError(f"{collection!r} is not a swarm-scoped collection")
    swarms = sorted(set(rule.get("swarms") or []) | {swarm_id})
    return write(data_dir, {**pol, collection: {**rule, "swarms": swarms}}, by,
                 {"grant": {"collection": collection, "swarm": swarm_id}})


def revoke(data_dir, collection, swarm_id, by):
    pol = load(data_dir)
    rule = pol.get(collection)
    if not isinstance(rule, dict) or rule.get("visibility") != "swarms":
        raise ValueError(f"{collection!r} is not a swarm-scoped collection")
    swarms = sorted(set(rule.get("swarms") or []) - {swarm_id})
    return write(data_dir, {**pol, collection: {**rule, "swarms": swarms}}, by,
                 {"revoke": {"collection": collection, "swarm": swarm_id}})
