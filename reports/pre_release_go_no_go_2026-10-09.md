# CIRP v0.2.6 pre-release Go/No-Go review

Date: 2026-10-09 Hawaii time

## Current decision

**CONDITIONAL GO to freeze and resubmit the source candidate; NO-GO for an external/customer distributable.** The product owner explicitly removed answer-quality certification from this release decision and deferred actual DPAPI persistence plus customer-data migration/restore. Those are retained below as accepted limitations, not silently reported as passes. QA V2 dispatch and the current-user clean dependency installation are now verified. The remaining release work is to bind the current bytes to a commit/remote CI and, before external distribution, build and rehearse a clean package.

## Checks performed in this review

| Check | Result | Boundary |
|---|---|---|
| Specification synchronization | PASS | 160 requirements / 37 schemas |
| Change governance | PASS | Current modified paths are declared |
| Real Chrome offline DOM flow | PASS after refreshing stale English/current-UI assertions | Project, upload, mock analysis, human review, source view and 390 px mobile width; no network/model |
| Real Chrome v9 production assets | PASS | 19 checks, 7 synthetic provider decisions, isolated TestClient traffic |
| Real loopback HTTP | PASS after one product repair | Start, Host/Origin guards, upload, analysis, review, JSON/XLSX export, restart persistence; zero provider calls |
| Non-English project-name export | RED then PASS | `summary/project` is now preserved as user identity instead of being rejected as untranslated system text |
| Targeted export regression | PASS | 70 tests across API, export readability and review-export integrity |
| QA V2 PDF request envelope | RED then PASS | Same frozen 15: previous maximum 98,954 bytes; new maximum 52,541; 15/15 fit under 64,000 without reducing evidence |
| Real QA V2 provider dispatch | PASS for transport/settlement | DeepSeek Flash reached 15/15; 7 contract-valid and 8 safely rejected; zero size rejections and zero unresolved calls; no quality claim |
| Fresh Windows dependency install | RED then PASS | CP936 failed to decode the UTF-8 requirements comment; forced UTF-8 child mode fixed it, then clean `.venv`, dependency check and loopback startup smoke passed |
| Bundle manifest | PASS | 836 current source files; runtime, databases, credentials and ignored local live results excluded |
| Full repository gate | PASS on replacement run | 2,915 unique Python tests completed with exit 0; 24 language tests, 11 deployment tests, 160 requirements / 37 schemas and the 836-file manifest passed. An earlier current-source attempt failed only because DEV-176 marked paid calls generally allowed; that governance failure is retained and is not counted as a pass. |

## Release status after owner scoping

1. **Accepted limitation: answer quality is not certified.** The owner directed this item not to block the current release scope. The prior Pro score and the new Flash 7/15 contract-valid result remain visible; construction answers require human review, and no accuracy claim is made.
2. **Closed: QA V2 dispatch size.** File/page/path/bbox metadata stays local while each exact evidence text is sent once. The frozen 15 are 15/15 below the 64 KB bound and 15/15 reached the real provider.
3. **The candidate is not yet immutable at report time.** The branch has uncommitted source and test changes, no release commit/tag and no package checksum tied to the tested bytes.
   Public validation reports are ignored by `/reports/` while the bundle manifest includes them; the release commit must deliberately force-add those public reports or exclude them and rebuild the manifest, then verify a fresh checkout/archive.
4. **Current-source full gate and remote CI are not yet bound to a commit.** A historical green gate cannot approve later source changes.
5. **Closed for current-user source install; installer lifecycle remains open.** A new `.venv` installed and started successfully after the UTF-8 launcher repair. A separate installer repair/uninstall rehearsal is still part of packaging.
6. **Accepted deferral: actual Windows DPAPI persistence is unverified for this candidate.** In-memory credential regressions pass, but save/restart/load/forget was explicitly skipped.
7. **Accepted deferral: customer-data upgrade and rollback were not rehearsed on this exact candidate.** No customer directory was touched.
8. **Original construction-document acceptance is incomplete.** F1-F13 synthetic tests and the Kapolei benchmark do not replace original-PDF visual/semantic review, latest-revision selection across drawings/specs/RFIs/submittals, low-quality scans, rotated pages, schedules, legends and DWG/Xref cases.
9. **There is no final distributable.** Build the source ZIP/installer from the release commit, include version and SHA-256, verify clean extraction, startup, upgrade and rollback, and ensure `.local`, credentials, reports and private documents are excluded.

## Final gate order

1. Rebuild the bundle manifest and run the full repository gate on the current source.
2. Freeze and push one release-candidate commit, then require remote CI against that exact commit.
3. For an external/customer release, build and hash the distributable and perform a clean install/repair/uninstall rehearsal.
4. Retain the explicit human-review limitation; bring DPAPI, migration/restore and independent answer-quality adjudication back into scope before claiming those guarantees.
5. Before broad use on new construction documents, add low-quality scan, rotated-page, schedule, legend and DWG/Xref acceptance cases.

## Operational considerations before release

- Define an answer latency target and cost ceiling. Current Pro mean was 15.305 seconds and nearest-rank P95/max was 38.913 seconds; cache order strongly affected observed cost.
- Keep automatic retry disabled for unknown billing outcomes; verify interruption and reconciliation from the packaged build.
- Treat documents as untrusted input: retain prompt-injection, path, HTML/CSV formula and cross-project source isolation tests.
- Make version, provider/profile, run, snapshot, selector and source revision visible in support diagnostics without logging credentials or document text.
- Prepare a canary project, release notes, known limitations, backup/restore instructions and a rollback decision threshold before enabling real customer files.
