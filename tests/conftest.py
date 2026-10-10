import asyncio
import json
import os
import pathlib
import socket
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))
HOST_SRC = pathlib.Path(os.environ.get("OMEGALIBRARY_HOST_SRC",
                                       str(ROOT.parent / "omegahive")))


class NetworkUsed(AssertionError):
    pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test runs with the network cut: any connect or name lookup fails the test loudly."""
    def refuse(*a, **k):
        raise NetworkUsed(f"network use attempted in a test: {a[:2]}")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    yield


@pytest.fixture(autouse=True)
def test_only_direct(monkeypatch):
    monkeypatch.setenv("OMEGALIBRARY_TEST_ONLY_DIRECT_EXTRACT", "1")   # TEST ONLY: unsandboxed bundle runs


class Lib:
    """A disposable library in tmp_path: write files, a seed, ingest, extract (test-only direct runner), rebuild."""

    def __init__(self, tmp):
        self.tmp = pathlib.Path(tmp)
        self.data = self.tmp / "data"
        self.files = self.tmp / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.sources = []
        self.collections = {"public": {"visibility": "public"}}

    def file(self, name, data):
        p = self.files / name
        p.write_bytes(data)
        return p

    def source(self, sid, paths, collection="public", kind="pdf", **meta):
        self.sources = [s for s in self.sources if s["id"] != sid]
        self.sources.append({"id": sid, "kind": kind, "title": meta.pop("title", sid), "collection": collection,
                             "locations": [{"path": str(p)} for p in paths], **meta})

    def seed(self):
        p = self.tmp / "seed.json"
        p.write_text(json.dumps({"schema": "omegalibrary.seed/1", "collections": self.collections,
                                 "sources": self.sources}))
        return p

    def ingest(self):
        from olib import fetch, grants, shelf
        out = shelf.ingest(self.seed(), self.data, fetch.Fetcher(offline=True))
        # ingest creates seed collections closed; the fixture then applies its policy as an (audited) operator would
        if grants.load(self.data) != self.collections:
            grants.write(self.data, self.collections, by="test fixture policy")
        return out

    def extract(self, runner=None):
        from olib import extract
        return asyncio.run(extract.extract_all(self.data, runner or extract.DirectTestRunner()))

    def rebuild(self):
        from olib import index
        return index.rebuild(self.data)

    def build(self):
        r = self.ingest()
        self.extract()
        self.rebuild()
        return r

    def set_collections(self, value):
        (self.data / "collections.json").write_text(json.dumps(value))


@pytest.fixture
def lib(tmp_path):
    return Lib(tmp_path)
