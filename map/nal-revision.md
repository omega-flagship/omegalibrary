# Map: NAL truth-value revision → the hive's commons

*Worked map page, OmegaLibrary slice 1. Written 2026-10-07 by the Platform lane. Every link carries its own
state; a state is claimed only where the evidence named next to it exists.*

States used here: **source-present** (the cited thing exists at that exact place), **tested** (a named test was run
by me on that exact commit and passed, or "test exists, not run"), **reviewed** (a review receipt names it),
**live** (shown running in a deployment). "Not shown" means I have no evidence either way.

## 1. Concept

NAL revision merges two judgements about the same statement drawn from **disjoint evidence** into one with more
evidence. With weights w = c / (1 − c):

- frequency f = (w1·f1 + w2·f2) / (w1 + w2);
- confidence c = w / (w + 1), where w = w1 + w2.

When the evidence overlaps, NAL does not revise: it chooses one judgement.

## 2. Theory sources on the shelf

| Citation | What it is | State |
|---|---|---|
| PLN book, sha256 `58d8e8b41923950370e01a76977098dc1b13a2fdfbcb75686768f720c058364a`, **PDF page 312** (printed page 308), Appendix A | Puts the NARS revision formula next to the PLN revision formula. This is the closest on-shelf statement of the NAL rule. It is a **secondary** source: PLN authors describing NARS. | source-present (read back through `olib.py read`; the original's hash was verified) |
| same sha256, **PDF pages 118–120** (printed pages 114–116), section 5.10 "Revision" | PLN's own revision rule. Page 119 notes that revision needs truth values with at least two components, as NARS has. | source-present |
| same sha256, PDF page 128 | "our truth-value addition rule is nothing but revision!" | source-present |
| Pei Wang, *Non-Axiomatic Logic* (the primary NAL source) | — | **gap: not on the shelf.** Slice 1 was limited to the project lead's named seeds. |
| https://wiki.opencog.org/w/Hyperon (historical) | Theory pointers | **gap: not fetched.** The host answered HTTP 429 "Site Unavailable" on 2026-10-06 (see RECEIPT.md). |

PDF page numbers are physical pages (1-based) in the pinned file. The printed page labels differ by 4 in this
manuscript. A citation always uses the PDF page number.

## 3. Implementation (host source)

Host checkout: `<omegahive checkout>`, branch `platform/cloud-next`, commit
**`1f7353dd0d164f9fb5b699d5b89719d9d1a1c0ca`**. Line numbers are at that commit.

| Link | Where | Last changed in | State |
|---|---|---|---|
| Reference NAL rule (upstream OmegaClaw library) | `lib_nal.metta:92` `Truth_Revision`; used at `:108` (NAL-1 `!Revision`) | `5980f1a6` | source-present |
| Hive copy of the rule | `hive/spaces/lib_hive.metta:25` `hive-truth-revision`. Its comment (line 20) says it is the "same rule as Truth_Revision in lib_nal.metta" | `5980f1a6` | source-present |
| Revise only on disjoint stamps, otherwise choose | `hive/spaces/lib_hive.metta:99` `hive-integrate!`: disjoint stamps → `revised`; overlapping stamps → `chosen` or `kept` | `5980f1a6` | source-present |
| Publish into a swarm commons | `hive/spaces/lib_hive.metta:123` `hive-publish!` (ACL, vocabulary quarantine, duplicate check, then `hive-integrate!`) | `5980f1a6` | source-present |
| Python → PeTTa call | `hive/spaces/service.py:204` `_apply_publish` builds `(hive-publish! &commons_<swarm> …)`; `:214` `publish` | `5980f1a6` | source-present |
| Commons API | `hive/core/hive.py:841` `Hive.publish`: evidence stamps, echo protection, then `self.spaces.publish` at `:891`; emits `belief.updated` on `revised` | `620d0f40` | source-present |
| Governance commons reuse | `hive/core/governance_evidence.py:4`: "the same lib_hive revision every swarm commons uses" | (not checked) | source-present |

**Local change against the reference rule (read from source, not reviewed).** Both copies clamp the result to
`(stv (min 1.00 f) (min 0.99 (max (max c c1) c2)))`. Revised confidence never falls below either input and is
capped at 0.99. Plain NAL has c = w / (w + 1) with no floor. The rationale for this choice is not recorded anywhere I
looked. That is a gap for the ecosystem map.

## 4. Tests

| Test (at `1f7353d`) | What it covers | State |
|---|---|---|
| `hive/tests/test_core_api.py:168` `test_publish_echo_revision_and_provenance` | the outcomes `adopted` → `kept` → `revised` through the API | **tested**: passed in my run (below) |
| `hive/tests/test_core_api.py:210` `test_repeating_a_belief_does_not_inflate_confidence` | echo protection: no self-revision | **tested**: passed |
| `hive/tests/test_drift_guards.py:20`, `:30` echo-after-read tests | a re-published read inherits the read stamp, so it cannot revise upward | **tested**: passed |
| `hive/tests/test_acceptance.py:69` `test_three_omegas_share_revise_and_survive_restart` | three minds revise and the result survives a restart | test exists, **not run** (needs `HIVE_E2E=1`, which starts a hive process; skipped by me) |
| `tests/hive_lib_smoke.metta:30-34` | `hive-truth-revision` agrees with `Truth_Revision` (0.0001 tolerance) | test exists, **not run** (needs an OmegaClaw runtime `run.sh`) |

My run, on a `git clone --shared` of the host at exactly `1f7353d` in a scratch folder (the host tree itself was
not touched):

```
PATH=$HOME/swipl-patched/bin:$PATH PETTA_PATH=<PeTTa checkout> python -m pytest -q -p no:cacheprovider \
  hive/tests/test_core_api.py::test_publish_echo_revision_and_provenance \
  hive/tests/test_core_api.py::test_repeating_a_belief_does_not_inflate_confidence \
  hive/tests/test_drift_guards.py::test_echo_after_query_inherits_the_read_stamp \
  hive/tests/test_drift_guards.py::test_echo_after_reading_a_belief_inherits_the_read_stamp \
  hive/tests/test_acceptance.py::test_three_omegas_share_revise_and_survive_restart
→ 4 passed, 1 skipped (HIVE_E2E) in 1.43s
```

These tests check outcomes (`revised`, `kept`, stamps). None of them checks the numbers of a revised truth value
against an independent NAL calculation. Only the `.metta` smoke compares the two rule copies, and I did not run it.

## 5. Review and deployment

| Link | State |
|---|---|
| A review receipt naming `lib_hive` revision | **not shown.** None was found among the platform's review receipts by filename. |
| Live: a running hive runs this code | **not shown.** The live hive was not queried. Checking it needs the owner's loaded-release record. |

## 6. Open questions this page raises

1. Where is the rationale for the confidence floor `max(c, c1, c2)` recorded? That floor departs from plain NAL.
2. Should a numeric test compare `hive-truth-revision` against a hand calculation in the Python suite, so the
   claim "same rule as lib_nal" is tested on every run and not only in the `.metta` smoke?
3. The primary NAL source should be added to the shelf, so this page cites theory first-hand.
