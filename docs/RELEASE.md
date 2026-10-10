# Release pins: OmegaLibrary 0.1.0-ecbd701

| What | Value |
|---|---|
| App source digest (`Programs.source_digest(app/)`) | `sha256:f6eb951687757b130be1c964e9d70b247747a7b28e1cac48cbda8a2a3218ad07` |
| `app/tools/bundle.lock.json` sha256 (pin it in `HIVE_APP_PINS` `files`; the source digest does not cover it) | `9c9b6b4078c68ae9d3052e05ce15f6f4118dbec00de6b4ad01111690ccd92484` |
| Vendored pypdf | 6.19.0 (BSD-3, `app/tools/vendor/pypdf-LICENSE.txt`) |

`app/` matches the installed release 0.1.0-ecbd701 except for one comment: `app/olib/fetch.py` line 3 names the
owner by role. The digest above is the installed release's; this copy's `Programs.source_digest(app/)` is
`sha256:a5e6fb98fad112ee42627967f09913d1a8e90ed798e52a9179691305ccf56128`. Pin the digest of the bytes you install. The full build receipt for slice 1 (review
rounds, the local shelf build and its hashes) is an internal record and is not included in this repository.
