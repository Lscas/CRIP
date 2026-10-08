# Versioned source-layout binding

Status on 2026-10-04: the selector/navigation foundation, named v9 model dispatch,
receipt3 authentication, result/failure persistence, follow-up proof and frozen
evaluation backend are implemented with offline synthetic tests. The v9 browser
question route is **not enabled**. HTTP creation defaults remain v8; no service
restart, customer database migration or live provider call is included.

## Implemented boundary

- `COMPLETE_SELECTOR_VERSION` keeps its historical v8 meaning. The low-level
  selector's historical default remains v7; existing question entry points
  explicitly continue to use v8.
- Explicit internal v9 selection preserves the existing complete-source scope,
  source order and original text. It carries both parser `extraction_method`
  and `text_map` into selected evidence. It does not attach an entire page's
  layout to the first evidence member.
- One geometry helper serves the legacy rendered view and the new source
  binding. Legacy callers still need no document, page or evidence identity;
  their rendered bytes and numeric-subclass compatibility remain unchanged.
- Binding first isolates document/page, then retains stable geometric order.
  Each observed token has a source ID and exact Unicode span; a model-facing
  part has only its text and the current round's `E` reference.
- `model_layout_navigation(rows)` rebuilds from the actual ordered rows on
  every call. Reordering changes aliases, dropping a row removes its parts,
  and duplicate or missing identities fail instead of being silently omitted.
- Missing or invalid maps, invalid pages and non-text-layer sources are
  explicit `UNBOUND` diagnostics. Valid geometry that simply does not qualify
  for the existing multi-column view is not mislabeled as a failed mapping.
  The original source text is retained in either case.
- Default binding/navigation generation has no artificial character cap.
  The optional byte limit exists for historical rendering compatibility.

Versions are `source-layout-binding-1` for the local derived object and
`source-layout-navigation-1` for the compact model-facing projection. The local
object retains source hashes, offsets and bounds; the model projection does
not expose these internal fields or stable evidence IDs.

## Authority and verification

Navigation is not a new source of answer authority. Matching numbers, nearby
tokens, shared lines or columns do not prove object identity, units, conditions,
relationships or arithmetic support. Original evidence and the existing claim,
numeric and calculation validators remain authoritative and unchanged.

`verify_layout_binding` compares the whole regenerated derived binding, not
only a supplied self-hash. Changed source text, source identity, projected
geometry or diagnostic contents invalidate a binding when they change that
derived object. This is not a claim that every unused original metadata field
is encoded: different non-text extraction methods or equivalently invalid maps
can yield the same diagnostic. Receipt3 authenticates the exact model-facing
navigation rather than every parser coordinate. Original source hashes,
navigation identity and ordered manifests are rebuilt for successful results,
settled contract failures and cached recovery, with the canonical named profile
and durable call commitment independently checked.

## Verified tests

- `tests/app/test_layout_binding.py`: all 57 original core cases now target
  the production module, including tampering with recomputed hashes, Unicode,
  byte boundaries, filtering, source isolation and full long-text retention.
- `tests/app/test_selector_binding.py`: 10 selector-to-binding/navigation
  cases, including multi-source repeated numbers, metadata handoff, stable
  v8 shape, changed aliases and legacy numeric subclasses excluding booleans.
- `tests/app/test_reference_v8_contract.py`: four pre-change frozen synthetic
  input contracts cover the legacy route and three named profiles, including
  full selection, system/user payload, request identity, receipts, persistence
  and a real MockTransport cache replay.
- Those 71 tests plus the existing 14 selector tests passed together: 85 tests,
  zero failures. JUnit: `reports/local/v9-selector-foundation-2026-10-04.xml`.
- All 30 tests in the existing complete-context and evidence-loop files also
  passed in two complementary invocations: 28 ordinary regressions and both
  large-input cases (310 and 8,500 rows). JUnit files are
  `reports/local/v9-selector-existing-loop-2026-10-04.xml` and
  `reports/local/v9-selector-large-input-2026-10-04.xml`; the first excludes
  exactly the two large-input cases, which the second executes.

The 1,760-test full gate in `reports/reference_case_ui_release_gate_2026-10-04.md`
preceded this selector change and does not cover it. A new full integration
gate is required before v9 is enabled. These tests do not prove model-quality
improvement or engineering correctness.

