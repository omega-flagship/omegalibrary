"""OmegaLibrary slice 1: a content-addressed source shelf, pinned page extraction and a rebuildable FTS5 index.

Layout of a data folder (``OMEGALIBRARY_DATA``):

    shelf/<sha256>            the original bytes, stored once, read-only
    manifest.json             every source: metadata, provenance of each location, current sha256, history
    extract/<sha256>.<tool>.json   per-page text from the pinned bundle (one record per source and bundle digest)
    index.sqlite              derived, rebuildable FTS5 index of current pages
    collections.json          who may see which collection (read on every call; never cached)
"""

VERSION = "0.1.0"
