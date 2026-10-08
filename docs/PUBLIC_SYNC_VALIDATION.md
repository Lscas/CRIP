# Public source synchronization — 2026-10-07

Status: CI reproducibility repair passed the new frozen Windows gate before push.
Remote acceptance is the GitHub Actions result for the resulting commit.
This is not default-branch release, live deployment or engineering-quality approval.

## GitHub CI repair: pre-push validation — 2026-10-07

GitHub Actions run `37673736407` for commit `68e9eac` failed before this repair.
The two original profile tests depended on real Windows DPAPI and failed on
Ubuntu. The repair isolates only those tests' secret backend in memory,
retains every existing assertion, and retains the real-DPAPI Windows test.
It does not change application or production credential handling.

The same investigation found 505 of the 796 manifest entries differed from the
corresponding Git blobs only by line endings: 476 CRLF and 29 mixed; all other
bytes matched. The manifest was regenerated from a fresh LF checkout and
compared with the exact Git archive bytes. The earlier local Windows result of 2,762
passing Python tests and its manifest are historical working-tree evidence, not
a GitHub CI pass or a validation of the committed bytes.

The repaired targeted profile/credential checks pass 23 tests locally, including
the real Windows DPAPI test with no skip. These overlap the new complete gate;
they are not added to its test count.

The new frozen Windows gate `ci-repair-gate-r1` completed all seven commands
from `scripts.run_checks.build_commands('all', ...)` with exit 0:

- Python: 2,762 unique tests; zero failures, errors, skips or duplicate nodes;
  JUnit duration 1,646.072 seconds.
- JavaScript: 24 language tests, 11 deployment-entry tests, both syntax checks.
- Specifications: 160 requirements and 37 schemas; source manifest: 797 files.
- All 797 source entries remained unchanged during the gate.
- Exact Git archive verification of the staged source found 797 manifest
  entries and 798 files including the manifest, with no missing, extra or
  mismatched entries. The same verifier rejected the old commit's 505 mismatches.

The frozen source-manifest SHA-256 is
`b958e2f1234cefe40be39d56486a054f2873e3edad9dbd20bca86079772a9fce`;
JUnit SHA-256 is
`13c61313177894e7ca5db06f1dca108531a4676b2781c4c35e300b9dc98694c7`.
Source snapshot, complete log, JUnit and result are retained locally under
`reports/local/ci-repair-gate-r1*`; they are not uploaded with public source.

Post-gate edits only record these results and correct the fixture description in
CHANGELOG, CR-0187, DEV_STATE, IMPLEMENTATION_STATUS and this document; they do
not inherit the earlier frozen manifest identity. The final manifest is rebuilt,
the governance checks are repeated, and the final staged/committed Git archive
must match before synchronization. No application or test source changes after
the frozen gate are authorized by this metadata note.

This record is written before push. It does not claim a new remote CI pass;
check the resulting commit's Actions run for that result. No default-branch
merge, deployment, customer-data action or model-provider call is claimed.

## Scope

Synchronize the accumulated application, contracts, migrations, prompts, tests
and reviewed development documentation to the existing feature branch. Keep
projection question creation disabled. This does not merge to the default branch,
restart a service, migrate customer data or modify human review history.

New private reports, independent field questions, source attachments, PDFs,
runtime databases and temporary outputs are excluded from the publication tree.
Previously tracked public artifacts and Git history are retained unchanged; this
is not a historical data-removal operation.

## Reproducibility repairs

- Public validator tests use an independently authored 20-item synthetic fixture.
  All distribution, approval, source-channel and byte/hash negative assertions
  remain. The original validator is unchanged. Private candidates remain local;
  their separate structural check is not model or factual evaluation.
- Source manifests exclude the `.git` pointer file used by linked worktrees.
- Text checkout line endings are LF so source and fixture commitments are stable
  across Windows and public CI. Binary files remain byte-preserved.
- Unpublished historical verification prose stays in change-record decision
  notes; executable test declarations refer to available public test sources.

## Verification boundaries

The previous 703-test targeted gate does not establish this publication tree's
full-suite status. The final isolated full attempt (r3) completed with exit 0:

- 2,762 unique Python tests; zero failures, errors, skips or duplicate nodes;
  elapsed pytest time 1,687.906 seconds.
- All seven commands from `scripts.run_checks.build_commands('all', node)`
  completed successfully: Python tests, specification synchronization, manifest,
  both JavaScript syntax checks, 24 language tests and 11 deployment-entry tests.
