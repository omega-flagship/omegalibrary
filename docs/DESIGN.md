# Design and operating notes

These are the owner's development notes for slice 1, kept as written apart from removing names and links to the
owner's private workspace. `data/` means the folder named by `OMEGALIBRARY_DATA`. Host commits named here
(`a17bdda`, `1f7353d`, `9abe9df`) are platform commits from before the handover and are not in the published
omegahive history; the published omegahive includes the r15 read actions this app needs.


OmegaLibrary is a Dot program (contract 0.1, `hive/PROGRAMS.md`) that keeps a pinned shelf of original sources. It
extracts page text with a pinned tool bundle, indexes the text in a rebuildable SQLite FTS5 index, and lets people
and attached minds search and read with exact page citations. It is built to the platform's knowledge-node plan (approved by the project lead; the plan itself is internal).
At the time these notes were written it was not deployed.

```
seed.json ──ingest──▶ data/shelf/<sha256>   (originals, stored once, read-only)
                      data/manifest.json   (metadata, provenance of every location, current sha, history)
          ──extract (tools/ bundle through the host's sandbox, offline)──▶ data/extract/<sha>.<bundle>.json
          ──rebuild──▶ data/index.sqlite   (derived; delete it any time)
app/program.py: describe / view / read / act, plus offer / bind / unbind for minds
```

## Layout

| Path | What |
|---|---|
| `app/` | The release folder: the one holding `plugin.json`, and what `HIVE_PLUGIN_DIRS` / `HIVE_APP_PINS` point at |
| `app/program.py` | The app: shelf, search and page views; reads `shelf`/`search`/`read`; mind actions `library_search`/`library_read` |
| `app/olib/` | The library code, standard library only (the hive's Python needs nothing extra) |
| `app/tools/` | The pinned extraction bundle: `extract_pages.py`, vendored pure-Python pypdf 6.19.0 (BSD-3, `vendor/pypdf-LICENSE.txt`) and `bundle.lock.json` |
| `seed.json` | The seed manifest: exactly the plan's seed list |
| `olib.py` | The operator command line |
| `map/nal-revision.md` | The worked map page |
| `tests/` | pytest; synthetic fixtures only |

## Ingest, extract, rebuild

```bash
cd omegalibrary
export OMEGALIBRARY_DATA=$PWD/data
.venv/bin/python -B olib.py ingest            # copies local seeds; fetches the public pages and the PDFs they link
.venv/bin/python -B olib.py ingest --offline  # local seeds only; URL sources are reported as errors, never skipped
.venv/bin/python -B olib.py extract --runner host-sandbox \
    --host-src <OmegaHive checkout> --python <Python 3.12 venv>/bin/python
.venv/bin/python -B olib.py rebuild           # the index is derived; rebuilding the same inputs gives the same digest
.venv/bin/python -B olib.py status
.venv/bin/python -B olib.py search "revision rule"
.venv/bin/python -B olib.py read <sha256> <page>
```

- **Ingest.**
  - Each entry is one edition. All of an entry's locations must hold identical bytes; otherwise the entry is refused,
    never resolved by picking one.
  - Identical bytes across entries are stored once and marked `same_bytes_as`.
  - Changed bytes for an existing entry move the old sha256 to `history` as `superseded`. The old blob stays, so old
    citations still resolve.
  - Every run writes `data/ingest-reports/<time>.json`, including every fetch attempt and its HTTP status.
- **Fetch scope.**
  - Seed pages are fetched only on `hyperon.opencog.org` and `wiki.opencog.org`, over https.
  - A PDF is fetched only when its exact URL is a link in the stored seed page named by `linked_from`.
  - Fetching is one request at a time, with a 2 s pause, a 30 s timeout, an 80 MB cap and an identifying
    User-Agent. A URL fetched once is not fetched again unless `--refetch` is given.
- **Extraction.**
  - `--runner host-sandbox` imports the host's own `hive/core/sandbox_run.py` from a frozen checkout and runs the
    bundle exactly as `ctx.sandbox_run` does: `sandbox-exec` on macOS, Landlock on Linux. The bundle runs with no
    network, writes only to its job folder, and has CPU and file-size limits. The bundle is verified against its
    lock before every run.
  - Large PDFs are run 40 pages at a time.
  - Every file gets a label: `ok`, `partial`, `encrypted`, `image_only`, `malformed`, `empty`, `hash_mismatch`,
    `too_large_for_sandbox` or `extractor_failed`.
  - Records are keyed by source sha256 and bundle digest, and say which runner made them. A production
    (`host-sandbox`) run never reuses a record made by the test-only runner.
  - For a source over 20 MiB the entry is given only `--shelf-root`; it opens `<shelf-root>/<sha256>` itself, never a
    caller-chosen path, and re-hashes the bytes.
  - A changed extractor (changed forward **or rolled back** on the same data folder) never mixes with old text:
    search serves nothing (`needs_rebuild`) until the index is rebuilt under the current extractor, so search
    citations and page reads always agree. The check compares bundle digests, not generations, so a rollback is
    refused like any other change until rebuilt; rollbacks stay operator-reviewed.
- **Test-only runner.** `--runner test-only-direct` runs the same verified entry **without** the hive's sandbox. It
  is refused unless `OMEGALIBRARY_TEST_ONLY_DIRECT_EXTRACT=1`. It exists only for tests. Its child still has no
  network: `sandbox-exec` with every network operation denied on macOS, `unshare -rn` on Linux where permitted, and a
  Python socket guard in every case.
- **No extraction inside the hive.** `plugin.json` declares no capabilities, so the app cannot use the host's
  `sandbox:run` even if it is listed in `HIVE_SANDBOX_RUN_APPS`. Extraction runs only from the operator command line.

## Access

`data/collections.json` is read on every call and never cached. Each collection has one of two forms:

- `{"visibility": "public"}`;
- `{"visibility": "swarms", "swarms": ["s_..."], "admins": true|false}`: the listed swarms, plus hive admins when
  `admins` is true.

Anything else is visible to no one (fail closed).

- **Who is asking** comes only from the host: the call's `ctx.swarm_id`, and whether `ctx.viewer` (or a person
  `ctx.actor`) has the hive role `admin`. A mind is never an admin.
