# Status: OmegaLibrary 0.1.0-ecbd701

Tier: Open (Apache-2.0). Checked on 2026-10-10.

## What works

- **Shelf, extraction, index, search and read with page citations** for people and attached minds, with
  fail-closed collection access, per-collection ranking, verified hits and chunked delivery of exact originals
  (`docs/DESIGN.md`).
- **Tests,** in a fresh Python 3.12.13 venv:
  - with `requirements-dev.txt` only and omegahive next to this repository: **37 passed, 1 skipped** (the
    host-integration test needs the hive's own dependencies); with no omegahive checkout at all: 35 passed, 3 skipped;
  - plus omegahive's `hive/requirements.txt`, omegahive `7b1ce3f` next to this repository and PeTTa `ae66fa8`:
    **38 passed**. The host test reported `r15 read actions: True`: mind reads are kept only as digests.
  - rerun after the comment fix below, against omegahive `e4571e9` (`OMEGALIBRARY_HOST_SRC` set): **38 passed**.
  - Every test runs with network access cut.
- **Contract check.** With `OMEGALIBRARY_DATA` set to an empty folder: **COMPATIBLE** with program contract 0.1
  (0 items, because the checker has no viewer and every seed is in the restricted `local` collection).

## Limited or known broken

- **No shelf ships.** You build it from `seed.json` and your own copies of the local PDFs. The two public seed
  pages (`hyperon.opencog.org`, `wiki.opencog.org`) answered HTTP 429 when slice 1 was built, so their linked PDFs
  were never fetched.
- **Licences unknown.** Every seed source has `licence: unknown`. Such sources stay in the restricted `local`
  collection (hive admins and explicitly granted swarms only) and must not be served hive-wide or publicly.
- **Host seams open** (`docs/DESIGN.md`, "Host seams"): no host-enforced per-mind rate field (the app's own
  ledger limits each mind to 30 calls a minute); no per-app read-only data mount for large sources; originals
  are not in the host asset store; collection grants live in the app's data folder; no contained PDF viewer that
  opens a citation at its page.
- **No OCR.** Image-only pages are labelled, never read.
- **Search cost** grows with the number of visible matches; a large corpus needs a bounded top-k per collection.
- **Module name.** The package imports as `olib` in the hive process; another app with a top-level `olib` would
  collide.
- Code comments in `app/` name the review rounds (for example "Kimi r3", "stand-in r3"); they are unchanged.
- **`app/` differs from the installed release by one comment.** `app/olib/fetch.py` line 3 says "the owner's
  approval" where the release names the owner; fixed in the handover copy, not yet in the owner's tree. No code
  changed. Every other file in `app/` is byte-identical to the installed release.
