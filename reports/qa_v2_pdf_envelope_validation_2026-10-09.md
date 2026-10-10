# QA V2 PDF request-envelope and clean-install validation

Date: 2026-10-09 Hawaii time

## Result

The QA V2 64 KB dispatch blocker is closed without reducing the frozen PDF evidence bundle. The model request now carries each immutable evidence ID and its exact text once. Repeated file name, page, Canonical path, bbox and coordinate-system data remain in local evidence rows for validation and citation enrichment.

This result does not claim that model answer quality improved. The product owner explicitly deferred that gate for this release scope, so every published construction answer still requires human review.

## Same-input byte test

- Database: the existing immutable Kapolei database, opened read-only for preflight.
- Run: `RUN-ae67a407392f40bbaa61593f08e0d28e`.
- Questions: the same frozen S1-S5, D1-D5 and C1-C5 set.
- Evidence selection: unchanged; 31,650-42,000 source-text bytes and the same local evidence identities.
- Previous serialized request maximum: 98,954 bytes.
- New serialized request range: 41,681-52,541 bytes.
- Result: 15/15 at or below the configured 64,000-byte limit; zero provider calls during preflight.
- Preview now exposes only byte counts and fit status, never prompt content or another source-text copy.

## Real provider test

The same frozen run was copied into a disposable data directory and submitted once per question to the saved official DeepSeek Flash profile. Automatic retry was disabled.

| Measure | Result |
|---|---:|
| Questions reaching the provider | 15/15 |
| Rejected for local input size | 0 |
| Answers passing the complete local claim/evidence contract | 7 |
| Answers safely rejected by that unchanged contract | 8 |
| Unresolved calls at completion | 0 |
| Prompt tokens reported | 234,689 |
| Completion tokens reported | 8,883 |

Every call ended as `SETTLED` or `SETTLED_ERROR`; none remained in an unknown billing state. The eight 400 responses are evidence-contract failures after a provider response, not transport or size failures. No independent answer-key adjudication was performed in this increment.

The original database retained its prior 610,615,296-byte length and modification identity. After settlement checks, the 614,404,096-byte disposable database, its three copied encrypted credential/profile files and the empty object directories were deleted. Only the ignored local result summary was retained; no credential, database or document is included in the source bundle.

## Regression coverage

- A synthetic request whose former repeated-metadata form exceeds 64,000 bytes now reaches the mocked HTTP transport below the cap.
- The provider payload contains only `evidence_id` and exact `text` for text evidence.
- Preview reports the exact current request-envelope version and byte counts without calling a model.
- The task fingerprint is upgraded to `qa-v2-model-input-2`, so an older settled answer cannot be recovered under the changed provider input.
- Exact-quote, numeric, object/unit/condition, image-region, durable settlement and no-retry validators remain unchanged.

## Clean Windows install

The first clean `.venv` attempt exposed a real launcher defect: Windows pip decoded the UTF-8 `requirements-app.txt` with CP936 and failed on its Chinese comment before installing dependencies. The dependency child environment now sets `PYTHONUTF8=1` while continuing to strip credentials.

The same fresh virtual environment then completed installation successfully. `scripts/local_deploy.py --check` reported dependencies ready, and the newly installed interpreter passed the real loopback upload/analyze/review/JSON/XLSX/restart-persistence smoke with zero paid calls.

The Windows replacement gate passed 2,915 unique Python tests, 24 language tests, 11 deployment-boundary tests, 160 requirements, 37 schemas and the 836-file source manifest. One earlier full run is retained as failed: all application tests reached completion, but `DEV-176` incorrectly marked paid calls as generally allowed. The task flag was restored to the repository-required `false`; the user-specific live-call authorization remains in the approval record and validation report. The exact governance red test passed before the successful replacement full run. A later GitHub LF-checkout failure was reproduced from the committed archive as one `docs/REQUIREMENTS.md` hash mismatch; the generator now writes LF explicitly and adds one regression, bringing the current suite to 2,916 unique Python nodes. A fresh Git archive passed its manifest and 37 governance/devtool checks, and GitHub's complete Ubuntu gate passed commit `a32d2d3d1b68682e087ae28d3c07fe05aac595ab`.

## Boundary

Closed here: QA V2 PDF request size, real provider dispatch, unknown-call check, and clean current-user dependency installation/startup.

Not closed here: answer-quality target, browser cutover, actual DPAPI save/restart/load/forget, customer-data migration/restore, signed packaging, remote CI, deployment or an installer uninstall rehearsal.