- **The seed's collections:**
  - `public` holds the public web seeds;
  - `local` holds the project lead's local PDFs, visible only to hive admins and to swarms explicitly granted it.
    Hive-wide reading of them has not been approved. Sources whose licence is unknown stay in `local`.
- **Every access change is a named, audited command.**
  - Ingest creates any collection the seed declares and the policy lacks **closed**
    (`{"visibility":"swarms","swarms":[],"admins":false}`), whatever the seed says, and never changes an existing
    rule.
  - Opening a collection is `olib.py set-policy <collection> --visibility public|swarms [--admins] --by
    "<who approved>"`.
  - A grant or revocation is `olib.py grant|revoke <collection> <swarm> --by "<who approved>"`.
  - `--by` is required and must not be blank. Each command appends an audit line to
    `data/collections-audit.jsonl`: time, OS user, by, and the policy digest before and after.
  - A hand edit of the file bypasses the audit (seam S7).
- **Nothing hidden leaks.** A source the caller may not see answers exactly like one that does not exist. The
  answer carries:
  - no title, id or snippet;
  - no page or hit count;
  - no duplicate reference (`same_bytes_as` lists only visible twins);
  - no index-freshness change and no view-revision change. Freshness is computed over visible sources only, so
    changing a hidden source does not move it, even before a rebuild.
  - The view revision hashes only the visible entries and their extraction status, never index freshness. A
    rebuild alone, before or after a grant, does not move it.
  - Freshness is reported only in search and shelf answers, about the sources the caller sees now. After a grant,
    the newly visible sources' freshness is information about sources the swarm may now see.
- **Ranking ignores hidden collections.** Each collection has its own FTS5 table, so BM25 statistics come only from
  that collection's pages. A search runs only over the tables of collections the caller may see, and filters by
  visible sha256 inside each query, before any snippet or count exists. Adding, growing or removing hidden pages
  changes no visible hit, snippet, order or total (tested; the old single table failed the same test).
- **Every served hit is verified.**
  - Before search returns a hit, its original is checked to still hash to its sha256. A full hash is cached
    against the file's identity (inode, size, mtime, ctime); any write or replacement forces a fresh hash.
  - A missing or tampered original gives no hit. It appears only in `withheld` with reason `original_unverified`;
    the caller can see that source, so naming it leaks nothing.
- **Revoking** a swarm takes effect on the next call, with no rebuild.

## Citations

