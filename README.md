# OmegaLibrary

OmegaLibrary is the OmegaHive knowledge node: a pinned shelf of original sources, page text extracted offline with
a pinned tool bundle, a rebuildable SQLite FTS5 index, and search and read with exact page citations for people
and attached minds. Access is per collection and fails closed: a source the caller may not see answers exactly
like one that does not exist.

**Tier:** Open source, Apache-2.0 (`LICENSE`; notices, including the vendored pypdf's BSD-3 licence, in `NOTICE`).

This repository is release **0.1.0-ecbd701**: `app/` matches the installed release (host source digest
`sha256:f6eb9516…ad07`, see `docs/RELEASE.md`) except for one reworded comment in `app/olib/fetch.py`, which gives
this copy the digest `sha256:a5e6fb98…6128`. No shelf data is included. The shelf, extracts and index
are built locally from `seed.json` into the folder named by `OMEGALIBRARY_DATA`. Seed entries for local PDFs use
the placeholder `<local-papers>/`; edit them to point at your own copies.

| Path | What |
|---|---|
| `app/` | The app folder: `plugin.json`, `program.py`, the library code `olib/` (standard library only) and the pinned extraction bundle `tools/` (with vendored pypdf 6.19.0) |
| `olib.py` | The operator command line: ingest, extract, rebuild, status, search, read, collection policy |
| `seed.json` | The seed list (public pages and named local PDFs; titles only, no files) |
| `map/nal-revision.md` | A worked map page |
| `tests/` | pytest; synthetic fixtures only, network cut in every test |
| `docs/DESIGN.md` | Design and operating notes: pipeline, access model, citations, install, host seams, limitations |
| `docs/RELEASE.md` | Release digest and pinned hashes |

## Requirements

- **Python 3.12.** The app and `olib.py` use only the standard library.
- **For the tests:** `requirements-dev.txt` (pytest 9.1.1 and pypdf 6.19.0; the tests build and read synthetic
  PDFs with pypdf).
- **For the host-integration test and the contract check:** an OmegaHive checkout (the `omegahive` repository) with
  its `hive/requirements.txt`, and PeTTa at commit `ae66fa8` with SWI-Prolog 10.1.14 and janus-swi 1.5.3 built
  against the same Python, as in the omegahive README ("Prerequisites").

## Test

From this folder, in a fresh Python 3.12 virtual environment:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests          # 37 passed, 1 skipped (the host test, without a host)
```

With OmegaHive checked out next to this repository (or `OMEGALIBRARY_HOST_SRC` set), two more tests use the host's
source: one validates the app's views with the host's validator, one extracts through the host's own
`sandbox_run`. Without a checkout they skip too (35 passed, 3 skipped).

The host-integration test runs the app inside a disposable in-process hive (no port, no live hive). Use a venv
that also has omegahive's `hive/requirements.txt`, with OmegaHive checked out next to this repository (or set
`OMEGALIBRARY_HOST_SRC`):

```sh
.venv/bin/pip install -r ../omegahive/hive/requirements.txt
export PATH=$HOME/swipl-patched/bin:$PATH    # the SWI-Prolog 10.1.14 built for PeTTa
export PETTA_PATH=<PeTTa checkout>
.venv/bin/python -m pytest -q tests          # 38 passed
```

Contract check, from the OmegaHive checkout (the app refuses to load without a data folder):

```sh
cd ../omegahive
OMEGALIBRARY_DATA=$(mktemp -d) python -m hive.programs_check ../omegalibrary/app   # COMPATIBLE, 0 items
```

## Quick start (build a shelf and mount it)

```sh
export OMEGALIBRARY_DATA=$PWD/data
python -B olib.py ingest --offline    # local seeds only; URL seeds are reported as errors
python -B olib.py extract --runner host-sandbox --host-src ../omegahive --python <Python 3.12 venv>/bin/python
python -B olib.py rebuild
python -B olib.py search "revision rule"
```

Then mount `app/` in a hive: `HIVE_PLUGIN_DIRS=<release copy of app/>`, `OMEGALIBRARY_DATA=<data folder>`, and
pin it with `HIVE_APP_PINS`, including `tools/bundle.lock.json` in `files`. Do not add `omegalibrary` to
`HIVE_SANDBOX_RUN_APPS`: extraction runs only from the operator command line. Full steps: `docs/DESIGN.md`,
"Install as a pinned app".

## How it plugs into OmegaHive

OmegaHive loads apps ("Dot programs") from the folders in `HIVE_PLUGIN_DIRS`. For OmegaLibrary that folder is
**`app/`**, which holds `plugin.json` and `program.py` implementing program contract 0.1 (described in
`hive/PROGRAMS.md` in the omegahive repository).

- The app declares **no capabilities**, so it cannot use the host sandbox.
- `view()` shows the shelf, search results and pages; `read()` answers `shelf`, `search`, `read` and `original`
  (chunked delivery of an exact original through the host's read route).
- Minds use `library_search` and `library_read`, both declared `"effect": "read"`, only after a named admin
  attaches them to the app. On a host with the r15 read actions (the published omegahive has them) the hive keeps
  only a digest of each mind read.
- Who is asking comes only from the host (`ctx.swarm_id`, the viewer's hive role). Collection access is set with
  audited `olib.py set-policy|grant|revoke --by "<who approved>"` commands.

## Licence

Apache-2.0 (`LICENSE`). The vendored pypdf is BSD-3 (`app/tools/vendor/pypdf-LICENSE.txt`); see `NOTICE`.
