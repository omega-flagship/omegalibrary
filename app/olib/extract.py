"""Page extraction through the pinned tool bundle.

Production runs the bundle through the host's ``sandbox:run`` (``ctx.sandbox_run`` inside the hive, or the host's own
``hive.core.sandbox_run.run`` for an operator's offline ingest; both are the same host function). A direct, unsandboxed
run of the same entry exists ONLY for tests and is refused unless ``OMEGALIBRARY_TEST_ONLY_DIRECT_EXTRACT=1``.

A record is keyed by the source's sha256 and the bundle digest, so a changed extractor never mixes with old text:
``extract/<sha256>.<bundle digest[:16]>.json``.
"""

import asyncio
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

from .util import digest, now_iso, read_json, write_json

APP_DIR = pathlib.Path(__file__).resolve().parents[1]
BUNDLE = "tools"
ENTRY = "extract_pages.py"
INPUT_CAP = 20 * 1024 * 1024        # hive/core/sandbox_run.py MAX_INPUT
CHUNK_PAGES = 40                    # pages per sandboxed run, to stay inside the 60 CPU-second ceiling
TEST_ONLY_FLAG = "OMEGALIBRARY_TEST_ONLY_DIRECT_EXTRACT"
RECORD_SCHEMA = "omegalibrary.extract-record/1"


def verify_bundle(app_dir=APP_DIR, bundle=BUNDLE):
    """The same check the host makes before every run (hive/core/sandbox_run.py verify_bundle)."""
    root = (pathlib.Path(app_dir) / bundle).resolve()
    lock = json.loads((root / "bundle.lock.json").read_text())
    files = lock.get("files") if isinstance(lock, dict) else None
    if not isinstance(files, dict) or not files:
        raise ValueError("bundle.lock.json lists the bundle's files and their sha256")
    present = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() or p.is_symlink()} - {"bundle.lock.json"}
    if present != set(files):
        extra, missing = sorted(present - set(files))[:5], sorted(set(files) - present)[:5]
        raise ValueError(f"bundle files differ from its lock (extra {extra}, missing {missing})")
    for rel, want in files.items():
        p = root / rel
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != want:
            raise ValueError(f"bundle file changed: {rel}")
    return root


def bundle_digest(app_dir=APP_DIR, bundle=BUNDLE):
    root = verify_bundle(app_dir, bundle)
    return digest(json.loads((root / "bundle.lock.json").read_text())["files"])


def write_lock(app_dir=APP_DIR, bundle=BUNDLE):
    root = (pathlib.Path(app_dir) / bundle).resolve()
    files = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_file() and rel != "bundle.lock.json":
            if "__pycache__" in p.parts:
                raise ValueError(f"remove {rel}: bytecode never belongs in the bundle")
            files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    (root / "bundle.lock.json").write_text(json.dumps({"files": files}, indent=1, sort_keys=True) + "\n")
    return digest(files)


def record_path(data_dir, sha, bdigest):
    return pathlib.Path(data_dir) / "extract" / f"{sha}.{bdigest[:16]}.json"


def load_record(data_dir, sha, bdigest):
    return read_json(record_path(data_dir, sha, bdigest))


# ---- runners --------------------------------------------------------------------------------------------------