Every hit is `{source_sha256, title, page, snippet}`, and every hit served is checked to resolve under the current
extraction. `read(sha256, page)` returns the extracted text of that page of that exact original. It re-hashes the
original on the shelf first, and refuses if the bytes have changed. A superseded original stays readable, says
`superseded` and names its replacement. **Pages are physical PDF pages (1-based), not printed page labels.** In the
PLN book the two differ by 4.

**Opening the original.** Each citation carries `original`: `{source_sha256, page, bytes, download, note}`.
- `download` names the app's declared read `original` `{sha256, offset?}`, called through the host's authorised route
  `POST /api/programs/omegalibrary/read`, for an original of **any size** (the PLN book included):
  - the host checks the person sees the swarm, and the app applies the same access as `read`;
  - each answer is one chunk of at most 3,000,000 bytes (the read route's 4 MB answer cap, after base64):
    `{name, mime, bytes, offset, length, next_offset, chunk_sha256, content_base64}`. Start at offset 0 and follow
    `next_offset` until it is null; `download.chunks` says how many there are;
  - a chunk is served only while the whole original still hashes to its sha256 and did not change while the chunk
    was read; the joined bytes hash to `source_sha256`;
  - the note states the page to go to.
- What the person does with the bytes, and opening them at the page, is the host's part (S8).

## Install as a pinned app (operator; not done)

1. Freeze a release copy of `app/` at a commit (for example `<releases>/omegalibrary/0.1.0`).
   Nothing under it may be edited afterwards.
2. Prepare a data folder outside the release, and run ingest, extract and rebuild into it.
3. Set the hive environment:
   - `HIVE_PLUGIN_DIRS=<release>` (appended);
   - `OMEGALIBRARY_DATA=<data folder>`;
   - optionally `OMEGALIBRARY_SWARMS=<home swarm ids>`;
   - `HIVE_APP_PINS`:
     `{"omegalibrary": {"release": "<release>", "digest": "<Programs.source_digest(release)>", "files": {"tools/bundle.lock.json": "<sha256>"}, "env": {"OMEGALIBRARY_DATA": "<data folder>"}}}`.
     The source digest covers `plugin.json` and every `.py` file, including the vendored pypdf. It does **not** cover
     `tools/bundle.lock.json`, so pin that file in `files`.
4. Do **not** add `omegalibrary` to `HIVE_SANDBOX_RUN_APPS`, and give no shared runtime the shelf. The app declares
   no capabilities, so in-hive extraction is impossible by construction; extract offline (S4, S6).
5. A named admin activates the app in a swarm, then attaches a mind with
   `actions: ["library_search", "library_read"]` and one allow/ask decision each. Revoking the attachment ends
   access. **Attach minds only on the live release main `a17bdda` (r15 read actions) or later** (S1 below). Older
   hosts retain every mind read in the hive's database.
6. Grant `local` to a swarm only with the project lead's explicit approval, recorded in `--by`.

The release digest and pinned file hashes are in `docs/RELEASE.md`. Deploying follows the platform rule: two
independent review verdicts before any restart.

## Host seams

- **S1. Mind reads are not retained.** Fixed in the host by Platform r15, now **live** in release main `a17bdda`.
  The earlier hosts (`cloud-next` `1f7353d`, and the r15 review branch `readacts` `9abe9df`) are superseded for
  this app.
  - Both mind actions declare `"effect": "read"`. On an r15 host:
    - the hive stores only `{redacted, digest, status}`;
    - a repeat re-runs the read under the current grants;
    - the app's ctx is read-only.
  - `tests/test_host_integration.py` asserts all of this when the host has r15: no page text in `program_actions`,
    `host_read_only` true, and a same-key repeat discloses nothing once the collection is hidden. It prints the host
    commit it ran against and skips on a host without r15.
- **S2. Per-mind rate limits for app actions.** App side done; host side still open.
  - The app's limit (30 calls a minute per mind) is a ledger in the data folder (`data/rate.sqlite`: actor id and
    time per call, nothing else), so it survives reloads and is shared by every process on the same data folder.
    If the ledger cannot be used, the call is refused.
  - Still needed: a rate field on the attachment policy, enforced by the hive in `program_attachments.authorize`.
    The reviewers ask for this before minds are attached.
- **S3. Host-enforced read-only mind actions.** Fixed by r15, live in `a17bdda`. The app reports `host_read_only` in each
  result, so a host that does not enforce it shows up in the tests.
