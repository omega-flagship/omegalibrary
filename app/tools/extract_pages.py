"""OmegaLibrary page-text extractor (pinned tool bundle entry).

Runs inside the host's ``sandbox:run`` (hive/core/sandbox_run.py): no network, a fresh job folder as the only
writable place, CPU and file-size limits. It reads one source, checks it against the sha256 the caller pinned, and
writes ``pages.json`` (per-page text) into the job folder. It never executes anything found in the source.

Usage (arguments are plain strings; the host passes them unchanged):
    extract_pages.py --sha256 <hex> --kind pdf|html [--shelf-root <absolute folder>] [--first N] [--last M]

Without ``--shelf-root`` the source is ``source.bin`` in the job folder (the host's ``inputs``). ``--shelf-root`` is for
sources over the host's 20 MiB input cap: the entry itself opens ``<shelf-root>/<sha256>`` (never a caller-chosen
path), which works only when the operator lists the shelf folder as a read-only root of the runtime
(HIVE_SANDBOX_RUNTIMES ... "reads"); the bytes are still checked against ``--sha256``.

Every outcome is labelled, never skipped: ok, partial, encrypted, image_only, malformed, empty, hash_mismatch.
"""

import sys

sys.dont_write_bytecode = True   # the bundle folder is read-only in the sandbox and must match its lock exactly

import hashlib
import html.parser
import json
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))

SCHEMA = "omegalibrary.extract/1"
TOOL = "extract_pages/1"
MAX_ERROR = 300


def _args(argv):
    out = {"shelf_root": None, "first": 1, "last": None, "kind": "pdf", "sha256": None}
    it = iter(argv)
    for a in it:
        if a == "--shelf-root":
            out["shelf_root"] = next(it)
        elif a == "--first":
            out["first"] = int(next(it))
        elif a == "--last":
            out["last"] = int(next(it))
        elif a == "--kind":
            out["kind"] = next(it)
        elif a == "--sha256":
            out["sha256"] = next(it)
        else:
            raise SystemExit(f"unknown argument {a!r}")
    if not out["sha256"] or len(out["sha256"]) != 64 or not set(out["sha256"]) <= set("0123456789abcdef"):
        raise SystemExit("--sha256 <64 hex> is required")
    if out["kind"] not in ("pdf", "html"):
        raise SystemExit("--kind is pdf or html")
    return out


def _clean(text):
    return (text or "").replace("\x00", "")


def _short(exc):
    return f"{type(exc).__name__}: {str(exc)[:MAX_ERROR]}"


class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "header",
             "footer", "nav", "ul", "ol", "table", "pre", "blockquote", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)


def _html(data):
    text = data.decode("utf-8", errors="replace")
    p = _Text()
    p.feed(text)
    p.close()
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    body = "\n".join(line for line in lines if line)
    rec = {"status": "ok" if body.strip() else "empty", "page_count": 1, "range": [1, 1],
           "pages": [{"page": 1, "text": body, "chars": len(body)}],
           "info": {"title": " ".join(p.title.split())[:500]}, "notes": []}
    if not body.strip():
        rec["notes"].append("the HTML page has no visible text")
    return rec


def _pdf(data, first, last):
    import pypdf   # vendored, pure Python
    import io
    rec = {"status": "ok", "page_count": None, "range": None, "pages": [], "info": {}, "notes": [],
           "pypdf": pypdf.__version__}
    if not data.startswith(b"%PDF-"):
        rec.update(status="malformed", notes=["no %PDF- header: not a PDF file"])
        return rec
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:   # labelled, never skipped
        rec.update(status="malformed", notes=["could not open: " + _short(exc)])
        return rec
    try:
        encrypted = reader.is_encrypted
    except Exception as exc:
        rec.update(status="malformed", notes=["could not read the trailer: " + _short(exc)])
        return rec
    if encrypted:
        try:
            ok = reader.decrypt("")
        except Exception as exc:   # e.g. AES without a crypto library: still encrypted, labelled as such
            rec.update(status="encrypted", notes=["encrypted; not decryptable here: " + _short(exc)])
            return rec
        if not ok:
            rec.update(status="encrypted", notes=["encrypted with a user password; no text extracted"])
            return rec
        rec["notes"].append("encrypted with an empty user password (owner restrictions only); text extracted")
        rec["encrypted"] = "empty-user-password"
    try:
        count = len(reader.pages)
    except Exception as exc:
        rec.update(status="malformed", notes=rec["notes"] + ["could not count pages: " + _short(exc)])
        return rec
    rec["page_count"] = count
    try:
        meta = reader.metadata or {}
        rec["info"] = {k: str(meta.get(k))[:500] for k in ("/Title", "/Author", "/CreationDate", "/Producer")
                       if meta.get(k) is not None}
    except Exception as exc:
        rec["notes"].append("metadata unreadable: " + _short(exc))
    if count == 0:
        rec.update(status="malformed", range=[0, 0])
        rec["notes"].append("the PDF has no pages")
        return rec
    first = max(1, first)
    last = count if last is None else min(last, count)
    rec["range"] = [first, last]
    failed = 0
    for n in range(first, last + 1):
        try:
            text = _clean(reader.pages[n - 1].extract_text())
            rec["pages"].append({"page": n, "text": text, "chars": len(text.strip())})
        except Exception as exc:
            failed += 1
            rec["pages"].append({"page": n, "text": "", "chars": 0, "error": "extract_failed: " + _short(exc)})
    if failed:
        rec["status"] = "partial"
        rec["notes"].append(f"{failed} page(s) in this range failed to extract (see each page's error)")
    return rec


def main(argv):
    a = _args(argv)
    src = pathlib.Path(a["shelf_root"]) / a["sha256"] if a["shelf_root"] else pathlib.Path("source.bin")
    data = src.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    out = {"schema": SCHEMA, "tool": TOOL, "sha256": a["sha256"], "kind": a["kind"], "bytes": len(data)}
    if digest != a["sha256"]:
        out.update(status="hash_mismatch", page_count=None, range=None, pages=[], info={},
                   notes=[f"bytes read hash to {digest}, not the pinned sha256"])
    else:
        out.update(_html(data) if a["kind"] == "html" else _pdf(data, a["first"], a["last"]))
    pathlib.Path("pages.json").write_text(json.dumps(out, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return 0 if out["status"] != "hash_mismatch" else 3


if __name__ == "__main__":
    os.umask(0o077)
    sys.exit(main(sys.argv[1:]))
