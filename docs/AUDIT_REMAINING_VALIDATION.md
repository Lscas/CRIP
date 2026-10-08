# Remaining findings from the 17-item review

## Scope

DEV-173 / CR-0189 continues from feature commit
`31ac925cae133a56ee3a5890c956d7364a031c4e`. The original review baseline is main
`d579486bade549f73f9996b812db6e360a0d6dc1`. This is source-only repair and feature
branch synchronization, not a main merge, deployment, customer migration, paid
model call or engineering-accuracy assessment. All new fixtures are synthetic.

## Remaining repairs and safety boundaries

- **4 — publication isolation:** only candidate schema ValidationError is
  quarantined. Evidence scope is checked first; business-invariant, assembly,
  database and ambiguous legacy-identity failures remain fatal. Valid siblings
  and safe issue diagnostics commit together. Original extraction is retained.
  A unique historical record linked to an invalid successor retains its exact
  human envelope/version/history, but is explicitly blocked from new review,
  verification and both export formats. Recovery atomically replaces the issue
  snapshot. An issue without a historical match permits partial export of valid
  siblings with an explicit notice.
  Publication does not imply complete coverage or create a Missing review item.
- **9 — dependent verification:** inspection requirement participates in semantic
  context. Changing it invalidates dependent check keys without resetting human
  review or charging a provider. Unchanged fields can still reuse valid checks.
- **11 — resume parity:** GET returns can_resume/reason from the same read-only
  predicate rechecked inside the resume transaction. Local publication failures
  with completed extraction can resume; all existing deadline, provider, active
  work and unresolved-cost guards remain. Reads never authorize paid retries.
  A completed PARTIAL run with publication issues and no pending evidence can
  also resume through the public API; a corrected converter reuses stored
  extraction, clears issues and then disables further resume. Other completed
  PARTIAL runs remain closed.
- **14 — CAD completeness:** text is split into handle-addressable fragments, not
  silently cut at 1,200 characters. Resource-limit or text-read loss is explicit
  PARTIAL with warnings. A resource limit is not claimed to be full extraction.
- **15 — CAD array instances:** use ezdxf mcount, including zero-spacing semantics;
  retain original handles, row/column counts and spacing as calculation evidence.
  Nested blocks, legend exclusion and actual material quantities remain outside
  this direct modelspace-object count.
- **17 — history queries:** an additive nonunique (run_id, task_key) index and
  SQL-bounded family lookup preserve manual/legacy recovery semantics. State
  checks read response-presence metadata; only recovery loads a saved response.
  The index does not introduce a schema-version marker; existing schema28
  integrity checks still run. A same-named wrong/partial/unique index is rejected.
  The synthetic measurement does not establish a 24-hour project performance SLA.

## Original 17-item regression map

The first-batch results remain historical evidence in
`docs/AUDIT_REPAIR_VALIDATION.md`; they are not relabelled as this source's gate.

| Finding | Current regression boundary |
| --- | --- |
| 1 numeric attributes | test_review_fact_assembly; test_review_export_integrity |
| 2 quantity revision | test_review_fact_assembly, including reversed order/unknown dates |
| 3 scoped identity | test_review_fact_assembly; test_review_system_baseline |
| 4 temporary quantity / isolation | test_review_fact_assembly; test_review_publication_isolation; publication_issues.test.mjs |
| 5 human edits | test_review_system_baseline; accepted/edited successor-isolation tests |
| 6 original evidence | test_review_export_integrity; test_review_export_validator |
| 7 quantity review | test_review_export_integrity; quantity_review.test.mjs |
| 8 legitimate items | test_review_fact_assembly; test_review_export_integrity |
| 9 inspection context | test_review_verification_context; existing test_verification |
| 10 upload project | upload_project_boundary.test.mjs executes the actual upload loop with an A-to-B switch |
| 11 failed-run resume | test_review_resume_contract; resume_contract.test.mjs |
| 12 malformed provider output | test_review_provider_boundaries; test_gateway_budget |
| 13 setup boundary | test_review_setup_boundary; local-entry/setup tests |
| 14 CAD text | test_review_cad_integrity; test_parsers |
| 15 MINSERT | test_review_cad_integrity including nonzero/one-axis/zero spacing |
| 16 clean Git-byte manifest | build_bundle_manifest --check plus exact Git archive check |
| 17 family history | test_review_task_history; existing budget/recovery/migration tests |

## Validation status

