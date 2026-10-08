# Audit repair: fact preservation and independent review

## Scope and authorization

The external review examined main `d579486bade549f73f9996b812db6e360a0d6dc1`.
This repair starts from feature commit `ea69c99b6542adbe6bb4b7833932b7bdbc16b2d2`.
On 2026-10-07 the user approved the first repair batch plus provider-response and
setup-server boundary fixes. This is DEV-172 / CR-0188, not a main merge or a
deployment. All new examples are synthetic. No real model, private document,
customer database or credential is read or changed.

## Contracts being repaired

- Findings 1/2/3: only explicit quantity property names enter structured quantity.
  Model numbers and pole counts remain properties. A Tag alone does not establish
  a shared installation scope. Explicit scope must match before revision comparison.
  Missing dates, equal-date disagreements or a latest revision omitting quantity
  leave it unresolved, with the stated values and citations retained as properties.
- Finding 4, limited conversion repair: temporary material has null structured
  quantity, retaining stated quantities as attributes. General candidate-level
  publication failure isolation is **not** included or claimed.
- Finding 5: a system change while pending writes an atomic, system-authored
  `SYSTEM_BASELINE_UPDATED` checkpoint. Historical gaps recover the system value
  from the first following human event's before-envelope; an unprovable baseline
  cannot authorize overwriting a human decision. No new table or migration.
  When old unscoped MG records must split, only a unique baseline/evidence mapping
  can reuse their record identity. Split current records return to PENDING; old
  decisions remain in history, not copied into arbitrary entities. Ambiguous
  legacy mappings stop with 409 and roll back the publication transaction.
- Findings 6/8: selected source passages preserve their exact saved text, language,
  punctuation, newlines and identifiers. English application labels are not a
  license to rewrite quotations. Legal material/test names are not excluded by
  incidental words such as manual, owner, cost or product data. Accepted/edited
  records are not silently hidden by display filters.
- Finding 7: item review and quantity review are independent. A quantity-only
  operation preserves the item review and increments its optimistic-lock version.
  Ordinary exports include quantity review; reviewed-only exports retain accepted
  materials but leave quantity/unit/basis blank unless quantity is VERIFIED.
- Finding 12: malformed response envelopes after trustworthy usage are terminal
  `SETTLED_ERROR`, with safe diagnostics and no cached answer or automatic retry.
  Missing/untrustworthy usage stays UNKNOWN for reconciliation.
- Finding 13: the setup page requires a single exact bound loopback Host. GET may
  omit Origin, otherwise it must be exactly same-origin; POST requires it. This
  check precedes CSRF-page disclosure, form reading and credential/process actions.

Independent Sol preflight found and drove additional negative controls: floor or
system alone cannot prove a shared building; contradictory scope and mixed known/
unknown quantity aliases cannot be silently resolved; normalized names cannot
create two envelopes with the same logical key. The six paid-response paths were
checked for accesses before the common terminal boundary. Extract and field
verification additionally have real temporary-ledger lifecycle tests. Export
validation preserves source text (including visual observations and private-use
glyphs), while application labels, known enum fields and formula cells retain
separate checks. Excel evidence columns explicitly say Excerpt.

## Changed test expectations

The initial new export regression file produced 31 failures and one pass against
the baseline. Its failures included exact quote loss, legitimate records hidden,
quantity inference, missing quantity review and item review resetting that state.
Local red/green logs are under `reports/local/review-*`; they contain synthetic data.

Four old readability tests explicitly required lossy source cleaning or quantity
inference. They now require preservation/no inference; application-label cleaning,
formula protection and source validation tests remain. Positive revision/legacy
migration fixtures now state a common synthetic location instead of assuming Tags
are globally unique. Their identity/history assertions are retained; unknown-scope
negative cases remain separate.

## Validation status

The frozen audit2 seven-command gate completed with exit 0. Python reported
2,849 unique test nodes, zero failures/errors/skips/duplicates, and 1,715.120 seconds
(1,717.797 seconds including process overhead). Both JavaScript syntax checks,
24 language checks, 11 deployment-boundary checks, specification synchronization
(160 requirements / 37 schemas), and the 808-file manifest check passed. The
808-file source inventory was unchanged from start to finish. The 87 new focused
tests and independent Sol rerun overlap this full suite and must not be added to
its total. The quantity-button Node tests also run inside the Python suite.

Frozen audit2 source-snapshot SHA-256:
`f1b7e038867cef7e774be7bf99d6b265960b62bf93affca6c224c4bff073d41a`.
JUnit SHA-256:
`24eefd7ae526c37d90de7bf83b343810b058e1a6bd88bcf0dc1598364072eabe`.
Local receipts are `reports/local/ci-repair-gate-audit2-{source,result}.json`,
`reports/local/ci-repair-gate-audit2.xml` and its `.log` (not public artifacts).
Independent Sol review found no blocking P1 in the approved scope. A non-blocking
history-display tie-order issue remains: the history endpoint orders by timestamp
only, while baseline recovery also orders by event ID.

This result is recorded after the gate. Only this report, DEV_STATE,
IMPLEMENTATION_STATUS and DEV-172 status are updated after the frozen run, followed
by rebuilding BUNDLE_MANIFEST. Application and test files retain their tested
bytes. Post-gate governance and exact staged Git-archive checks are required before
publication. Only the existing feature branch is synchronized; the primary
worktree, its private dirty manifest, main and the running service remain unchanged.
Remote acceptance must use the resulting commit's own Actions run, not historical
CI or these Windows results. No engineering accuracy or field-device claim is made.

Frozen attempt audit1 (808 source files; frozen source-snapshot SHA-256
`f0e4b80f3429cabf5380d510023aad704c772b78dd4919373aa868a8ce2c5875`)
was stopped after a confirmed readability-test failure; it is not acceptance.
The exact expected product output passed, but the next assertion referenced the
removed private `validator.MACHINE_CODE` regex. That assertion now checks the
original property label directly, retaining the exact-output assertion. The new
validator tests separately reject untranslated application enums and formulas.
Sol accepted this test-only correction. The interrupted attempt retained its
log/source/result receipt with source_unchanged=true and exit_code=1; there is no
completed pytest count for that attempt. The replacement attempt reruns the full
seven-command gate from a new frozen identity.

## Explicitly remaining

Findings 9, 11, 14, 15 and 17 (verification context, failed-run UI resume, CAD text
completeness, MINSERT quantity and history-query amplification) remain outside this
batch. Finding 4 still needs general candidate-level error isolation. Findings 10
and 16 were already fixed in the feature baseline; main has not received them.
Complex option/parent/context mapping, actual engineering quality, and deployment
remain independent gates. Do not reinterpret this batch as resolving all 17 items.
