# Project questions — FR-QA-001

The reviewer may select a `PARTIAL` or `COMPLETED` analysis run and ask an English question about its files. CIRP first performs a bounded deterministic search over that run's immutable parser/OCR evidence, then sends only the top evidence windows and the question through the existing configured low-cost model gateway. Project and run scope are checked by the server; evidence from another project is never eligible.

Each submission is one explicit budgeted action. The existing CNY project limit, unresolved-call freeze, usage settlement, request pacing, non-reasoning policy, and no-automatic-retry rules remain in force. The currently configured live API or local model may answer from an older terminal run's immutable evidence even when that run was analyzed with Mock or another provider; the question task fingerprint includes the current provider, model, prompt and evidence snapshot. Repeating an identical question against the same snapshot and provider recovers its settled response without another provider request. Mock mode performs local retrieval only and states that no answer was generated.

The model may return `ANSWERED` only with at least one citation from the supplied evidence. Every published quotation must match the immutable source text exactly and uniquely; an invented quote, unknown evidence ID, truncated response, invalid JSON, or out-of-contract field fails closed after accounting and is never silently retried. Project evidence is treated as untrusted data, not as instructions. The page renders answer text without HTML and puts every source quotation directly below the answer with a link to the existing evidence viewer.

This first slice is independent-question Q&A, not a persistent conversation or a complete semantic index. Deterministic lexical prefiltering is intentionally used before adding a vector database; exact identifiers and project terminology work best. A grounded answer still requires human review and does not prove completeness, design correctness, or contractual authority.

## Offline acceptance evidence

- Specific construction terms rank the matching run-scoped evidence above unrelated passages.
- Mock mode publishes retrieval context without a model call or fabricated answer.
- MockTransport live-provider tests answer from a Mock-analyzed evidence snapshot, settle usage, recover an identical question without another request, and reject an invented quote after recording the terminal contract failure.
- A run belonging to another project is rejected before retrieval or model dispatch.
- Browser validation with a synthetic demo project confirmed question enablement after a terminal run, inline evidence, the existing evidence drawer, readable line location, and no console error.

No customer document, credential, live provider, or paid API was used for this validation.