The frozen remaining3 seven-command gate completed with exit 0: 2,892 unique
Python nodes, zero failures/errors/skips/duplicates, 2,036.899 seconds
(2,039.891 seconds including process overhead). Both JavaScript syntax checks,
24 language checks, 11 deployment-boundary checks, specification synchronization
(160 requirements / 37 schemas), and the 824-file source manifest passed. All
824 source files were unchanged from start to finish. The UI Node regression
wrappers and targeted tests overlap the Python suite and are not extra nodes.

Frozen source-snapshot SHA-256:
`0cce388bebc045e149b667a0a55f662fcc2418ff16b5aef1c7d6b36eb114a305`.
JUnit SHA-256:
`84ff151e887365653176fa4dfa8c8954d37390225b896784235b3b37256375d5`.
The frozen staged Git tree is `67f944d98058dd01cf5374695777ff7b00294e6d`;
its exact Git archive check passed with 824 manifest entries, 825 files including
the manifest, and zero missing/extra/byte-mismatched entries. Local receipts are
`reports/local/ci-repair-gate-remaining3-{source,result}.json`, its XML and log.
The other stopped attempts below are retained separately and are not acceptance.

This result is recorded after the gate. Only this report, DEV_STATE,
IMPLEMENTATION_STATUS and DEV-173 status change afterward, followed by manifest
regeneration. Application/test bytes remain the tested bytes. Independent final
receipt review, post-gate governance and exact staged Git archive checks precede
publication. Remote acceptance must use the resulting commit's own Actions run,
not a previous commit's green status. No customer deployment or engineering
accuracy claim follows from these offline checks.

Initial schema-isolation regression failed as expected (whole run FAILED and no
valid sibling published); initial resume tests failed because can_resume was
missing. Failed/superseded invocations remain under ignored reports/local and are
not acceptance evidence. No private evidence, credentials or customer databases
are included in the source bundle.

The first 191-test integration run retained two real failures: an experimental
version29 marker conflicted with the historical schema28 guard, and a new dynamic
Chinese application-owned CAD scope label was not in the English export cache.
The index now has no new schema-version marker and must preserve every existing
startup integrity check. The new CAD application label is English; source text
is unchanged. Replacement checks, not the failed attempt, determine acceptance.

Frozen attempt remaining1 was deliberately stopped after independent review
found that issue-bearing PARTIAL runs could not recover via the public Resume
endpoint. Its source identity was
`5787e4f65eb6097aadddc42622778f4412663003ed276ea8cb6cd37f35391e39`
(824 files, unchanged). The partial Python invocation exited nonzero after
327.594 seconds; no completed test count or success is claimed. All remaining
six commands finished, but this is not a passed gate. The new API regression
failed on can_resume=false before the bounded state-machine fix; both artifacts
are retained under reports/local. A new frozen gate is required.

The replacement publication/resume combination passed 26 Python nodes. Sol's
independent source preflight found no further blocking P0/P1 after the public
PARTIAL recovery correction. This is admission to the full gate, not its result.
Other overlapping targeted checks: 41 publication/export, 19 CAD/resume, 4 UI/
inspection context, and 7 task-history plus 43 recovery/schema28 checks passed.
They must not be added together or added to the full-suite total.

Synthetic task-history observation (8,510 entries, most with approximately 4 KB
responses): bounded metadata lookup 0.002090 seconds / 8,009 peak Python bytes;
old whole-run response lookup 0.244929 seconds / 39,992,130 bytes. The production
SQL trace confirms all three exact/range branches use the composite index. This
is one local measurement, not an end-to-end throughput or latency guarantee.

Remaining non-blocking CAD limitations include a pre-resource-limit fragment
inventory count, repeated warnings after the layer cap, and per-instance count
provenance growing with the input. The resource cap is explicit PARTIAL, not
silent SUCCESS; these limitations do not claim complete CAD semantic coverage.

Frozen remaining2 was stopped after the CAD parser test's obsolete Chinese
application-label substring failed. Counts, units, PENDING review and calculation
basis passed. Sol approved replacing only that assertion with explicit English
legend/reference and mandatory human-scope-review warnings; all other assertions
are retained. Its 824-file source identity was
`a40391448e1461596112f11715f96e78bfcba5920e3931f232c8117a571498e9`,
unchanged for the partial 492.640-second invocation. Exit 1 and the reproduced
failure remain archived; there is no completed pytest count. A replacement full
gate is required and must not borrow acceptance from either stopped run.
