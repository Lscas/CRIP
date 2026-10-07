# Reference Cases API

`ReferenceCaseStore` is the local, single-user human follow-up service for a
terminal Reference QA outcome. It is deliberately separate from immutable
`reference_results`: creating, viewing, assigning, resolving, or reopening a
case never changes a model result, its citations, or an evaluation item.

## Construction

```python
from app.reference_cases import ReferenceCaseStore

cases = ReferenceCaseStore(db, uploads)
```

The database initializer installs `migrations/022_reference_cases.sql`, the
additive `migrations/023_reference_case_followups.sql`, and guarded schema28's
status-constraint widening after schema27. No additional table or migration is
needed for the versioned case consumer. `uploads` is optional for source-free
case work, but required to authenticate a new projection follow-up link.
The service makes no model or network calls.

## Create

```python
cases.create(
    project_id, run_id, question,
    result_id=None,
    evaluation_id=None,
    question_id=None,
    actor="local-user",
    attachments=(),
    supplemental_question=None,
    note=None,
)
```

`result_id` may reference a legacy `ANSWERED`, `CANNOT_ANSWER`, or
`NEED_USER_INPUT` result, or a saved `PROJECTION_LOOP_OUTCOME` with status
`REVIEW_REQUIRED`, `CANNOT_ANSWER`, or `NEED_USER_INPUT`, from the same project,
run, snapshot, and normalized question. A projection result starts a human case
in `OPEN`; it does not become an answered or approved machine result.
`evaluation_id` and `question_id` are supplied together and remain legacy-only. They must
identify the same project/run/snapshot/question evaluation item. A terminal
failed item produces source status `FAILED` and cannot be represented by a
made-up result ID. A linked result and evaluation item must agree.

The source identity is idempotent: project, run, snapshot, normalized question,
and the result ID (or, for failures, evaluation item ID). Repeating the same
source create returns the original case. Projection source identities additionally
bind the frozen result hash in a separate `reference-case-projection-source-1`
domain; legacy source-key bytes are unchanged. Every read or write checks the
source key, so changing the result kind cannot downgrade this binding.
A replay containing a note,
supplemental question, or attachments returns HTTP-domain conflict `409`; callers
must use `update` so the input is not silently lost.

Attachments are existing `documents` from the same project. They are recorded
as links only; this service does not upload files.

## Read

```python
cases.get(case_id)
cases.list(project_id, status=None, assignee=None, run_id=None, limit=50, offset=0)
```

Both calls are read-only. `get` returns full append-only history. `list` returns
the same public case shape without history, with `items`, `total`, `limit`, and
`offset`. `latest_note` is the most recent non-empty human event note, provided
for a compact pending-work view; it is not a new workflow status.

Case creation and daily handling use source-free saved-result identity checks in
the same database snapshot as the case, attachments and history. A missing PDF
does not prevent assignment, notes, attachment registration, human resolution or
reopening. This is not source authentication: original-source viewing and a new
projection follow-up link still require readable, fully authenticated source files.

Each case has this shape (timestamps omitted here):

```json
{
  "case_view_version": "reference-case-view-2",
  "case_id": "QACASE-...",
  "project_id": "P-...",
  "run_id": "RUN-...",
  "snapshot_id": "SN-...",
  "question": "...",
  "source": {"status": "NEED_USER_INPUT", "result_id": "QAR-...", "evaluation_id": null, "item_id": null, "result_kind": "PROJECTION_LOOP_OUTCOME", "result_hash": "..."},
  "status": "OPEN",
  "assignee": "",
  "resolution": "",
  "latest_note": "Waiting for field confirmation.",
  "version": 0,
  "attachments": [{"document_id": "DOC-...", "name": "field-photo.pdf"}],
  "history": []
}
```

`result_kind` and `result_hash` are derived by the service, never supplied by the
client. Both are `null` for a terminal failed evaluation item without a result.

## Update

```python
cases.update(
    case_id, expected_version,
    status=None,
    assignee=None,
    note=None,
    resolution=None,
    actor="local-user",
    attachments=(),
    supplemental_question=None,
)
```

Allowed statuses are `OPEN`, `NEEDS_INFORMATION`, `IN_REVIEW`, and `RESOLVED`.
Resolving requires a non-empty human `resolution`; it is stored only in the case
and its history, not in AI output. Any change consumes `expected_version` and
appends an audit event. A stale version returns `409`. Reopening retains the old
resolution in the event `before` history but clears the current case resolution.
Resolving that reopened case therefore requires an explicitly supplied new
resolution. Supplemental questions, notes, and attachment links are append-only
events. The API accepts an actor supplied by its caller; it does not decide
permissions or approvals.

## Saved follow-up result link

```python
cases.link_followup(
    case_id, expected_version, run_id, result_id,
    supplemental_document_ids, note, actor="local-user",
)
```

This is a local link only: it never creates a run, sends a model request, or
rewrites the original source/result. `run_id` must be a different completed or
partial `REFERENCE_QA` run in the same project. Its immutable saved result must
have the same normalized case question and its own run snapshot. The supplied
documents must already be case attachments and be included in that new run.
Legacy and projection case/result kinds may be mixed; neither combination opens
the projection question-creation or evaluation path.

Every supplied document needs receipt proof that it was actually sent to the
saved result's settled model call: either an evidence input belonging to that
run/document or a visual input whose saved visual region belongs to the
document. For v8 multi-round results, `sent_visual_regions` is the de-duplicated
all-round audit set; `visual_regions` remains the final-round-only set used for
answer citation authority. Missing, disabled, malformed, cross-project, or
unsettled receipts are rejected. Each immutable link preserves document hash,
first input round, text/visual input counts, and result citation count. A
citation alone is not proof that the document was sent.

