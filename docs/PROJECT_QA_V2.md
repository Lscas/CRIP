# Project QA V2

## Decision

Feature 8 is being replaced beside the existing project-question path. Parsing, immutable evidence, Canonical Storage V2, SQLite, the existing model gateway and deterministic workflow answers remain the foundation. The replacement boundary is:

`question -> effective source set -> evidence bundle -> optional region vision -> claim-bound answer -> validation`

The independent path now exposes both `POST /api/projects/{project_id}/questions-v2/preview` and `POST /api/projects/{project_id}/questions-v2`. Preview makes no model call. The answer route uses a separate concise prompt/schema and the existing audited gateway. Neither route changes `POST /api/projects/{project_id}/questions`, which remains the browser default until the release gates pass.

## Effective source policy

Documents are grouped only by a conservative normalized filename lineage. For one lineage:

1. Use a unique explicit internal issue date when every competing document has a valid date.
2. If the newest date is tied, or no document has a date, use a revision only when all relevant labels share a safely sortable numeric, single-letter or common-prefix numeric form.
3. A selected full document supersedes older full versions in the same lineage.
4. Addendum, ASI, RFI, PCD and Bulletin lineages remain overlays. A newer revision may supersede an older revision of that same overlay, but an overlay never silently replaces the base documents.
5. Missing, invalid, internally conflicting, incomparable or tied metadata keeps every competitor active and emits a conflict. Upload order is never a precedence rule.

This is intentionally conservative. A future explicit document-identity and supersession table can replace filename lineage without changing the evidence-bundle or answer contracts.

## Evidence bundle contract

The bundle reads only `is_source_text=1` Canonical nodes from the effective documents. It intersects exact typed identifiers found in the question, supplements them with bounded local FTS/LIKE retrieval, then expands a hit to its exact structural path. Table hits expand to the table and retain nearby rows within the block byte cap.

Each included citation retains its immutable evidence ID, document, file name, page, Canonical path, exact quote, extraction method and source bbox/coordinate system when available. Model-generated vision narration is never promoted to source text. Bundles are bounded to 42,000 UTF-8 bytes, with any one structural block bounded to 12,000 bytes.

The preview response reports `RETRIEVAL_READY` or `INSUFFICIENT`, the effective-source decisions and conflicts, whether region vision is recommended, and `model_called: false`. It is diagnostic evidence, not an answer and not a construction-accuracy claim.

## Claim-bound answer contract

QA V2 returns `ANSWERED`, `PARTIAL` or `INSUFFICIENT`. Its answer is exactly the ordered text of its atomic claims. Each text claim has one to four exact quotes from evidence IDs in the current bundle; a number cannot appear in a text-only claim unless it appears in that claim's quotes or is the result of a validated calculation.

Calculations are accepted only when the question explicitly requests addition, subtraction, multiplication, division or percentage arithmetic. Every operand must occur in cited text and the server recomputes the result with `Decimal`; `PERCENT_OF` requires exactly one cited base and one cited percentage. Unit conversion, rounding, date arithmetic, image arithmetic and geometry inference fail closed. A settled response is revalidated and recovered without another request; prompt, schema, model, inference settings, evidence, source conflicts and image hashes are part of the QA V2 task identity.

## Question-time region vision

Spatial/drawing wording or OCR-only evidence can enable a multimodal answer when the configured DeepSeek vision route is active. QA V2 selects one page already routed by the bundle, renders one bounded page overview and at most one padded crop around a compatible source bbox, and sends both in one audited answer request. It never sends a whole PDF or drawing set.

An `IMAGE_REGION` citation must reproduce the supplied region, document, page and bbox; its observation is always `needs_review: true`. It is not a source-text quote and cannot support arithmetic. If the provider, page or crop is unavailable, the answer must include `QUESTION_TIME_VISUAL_CONTEXT_UNAVAILABLE` and cannot claim full completion.

## Release gate

`scripts/benchmark_qa_v2_retrieval.py` compares legacy retrieval and QA V2 on the same immutable database. It writes only counts and locked answer-atom names and makes zero provider calls. Cutover requires at least 90% overall atom coverage, no critical question below 80%, no regression on the currently complete questions, at least 98% contract-valid replay responses, and at least 12/15 fully usable answers with no unsupported claim.

This task did not read the fixed customer benchmark databases under `.local`. Without an explicitly authorized staging copy outside that boundary, the release gate is not proven, the browser stays on the old endpoint and the old general retrieval remains in place.

## Remaining slices

1. Run the fixed no-call retrieval comparison on an explicitly authorized immutable staging database outside `.local`; tune only measured missed structural blocks.
2. Replay the resulting identical bundles through the approved model matrix. Escalate model/reasoning only for contract or synthesis failures after evidence coverage passes.
3. Switch the browser after the gates pass, preserve deterministic workflow-index answers, and delete the old general-retrieval branch and prompt after rollback verification.

No vector database, new parser, microservice, queue, full semantic knowledge graph or second verification model is required for the remaining slices.
