# Project questions — FR-QA-001

The reviewer may select a `PARTIAL` or `COMPLETED` analysis run and ask an English question about its files. CIRP first performs a bounded deterministic search over that run's immutable parser/OCR evidence, then sends only the top evidence windows and the question through the existing configured low-cost model gateway. The local SQLite FTS5 index covers source text, file name and readable Section/Sheet/Page/Paragraph locators. Exact RFI/Submittal identifiers and Email header/body terms therefore compete by relevance instead of database insertion order. Project and run scope are checked by the server; evidence from another project is never eligible.

Each submission is one explicit budgeted action. The existing CNY project limit, unresolved-call freeze, usage settlement, request pacing, non-reasoning policy, and no-automatic-retry rules remain in force. The currently configured live API or local model may answer from an older terminal run's immutable evidence even when that run was analyzed with Mock or another provider; the question task fingerprint includes the current provider, model, prompt and evidence snapshot. Repeating an identical question against the same snapshot and provider recovers its settled response without another provider request. Mock mode performs local retrieval only and states that no answer was generated.

The model may return `ANSWERED` only with at least one citation from the supplied evidence. Every published quotation must match the immutable source text exactly and uniquely; an invented quote, unknown evidence ID, truncated response, invalid JSON, or out-of-contract field fails closed after accounting and is never silently retried. Project evidence is treated as untrusted data, not as instructions. The page renders answer text without HTML and puts every source quotation directly below the answer with a link to the existing evidence viewer.

Existing databases are backfilled when the application first opens them, and new evidence is indexed in the same local transaction path. If the bundled SQLite runtime lacks FTS5, application startup and Q&A remain available through a complete LIKE scan; that compatibility path is slower but does not restore the old first-160-row cutoff. The index is local, adds no provider request, and is not a second hosted service.

This slice is independent-question Q&A, not a persistent conversation or a semantic vector index. Deterministic lexical ranking remains intentionally conservative; exact identifiers and project terminology work best. A grounded answer still requires human review and does not prove completeness, design correctness, or contractual authority.

## Offline acceptance evidence

- Specific construction terms rank the matching run-scoped evidence above unrelated passages.
- A precise RFI response after 160 earlier common-term passages remains the first result; Submittal identifiers, Email metadata and drawing Sheet locators are covered.
- A pre-index database is backfilled on open, and an environment without FTS5 uses the complete compatibility scan instead of silently losing later evidence.
- A 12,000-fragment synthetic production-path benchmark measured about 246 ms per complete compatibility query versus 44 ms with FTS5 (about 5.6x); results vary by computer and corpus.
- Mock mode publishes retrieval context without a model call or fabricated answer.
- MockTransport live-provider tests answer from a Mock-analyzed evidence snapshot, settle usage, recover an identical question without another request, and reject an invented quote after recording the terminal contract failure.
- A run belonging to another project is rejected before retrieval or model dispatch.
- Browser validation with a synthetic demo project confirmed question enablement after a terminal run, inline evidence, the existing evidence drawer, readable line location, and no console error.

No customer document, credential, live provider, or paid API was used for this validation.
