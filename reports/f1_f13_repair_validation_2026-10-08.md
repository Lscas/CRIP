# F1-F13 repair validation

## Scope

This DEV-174 / CR-0190 repair starts from fixed application commit
`c5c51e36e03c4578912cc3e50e39e394a55e648b` in the isolated
`fix/f1-f13-20261008` worktree. The user's handoff is the acceptance source.
The separately described six portable probe files were not present, so this
change implements their documented triggers as repository regressions. A
probe that reproduces an old error is never counted as positive acceptance.

No real provider, customer document, credential, customer database, deployed
service, push, merge or release is used by this repair.

## Positive acceptance map

| Finding | Positive result | Repository evidence |
|---|---|---|
| F1 | A settled evidence-scoped verifier batch survives a synthetic report-save crash; restart maps `/batch/*` results back to original fields and the provider request count remains exactly one. Legacy per-record recovery remains the fallback when no current batch identity matches. | `test_batched_verification_recovers_settled_call_after_report_save_crash` |
| F2 | Building A cannot borrow Building B's value; feet cannot become meters; a conditional anchor count cannot become unconditional. Correct controls pass. The wrong/correct Building cases also run through the real `/questions-v2` HTTP route and settle as rejected/accepted respectively. | `test_claim_contract_binds_object_unit_and_condition_in_one_source_clause`; `test_questions_v2_endpoint_enforces_object_value_binding` |
| F3 | Quantity and design-property revision selection uses that field's own evidence IDs rather than an unrelated atom/title source. The result is 5 EA and 200 psi independent of unrelated source ordering. | `test_each_field_uses_its_own_cited_revision_not_an_unrelated_atom_source` |
| F4 | Human-accepted or edited material names remain verbatim in JSON and Excel, including `2 inch PVC valve`, `Single phase transformer` and `Two pole circuit breaker`. | `test_reviewed_export_preserves_dimension_phase_and_pole_in_complete_name` |
| F5 | `Manual transfer switch functional test` remains an executable inspection/test item while actual manual-document objects remain excluded. | `test_manual_transfer_switch_functional_test_is_not_treated_as_a_manual_document` plus existing document guards |
| F6 | An explicit `PERMANENT` classification overrides the temporary-name heuristic and retains the stated 2 EA quantity; the keyword conflict remains visibly review-required. | `test_explicit_permanent_kind_overrides_temporary_name_heuristic_without_losing_quantity` |
| F7 | Trusted-usage responses with `choices:[null]`, `message:null` or list-valued completion details settle once as a durable contract failure and replay locally. A response without trusted usage remains `UNKNOWN`, creates no business failure and sends no second request. | `test_v9_trusted_usage_malformed_envelope_is_terminal_and_replays`; `test_v9_missing_usage_stays_unresolved_and_does_not_publish_failure` |
| F8 | An A to B to A project switch increments a request generation, so the older A response cannot render or clear the current question; a subsequent current response works normally. | `question_project_boundary.test.mjs` |
| F9 | Fallback excerpts preserve the first character after LF, CRLF, period-space and semicolon-space boundaries, including leading `No`. | `test_fallback_excerpt_keeps_first_character_after_real_boundary` |
| F10 | Reference export reads result state, citations and review history inside one SQLite read snapshot; a concurrent accepted review cannot produce an old row with new history. | `test_reference_export_review_and_history_share_one_read_snapshot` |
| F11 | A failed or version-conflicted case update refreshes the server version while retaining the case-isolated assignee, note, resolution, supplemental question and attachment draft. | `reference_projection_cases.test.mjs` draft-conflict case |
| F12 | Supplemental question history reads `event.after.question` and inserts it through the existing text-only DOM path; markup-like content stays literal. | `reference_projection_cases.test.mjs` supplemental-history case |
| F13 | A loopback profile with an explicit key stores the key only through the credential backend and reloads it; a keyless loopback profile still works. Endpoint `remember:false` continues to clear the active saved profile. | `test_custom_loopback_profile_round_trips_an_explicit_saved_key`; existing keyless and endpoint tests |

## Validation status

- Changed-area Python and Node suites: PASS.
- `scripts/check_spec_sync.py`: PASS, 160 requirements and 37 schemas.
- `scripts/check_changes.py --base HEAD`: PASS for DEV-174 / CR-0190 declarations.
- Frozen full repository gate: PASS on the repaired source. The gate completed
  2,913 Python tests with no failure, error, skip or duplicate progress marker;
  24 localization tests and 11 deployment-boundary tests also passed. JavaScript
  syntax, the 160-requirement / 37-schema synchronization check, and the
  829-file bundle manifest check all passed.

The first full-gate attempt is retained as a failed attempt. All executable
behavior reached the end of the Python suite, but one old static UI assertion
still required the legacy mutable `state.project` URL instead of the new
captured `project` boundary. The manifest also correctly reported stale source
hashes. The assertion was updated to require the captured identity and request
generation; the manifest is rebuilt only after the final source is fixed.
The corrected final-source rerun then exited successfully. Subsequent edits to
this report, `docs/DEV_STATE.md` and the DEV-174 status are metadata-only; the
manifest and governance checks are rerun after those edits rather than
misrepresenting them as part of the earlier source freeze.

## Deliberate remaining boundaries

- Synthetic MockTransport responses validate lifecycle behavior but do not
  establish DeepSeek, V4.1 Pro or any other model's construction-answer quality.
- The original PDFs and described portable probe package were unavailable in
  this worktree. Original-PDF semantic/visual checks remain separate.
- The DOM tests exercise the production handlers with an offline surrogate;
  real Chromium acceptance remains separate.
- The credential round-trip uses an in-memory credential backend. Existing
  platform-specific Windows DPAPI coverage remains the real OS boundary.
- No deployment, customer migration or release decision is implied by a green
  repository gate.