- 160 requirements and 37 schemas passed structural checks. The 796-file source
  manifest was unchanged throughout the full run.
- Frozen manifest SHA-256:
  `4433f9440cd5a973e67475ba981b639bad2ce53f07bfde33e77c12ac1d476a29`.
  JUnit SHA-256:
  `215c4abb0f188bfc211d4a8d8d60fb6081f4d87c154202f2dffba2ef110becbc`.

The final Chromium r5 harness ran against the same public product source in three
fresh temporary databases/browser contexts. Each run passed the seven grouped
checks: saved terminal-case creation, authenticated source viewing, both mixed
legacy/projection follow-up directions, missing-original close/reopen, the
English-only compatibility boundary and 390/320px layout. The observed API counts
were 65, 65 and 64, not fixed expected totals. Each run had seven MockTransport
handler calls and seven unique settled receipts, with zero external requests.
Machine/citation/review/ledger snapshots were checked around human workflow writes.

The earlier r2 report's "bilingual switch" claim was incorrect. D-25 already makes
the runtime English-only; retained bilingual catalogs and hidden compatibility
selectors do not enable Chinese UI. Its fixed-delay network window also mixed in
normal background polling. r5 pauses only background interval callbacks, drains
in-flight requests, verifies rejection of `zh-CN` and a programmatic hidden `en`
compatibility event, and requires zero route/fetch/XHR requests in that isolated
window. It restores intervals and observes a subsequent settings poll in every
run. This is not a visible language-switch interaction. The probe compares the
display-preference key, draft and current case state, not all browser storage or
an additional full database snapshot; the Node contracts separately constrain
preference writes. Prior reports and diagnostic failures remain unchanged locally.

Final browser harness SHA-256:
`e97b3fa23255472ff6060f57b8eba3c77a869158ac3cb95f9a139a64d094f430`.
Full logs, JUnit, source snapshots and browser reports are retained locally rather
than added to the public source commit. This document, the development/status
summaries, CHANGELOG and CR-0186 are post-gate metadata; the publication manifest
is regenerated afterward and their governance checks are repeated. They do not
silently replace the archived frozen identity above. Application, contracts,
migrations, prompts, scripts and tests must remain byte-identical to that gate.

Synthetic browser tests are not real-device or construction-answer validation.

The first isolated full attempt finished with 2,753 unique Python tests, four
failures and no errors/skips, in 1,613.601 seconds. Its 796-file source was
unchanged. One failure exposed a real legacy evaluation-to-case query that did
not select the result hash required by the new public case view. Two failures
correctly rejected enabling paid calls in the default offline task declaration;
user live-call authorization is separate from that default. The fourth rejected
five verification pointers to unpublished reports. The original failed log,
JUnit and source identity are retained locally; none is called a passing gate.

The second attempt was intentionally stopped before completion after staged
change-declaration preflight exposed an unrelated mixed-record checker defect.
Its runner exited 1, with the frozen source unchanged; partial progress is not a
completed test count or a pass. Its log, source identity and result are retained
separately. The checker must judge a `spec_only` record against the changed paths
that record actually covers, while still rejecting product paths claimed by any
such record and retaining complete union-of-records path coverage. The historical
spec-only record keeps its original classification. A separate stale requirement
reference is corrected without inventing a new requirement or approval.

The actual pre-repair checker was also loaded against the new valid mixed-record
test and failed its assertion; the corrected checker and expanded negative tests
are included in the passing r3 full gate. No historical spec-only record was
relabelled as product implementation to make the gate pass.

Public traceability now points to the corresponding checked-in feature documents
for evidence-loop and reference routing/readiness/versioning. The original local
comparison reports remain unchanged and unpublished; moving a pointer does not
revalidate historical model scores or make them public acceptance evidence.

The user authorized API calls. Both previously used loopback service ports were
unreachable during this preparation, so no provider dispatch is made merely to
exercise that permission. A future scoped live run requires a healthy configured
service and settled prior calls; it must not bypass the disabled projection entry.

The six DEV-171 goals remain unfinished. Real-phone/customer-runtime acceptance,
independent engineering-answer evaluation, pre-result failure help, capacity
preflight and separately gated projection question creation remain open. Automatic
model escalation is still deferred. GitHub-hosted CI is a separate post-push check;
this report records local public-tree validation, not a remote CI success.