class HostSandboxRunner:
    """Runs the bundle through a host ``sandbox_run(entry, args, inputs, outputs, runtime, bundle, **limits)``.

    Inside the hive pass ``ctx.sandbox_run``. For an operator's offline ingest, ``from_host_source`` binds the host's
    own ``hive.core.sandbox_run.run`` from a read-only host checkout (same code path, same OS sandbox)."""

    kind = "host-sandbox"

    def __init__(self, sandbox_run, runtime="python", shelf_readable=False):
        self.sandbox_run, self.runtime, self.shelf_readable = sandbox_run, runtime, shelf_readable

    @classmethod
    def from_host_source(cls, host_src, python_exe, shelf_dir, app_dir=APP_DIR):
        sys.path.insert(0, str(host_src))
        os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        from hive.core import sandbox_run as host   # read-only import of the host's module
        os.environ["HIVE_SANDBOX_RUN_APPS"] = "omegalibrary"
        os.environ["HIVE_SANDBOX_RUNTIMES"] = json.dumps(
            {"python": {"exe": str(python_exe), "reads": [str(pathlib.Path(shelf_dir).resolve())]}})
        program = {"id": "omegalibrary", "path": str(pathlib.Path(app_dir).resolve())}

        async def run(entry, args=(), inputs=None, outputs=(), runtime="python", bundle="tools", **limits):
            return await host.run(program, entry, args, inputs, outputs, runtime, bundle, **limits)
        runner = cls(run, shelf_readable=True)
        runner.host_module = host.__file__
        return runner

    async def run(self, sha, blob_path, kind, first, last):
        args = ["--sha256", sha, "--kind", kind, "--first", str(first)]
        if last is not None:
            args += ["--last", str(last)]
        inputs = None
        size = pathlib.Path(blob_path).stat().st_size
        if size <= INPUT_CAP:
            inputs = {"source.bin": pathlib.Path(blob_path).read_bytes()}
        elif self.shelf_readable:
            args += ["--shelf-root", str(pathlib.Path(blob_path).parent)]   # the entry opens <root>/<sha> itself
        else:
            return {"status": "too_large_for_sandbox", "pages": [], "page_count": None, "range": None, "info": {},
                    "notes": [f"{size} bytes is over the host's {INPUT_CAP}-byte sandbox input cap and the shelf is "
                              "not a read-only root of the runtime (see README: host seam S4)"]}
        out = await self.sandbox_run(ENTRY, args, inputs, ["pages.json"], runtime=self.runtime, bundle=BUNDLE,
                                     cpu_seconds=60, file_bytes=30 * 1024 * 1024, timeout=120)
        return _parse(out.get("exit"), out.get("outputs", {}).get("pages.json"), out.get("stderr", b""))


# Python-level network cut for the test-only runner's child (the same guard tests/conftest.py puts on the parent).
_NO_NET = (
    "import runpy, socket, sys\n"
    "def _cut(*a, **k):\n"
    "    raise OSError('network is cut for the test-only extractor')\n"
    "for _n in ('connect', 'connect_ex', 'sendto'):\n"
    "    setattr(socket.socket, _n, _cut)\n"
    "socket.create_connection = socket.getaddrinfo = _cut\n"
    "sys.argv = sys.argv[1:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n")
_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_NO_NET_PROFILE = "(version 1)(allow default)(deny network*)"


def _os_network_cut():
    """The OS-level network denial for the test-only runner's child, or None where this machine has none."""
    if sys.platform == "darwin" and pathlib.Path(_SANDBOX_EXEC).is_file():
        return [_SANDBOX_EXEC, "-p", _NO_NET_PROFILE]
    if sys.platform.startswith("linux"):
        try:
            ok = subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return ["unshare", "-rn"]
    return None


class DirectTestRunner:
    """TEST ONLY. Runs the same verified bundle entry in a temporary job folder WITHOUT the hive's sandbox.

    The child still has no network (stand-in r3 item 8): on macOS it runs under ``sandbox-exec`` with every network
    operation denied, on Linux in a new network namespace (``unshare -rn``), and in every case behind the same Python
    socket guard the tests put on their own process. Where the OS cannot cut the network it refuses to run at all
    (stand-in r4 item 1): the Python guard alone leaves raw ``_socket`` usable."""

    kind = "test-only-direct"

    def __init__(self, python_exe=None):
        if os.environ.get(TEST_ONLY_FLAG) != "1":
            raise PermissionError(f"the direct extractor is for tests only; set {TEST_ONLY_FLAG}=1 to use it")
        self.python = python_exe or sys.executable
        self.os_cut = _os_network_cut()
        if not self.os_cut:
            raise PermissionError("the direct extractor needs OS network isolation (sandbox-exec or unshare -rn); "
                                  "none is available here, so it does not run")

    def command(self, entry_path, args):
        return [*(self.os_cut or []), self.python, "-B", "-c", _NO_NET, str(entry_path), *args]

    async def run(self, sha, blob_path, kind, first, last):
        root = verify_bundle()
        with tempfile.TemporaryDirectory(prefix="olib-job-") as job:
            pathlib.Path(job, "source.bin").write_bytes(pathlib.Path(blob_path).read_bytes())
            args = ["--sha256", sha, "--kind", kind, "--first", str(first)]
            if last is not None:
                args += ["--last", str(last)]
            env = {"PATH": "/usr/bin:/bin", "HOME": job, "TMPDIR": job, "LANG": "en_US.UTF-8"}
            proc = await asyncio.to_thread(subprocess.run, self.command(root / ENTRY, args), cwd=job, env=env,
                                           capture_output=True, timeout=120)
            p = pathlib.Path(job, "pages.json")
            return _parse(proc.returncode, p.read_bytes() if p.is_file() else None, proc.stderr)