- **S4. Large sources in `sandbox:run`.**
  - The input cap is 20 MiB. The 45.5 MB PLN book was extracted offline, by listing the shelf folder in the
    runtime's operator `reads`. That would grant the shelf to every app allowed to use that runtime name, so do not
    configure it on a shared hive.
  - The entry now opens only `<shelf-root>/<sha256>`.
  - Needed: a per-app read-only data mount, or a larger input cap.
- **S5. Originals in the host asset store.** Open, and not claimed. Slice 1 keeps the shelf in the app's own data
  folder. Needed: a Library-swarm asset put (or a curator flow), after which `GET /api/assets/{sha256}` could serve
  any size with the host's own access check.
- **S6. App maintenance tasks.** Open. There is no in-hive extraction: the app no longer declares `sandbox:run`
  (review round 3) and has no extraction entry point. Extraction runs from the operator command line. A later slice that
  wants it in the hive re-declares the capability with a reviewed maintenance route.
- **S7. Collections are app-internal.** Open. Grants are audited by the command line, but the policy file lives in
  the app's data folder, and the host's visibility rules (R13) do not know these collections. Needed: a
  host-managed, named-admin, audited store for app collection grants.
- **S8. Opening the original PDF at a page.** App side done; the viewer is host work and still open.
  - The app delivers the exact pinned original of any size through the host's authorised read route, in verified
    chunks, with the page stated (above). The PLN book is no longer without delivery.
  - The app cannot open a PDF at a page itself on this host:
    - the host's `show-file` is download-only for PDFs (Review I1), takes no page and stops at 20 MB;
    - an app's own `ui/` frame is limited to three files of at most 600 KB each (`programs.py` `UI_FILES`), too small
      for a PDF renderer such as pdf.js;
    - this app ships no `ui/` frame. A frame is new interface work and waits for its own approved plan.
  - Still needed from the host:
    1. **A contained viewer.** pdf.js, rendering to canvas inside a sandboxed iframe with no `allow-same-origin`,
       popups or top navigation. Links inside the PDF are disabled or routed through `open-url`. A real-browser test
       proves a PDF link cannot navigate the host tab (the reason `show-file` is download-only today, Review I1).
    2. **A page parameter.** `show-file` accepts `page: <int ≥ 1>`, and the viewer opens at that physical page.
    3. **Large files.** Above 20 MB, the viewer takes bytes the frame fetched in chunks, or a byte route with HTTP
       ranges served by an app hook under the same viewer and swarm checks, re-hashed against the sha256, typed
       `application/pdf` with nosniff, never stored by the host (or, after S5, the asset route).
    4. **Frame plumbing.** The frame gets the bytes or the byte route without seeing any URL.
  - **People acceptance of "a citation opens the exact page" is not claimed** until those exist.

## Limitations

- **Public seeds not fetched.**
  - Both `hyperon.opencog.org` and `wiki.opencog.org` answered **HTTP 429** with a "This site is currently
    unavailable" hosting page, on three attempts on 2026-10-06.
  - So the public pages are not on the shelf, and neither are the PDFs they link: links can only be chosen from a
    stored page.
  - The seed entries remain, and each ingest reports them as errors.
- **Metadata.** Authors are not curated for the seven ecosystem papers or the PLN book (`metadata_source` says so),
  and the licence is unknown for every source. Titles come from filenames. The PDFs' own `/Title` and `/Author`
  values are kept in the extraction records.
- **Collection.** Local PDFs are in the restricted `local` collection: hive admins and explicitly granted swarms only.
  The `public` collection is empty until the public seeds can be fetched.
- **Extraction limits.** There is no OCR: image-only pages are labelled, never read. One PLN book page has no text
  layer and is listed in `empty_pages`.
- **Search cost.** Search collects every visible match, to rank across visible collections and count them. That is
  fine at slice-1 size (661 pages); a large corpus needs a bounded top-k per collection.
- **Global extractor state.** `needs_rebuild` after an extractor change is global. It describes the extractor, not
  any source.
- **Contract checker.** The host's contract checker has no viewer, so it sees 0 items (all seeds are `local`). The
  views are validated with content by the tests, which use the host's own `validate_graph`.
- **Module name.** The app's package is importable as `olib` in the hive process. A different app with a top-level
  `olib` would collide.