The subsequent backend increment passed 349 Reference/source regressions and
137 contract/devtools/Gateway-budget regressions. This includes 62 new v9 checks
and all four frozen v8 contracts. Exact scope and durable JUnit locations are in
`reports/reference_layout_v9_backend_validation_2026-10-04.md`; it is still not a
full-repository release gate or a customer runtime deployment.

## Named v9 backend integration

### Explicit live evaluation proof boundary

The controlled runner requires both the startup-enabled v9 capability and
`evaluation_execute_preview_proof=true`. It sends the complete preview proof
to item execute. Pending calls validate it during a single initial preparation,
before reservation/provider traffic. Terminal calls use `replay_only=true`,
recompute the proof locally and authenticate the saved result/failure. A pending
replay-only request is rejected without selection or dispatch. Existing no-body
and managed execution remain supported; a v9 proof on a v8 item is rejected.

The runner retains the v8 report shape and separates original saved-call counts
from calls newly made during recovery. Historical failure records hold only the
terminal receipt, so their whole-question call/supplement totals remain unknown
rather than zero or one. The schema26 increment adds optional complete safe
chains for new named-v9 failures without backfilling history; its validation is
separate from the frozen startup gate. Its final 325-test targeted regression and
independent boundary review passed. Its first 2,079-test frozen gate failed one
historical migration fixture; a test-only boundary repair passed 46 targeted
checks and Sol review. The replacement 779-file frozen gate then passed all 2,079
Python tests and auxiliary checks, without runtime or model-quality claims
(`reports/reference_v9_failure_chain_validation_2026-10-04.md`). The prior 1,912-test entry gate predates this explicit execute boundary;
see `reports/reference_v9_proof_runner_validation_2026-10-04.md` for its own tests.

The new v9 runner report is `reference-live-smoke-3`. It separates
`requested_supplement_rounds/requests` from `accepted_supplement_rounds/requests`.
An accepted supplement means the local retrieval was actually attempted, not
that useful evidence was found. `QA_V3_NO_NEW_EVIDENCE` therefore counts an
accepted retrieval without another model receipt; `QA_V3_ROUND_LIMIT` counts
the final proposed requests only as requested. V9's older `supplement_rounds`
and `supplement_requests` names are aliases for accepted counts. V8 keeps its
original requested-count meaning and report shape, so those aliases cannot be
pooled across versions. Missing historical counts remain absent/unknown.

Authenticated complete failure metadata is returned only as optional execute
top-level `failure_execution`, not inside the legacy failure object. Reports
project counts and byte totals, never the trace requests, source text, rejected
answer or private reasoning. Saved-execution cache flags do not substitute for
request-local `CURRENT_EXECUTE` telemetry; missing current telemetry stays unknown.

### Original named backend integration

- Both initial and supplemental input are complete-source; v9 rejects legacy
  per-evidence layout carriers and accepts only canonical named text profiles.
- Actual system/user input, layout navigation and selector policy enter request,
  task and cache identity. The initial manifest also retains selection identity,
  document/locator scope, source groups and conflicts, including UNBOUND rows.
- Receipt3 has a domain-separated ordered manifest, navigation version/hash/bytes,
  shared initial manifest and profile-neutral actual-input hash. It has no old
  per-E layout fields or image inputs. Mixed receipt2/receipt3 chains are rejected.
- Success, settled contract failure, replay and case follow-up authenticate the
  same source projection. Frozen evaluation/result selector versions must agree.
- Migration25 expands only the frozen evaluation selector allowlist. Temporary
  schema24 upgrade tests preserve old table values, columns, indexes and foreign
  keys, exercise backup restore, reject invalid selectors, and interrupt/roll back
  at drop, rename and marker writes. No customer database has been upgraded.
- Ordinary HTTP creation still uses v8. The internal store can create named v9
  evaluations, whose existing execution APIs, cloning, comparisons, scorecards,
  readiness and job recovery are covered by MockTransport tests.

## Immutable receipt3 prompt assets