Before accepting a link, CIRP reauthenticates the saved result against the same
Reference-result receipt validator used on save: exact stored JSON hash,
project/run/snapshot/question/status, receipt schema and unique calls/rounds,
provider/model/question and request identities, settled-call ownership, and
source-text or visual-image hashes. Complete-source v8 receipts additionally
require their non-secret `execution_profile` and recompute their ordered
evidence manifest and source byte total. A profile-less receipt-v2/v8 shape
therefore cannot newly authenticate or create a follow-up link; historical
v3-v7 results retain their existing legacy authentication behavior. A run and
its result must be newer than the case and the selected attachment links, so an
old answer cannot be retrospectively presented as a response to a later
supplement.

Projection links use full source authentication and the separately versioned
`reference-case-projection-followup-proof-1`. Each selected attachment must have
at least one non-empty projected text row in an authenticated receipt round.
Presence in the snapshot or in an empty page manifest is not proof of model
input. The proof binds case/run/snapshot/question/result identity and stores
receipt source references, row descriptors and hashes, not original row text.
It counts actual input rounds; visual-input and citation counts remain zero.

Full source authentication happens outside the write lock. Before the atomic
link/case-version/event write, the service rechecks the case, result, run,
attachment and complete-snapshot identities, not just the case version. A
concurrent change produces a conflict without a partial link.

After linking, source-free reads validate the canonical proof and hash, frozen
result/receipt identity, internal descriptor/count consistency, and a matching
append-only human event containing the follow-up ID and proof hash. Receipt6
does not independently encode row text: a read without the PDF cannot recreate
or reauthenticate the row descriptors. The proof is a historical record of
authentication **at link time**, not a claim that current source files are still
available or that the input supports an engineering answer. The event binding
also does not claim protection against coordinated rewriting of the entire DB.

The same case/result and identical payload is idempotent; a changed replay is
`409`. A stale link is also `409`, and retrying the saved link causes no model
call. A resolved case must be reopened first. An `ANSWERED`, `CANNOT_ANSWER` or `REVIEW_REQUIRED`
link moves the case to `IN_REVIEW`; `NEED_USER_INPUT` moves it to
`NEEDS_INFORMATION`. No linked answer resolves or approves the human case.

The public case shape adds a `followups` array. Each record has `followup_id`,
`run_id`, `snapshot_id`, `result_id`, `result_status`, derived `result_kind` and
`result_hash`, `created_at`, and
`proof.documents`; the latter keeps the distinct linked, sent-to-model, and
citation evidence fields.

The HTTP endpoint is:

```
POST /api/reference-cases/{case_id}/follow-up-results
{"expected_version": 3, "run_id": "RUN-...", "result_id": "QAR-...",
 "supplemental_document_ids": ["DOC-..."], "note": "Checked revised photo."}
```

The local HTTP API fixes the actor to `local-user`; callers cannot self-report
an actor. All case GET routes remain read-only.

## Browser staged supplement flow

The Reference workspace provides one explicit local sequence for a non-resolved
case: upload same-project attachments, prepare a different `REFERENCE_QA` run,
preview the selected pages, explicitly answer with that run, then link its saved
result. Upload and preview do not call a model; only the explicit answer action
can do so. A saved answer never resolves or approves the case.
This staged Answer action still uses the existing `questions-v3` v8/v9 Reference
QA path and saves a legacy result, including when the case originated from a
projection outcome. It is not a projection question-creation entry.

The browser keeps the current case/project scope while asynchronous work is in
flight, rejects stale or duplicate attachment writes, and preserves a saved
answer for read-only recovery if a result-detail, link, list, or refresh response
is interrupted. It does not retry the model request to recover a link. These UI
guards do not change the case API, immutable result, human review, project-input
selection, or database semantics. Production-asset browser evidence is recorded
in `reports/field_case_followup_browser_validation_2026-10-04.md`; it uses mock
transport and is not a live model-quality or phone-network acceptance claim.
## Projection source-review integration checkpoint (2026-10-07)

Current formal-promotion checks and retained regressions: `../reports/reference_projection_human_review_foundations_2026-10-07.md`. This is a new source identity, separate from the previous persistence full gate.

Saved `reference-projection-execution-2` / `REVIEW_REQUIRED` results now have
read-only original-source endpoints:

- `GET /api/reference-results/{result_id}/projection-review`
- `GET /api/reference-results/{result_id}/projection-review/sources/{ordinal}/image`

Every request independently authenticates the saved row, full source projection,
receipt and settled ledger chain. Only then does the store derive complete selected
text and server-owned page/CropBox coordinates. The image path selects its source
by ordinal, reads the server-selected object into immutable bytes, checks its PDF
hash and renders those same bytes. Neither route accepts client source, path or
geometry parameters, writes human state, or calls a model. Original-source failure
is unavailable/409, never a trusted cached quote. Non-review/V1 results do not gain
this view. Both responses use `Cache-Control: no-store`.

The browser uses a separately advertised `reference_projection_review` capability
for source viewing. The versioned case integration additionally uses
`case_workflow_available`, `case_view_version: reference-case-view-2`, and
`saved_followup_link_available`; `question_creation_available` remains false.
Exact original text is not translated
or clipped. Showing a source is not verification of object/condition applicability,
engineering correctness or answer completeness. Image display requires an explicit
click and does not reuse the generic unbound page-image route.

The existing case consumer now separates source-free daily handling from full
source authentication for original-source viewing and new attachment-input
proofs. Source errors leave human task handling available. Projection original
views bind the selected case version, project and immutable result hash; a stale
response must not render original text or images into another case. This code
does not imply a customer migration, service restart, real model-quality gate or
completion of the full field-QA goal.