def _parse(code, raw, stderr):
    if raw is None:
        tail = (stderr or b"")[-400:].decode("utf-8", "replace")
        return {"status": "extractor_failed", "pages": [], "page_count": None, "range": None, "info": {},
                "notes": [f"the extractor exited {code} without output: {tail}"]}
    return json.loads(raw.decode("utf-8"))


# ---- driving one source ---------------------------------------------------------------------------------------

async def extract_source(data_dir, entry, runner, bdigest=None, chunk=CHUNK_PAGES):
    """Extract every page of one shelf entry; writes and returns the record. Every outcome is labelled."""
    from .shelf import Shelf
    shelf = Shelf(data_dir)
    sha, kind = entry["sha256"], entry["kind"]
    bdigest = bdigest or bundle_digest()
    blob = shelf.blob_path(sha)
    record = {"schema": RECORD_SCHEMA, "sha256": sha, "kind": kind, "bundle_digest": bdigest,
              "runner": runner.kind, "started_at": now_iso(), "pages": [], "notes": [], "runs": 0}
    first, last_page, count, statuses = 1, None, None, []
    while True:
        last = None if kind == "html" else first + chunk - 1
        part = await runner.run(sha, blob, kind, first, last)
        record["runs"] += 1
        statuses.append(part.get("status"))
        record["notes"] += [n for n in part.get("notes", []) if n not in record["notes"]]
        if part.get("info") and "info" not in record:
            record["info"] = part["info"]
        for k in ("pypdf", "encrypted", "tool"):
            if part.get(k):
                record[k] = part[k]
        record["pages"] += part.get("pages", [])
        count = part.get("page_count") if part.get("page_count") is not None else count
        rng = part.get("range")
        if part.get("status") not in ("ok", "partial") or not rng or count is None or rng[1] >= count:
            break
        first = rng[1] + 1
    record["page_count"] = count
    record["status"] = _overall(statuses, record["pages"])
    if record["status"] == "image_only":
        record["notes"].append("no page has a text layer (image-only or scanned); OCR is not in slice 1")
    empty = [p["page"] for p in record["pages"] if p.get("chars", 0) == 0 and not p.get("error")]
    if empty and record["status"] in ("ok", "partial"):
        record["empty_pages"] = empty
    record["finished_at"] = now_iso()
    write_json(record_path(data_dir, sha, bdigest), record)
    return record


def _overall(statuses, pages):
    for bad in ("hash_mismatch", "encrypted", "malformed", "too_large_for_sandbox", "extractor_failed"):
        if bad in statuses:
            return bad
    if statuses and all(s == "empty" for s in statuses):
        return "empty"
    if pages and all(p.get("chars", 0) == 0 for p in pages):
        return "image_only" if not any(p.get("error") for p in pages) else "partial"
    return "partial" if "partial" in statuses else "ok"


async def extract_all(data_dir, runner, only=None, force=False):
    from .shelf import Shelf
    shelf = Shelf(data_dir)
    bdigest = bundle_digest()
    out = []
    for e in shelf.current():
        if only and e["id"] not in only:
            continue
        rec = None if force else load_record(data_dir, e["sha256"], bdigest)
        # A production run never reuses a record a test-only (unsandboxed) run produced.
        reusable = rec is not None and (runner.kind != "host-sandbox" or rec.get("runner") == "host-sandbox")
        if not reusable or rec.get("status") in ("extractor_failed", "too_large_for_sandbox"):
            rec = await extract_source(data_dir, e, runner, bdigest)
        out.append({"id": e["id"], "sha256": e["sha256"], "status": rec["status"], "pages": len(rec["pages"]),
                    "page_count": rec.get("page_count"), "notes": rec.get("notes", [])})
    return out
