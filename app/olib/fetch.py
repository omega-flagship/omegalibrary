"""Polite, bounded fetching of the approved public seed pages and the original PDFs they link.

Scope (the owner's approval, plan r1 seed list): only the seed pages on SEED_PAGE_HOSTS, and a PDF only when the exact
URL appears as a link in a seed page already stored on the shelf. One request at a time, a pause between requests,
a timeout, a size cap and an identifying User-Agent. Nothing else in OmegaLibrary touches the network.
"""

import html.parser
import time
import urllib.error
import urllib.parse
import urllib.request

from .util import now_iso

SEED_PAGE_HOSTS = frozenset({"hyperon.opencog.org", "wiki.opencog.org"})
USER_AGENT = "OmegaLibrary/0.1 (OmegaHive knowledge node, slice 1; polite single fetch per source)"
TIMEOUT = 30
MAX_BYTES = 80 * 1024 * 1024
PAUSE = 2.0


class FetchRefused(Exception):
    pass


class _Links(html.parser.HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.links = base, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(urllib.parse.urljoin(self.base, href.strip()))


def page_links(html_bytes, base_url):
    """Absolute link targets in a stored HTML page (fragment removed), in document order."""
    p = _Links(base_url)
    p.feed(html_bytes.decode("utf-8", errors="replace"))
    p.close()
    out = []
    for link in p.links:
        link = urllib.parse.urldefrag(link)[0]
        if link not in out:
            out.append(link)
    return out


def check_seed_page(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in SEED_PAGE_HOSTS:
        raise FetchRefused(f"{url}: a seed page must be https on {sorted(SEED_PAGE_HOSTS)}")


def check_linked(url, linked_from_url, linked_from_bytes):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise FetchRefused(f"{url}: https only")
    if url not in page_links(linked_from_bytes, linked_from_url):
        raise FetchRefused(f"{url}: not linked from the stored seed page {linked_from_url}")


class Fetcher:
    """The only network client. Tests replace it; ``offline=True`` refuses every fetch."""

    def __init__(self, offline=False, pause=PAUSE):
        self.offline, self.pause, self._last = offline, pause, 0.0
        self.log = []

    def get(self, url):
        if self.offline:
            raise FetchRefused(f"{url}: offline (no network in this run)")
        wait = self._last + self.pause - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        started = now_iso()
        try:
            resp_cm = urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:   # recorded as an attempt, then raised: never a silent skip
            self._last = time.monotonic()
            self.log.append({"url": url, "fetched_at": started, "http_status": exc.code, "error": str(exc)[:200]})
            raise
        except Exception as exc:
            self._last = time.monotonic()
            self.log.append({"url": url, "fetched_at": started, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            raise
        try:
            with resp_cm as resp:
                data = resp.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise FetchRefused(f"{url}: larger than {MAX_BYTES} bytes")
                info = {"url": url, "final_url": resp.geturl(), "http_status": resp.status,
                        "content_type": resp.headers.get("Content-Type", ""), "fetched_at": started,
                        "bytes": len(data)}
        finally:
            self._last = time.monotonic()
        self.log.append(info)
        return data, info