Receipt3 binds the complete UTF-8 system message: existing `system.md`, the fixed
`\n\n---\n\n` separator, `layout-supplement.md`, `\nJSON Schema:\n`, and the
expanded complete-decision schema serialized with the existing compact JSON
serializer. The expansion helper is shared with Gateway; old v8 golden hashes
remain unchanged. The frozen receipt3 digest is
`5a28a492d2bf6553f8fb6518132f23809e4ae846ea328b464efdf20707df732e` (10,456 bytes).
The base prompt, supplement and complete schema (including referenced schemas)
are immutable for receipt3. A future change requires a new receipt/prompt version
and retention of the old reconstruction assets; do not replace this golden to
make an in-place change pass.

## Entry integration and remaining release work

1. Explicit named-v9 selection is now implemented for ordinary/Reference
   questions, evaluation creation/cloning and supplemental browser questions.
   Preview/Ask binds the same route, run/snapshot, question and selection before
   spending. Existing global/visual profiles are not silently converted.
2. The proof-runner frozen full gate completed with 1,978 Python tests and an
   unchanged 771-file source identity; see
   `reports/reference_v9_proof_full_gate_2026-10-04.md`. Runtime backup/restore
   safeguards and controlled migration are still required before enabling v9.
3. Only then perform the authorized real model comparisons with frozen matching
   inputs. Independent engineering adjudication, bounded Pro escalation and true
   phone/private-network acceptance remain separate unfinished requirements.

### Implemented entry contract (default disabled)

- Add optional `selector_version` (only explicit v9) and strict `preview_proof`
  to question input, and selector/profile to preview input. Missing fields retain
  v8 behavior. V9 Ask requires an explicit run and canonical named profile.
- A server-recomputed proof binds project/run/snapshot, normalized question hash,
  selector/policy, profile/version/canonical route hash, selection, initial source
  manifest and prompt contract hash. It is not a signature or a permission grant;
  no new database table, nonce or TTL is needed. Never include credentials.
- Extract one initial preparation function. Ask prepares once, compares the
  complete proof, then passes that same selection/rows/groups/conflicts into the
  existing loop, eliminating a check-then-reselect window. Any mismatch is 409
  before reservation or HTTP. Preview needs no live credentials; Ask checks the
  configured official channel inside the existing provider lock.
- UI defaults to existing v8 with three explicit named-v9 choices. Clear proof
  on question/project/run/profile/knowledge changes. Case state retains its own
  route/proof and existing case-version/attachment checks; the backend replaces
  the current second preview just before Ask.
- Evaluation creation accepts optional v9 with a named profile; defaults remain
  unchanged. Its frozen task already supplies execution identity, so managed
  jobs need no additional client-proof table. Verify all three entry points share
  the same initial manifest and every stale/mismatch/preview/GET makes zero calls.

The startup-only flag defaults to false. Standard local saved-profile/Mock
startup accepts `--reference-layout` / `--no-reference-layout` without reading
`.env` or changing the saved model configuration. With neither option it retains
its default-off behavior. The existing explicit `--live` path still loads
`CIRP_REFERENCE_LAYOUT_ENABLED`; either explicit CLI flag takes precedence.
Both options are rejected for remote preview before dependency setup or startup.
The effective gate survives an in-app model-settings restart but is never saved
in the model profile; a new standard startup without the option is off again.
These options are not authorization to migrate a database or send a model request.
The
public `capabilities.reference_layout_v9.enabled` field controls the UI; absent
or false means hidden v9 options. New v9 preview/ask/evaluation/clone/job entry
points and pending managed callbacks fail closed when disabled. Authenticated
saved result/failure replay remains readable without a new call. An enabled
Reference knowledge response also provides the terminal run's `snapshot_id`.

Ordinary and Reference requests reject delayed responses after project,
question, profile, run or snapshot changes, including the post-answer result
refresh. Profile controls are frozen while their operation is pending. Case
proofs instead bind their explicitly prepared run, preserving existing case
version and attachment checks. V9 case answering uses one preview; v8 retains
the previous second-preview check. V9 evaluation cloning requires an explicit
named target and never falls back to the global legacy profile.

Production-asset Chromium coverage is in
`scripts/reference_v9_browser_smoke.py`; durable synthetic results are stored
under `reports/local/`. This does not enable the actual customer runtime, prove
engineering answer quality, or substitute for the independent field/phone gate.
