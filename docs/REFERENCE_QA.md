# Reference QA preparation and transparent page selection

Reference QA is a new opt-in analysis mode beside the existing full analysis pipeline. It supports the target architecture in which CIRP manages local files, snapshots, runs, results, citations and review while a configured advanced model performs the actual engineering-file reasoning. The default `LEGACY_ANALYSIS` path remains unchanged.

DeepSeek Reference profiles use the exact current official IDs: `deepseek-flash` for V4.1 Flash and `deepseek-v4-pro` for the independent V4 Pro text model. Flash may explicitly receive selected-page images; Pro is text-only and image input fails before dispatch. Historical Flash aliases remain readable, but a newly selected profile freezes the canonical ID. The same-snapshot comparison contract requires identical run, snapshot, ordered question set and selector version; switching models alone cannot alter evidence scope or establish correctness.

## Local preparation boundary

Create a run with `analysis_mode=REFERENCE_QA` to execute only the existing local file parser and local OCR path. After immutable parser evidence is saved, the run ends in `PARTIAL` with stage `Local page catalog ready`.

This mode intentionally skips:

- visual-model page processing;
- Canonical graph construction;
- full-corpus model extraction;
- material, inspection, conflict and missing-record assembly;
- semantic field verification.

It therefore does not reconstruct a second engineering-semantic database before the question-time model. It also performs no model or provider request during preparation.

`LEGACY_ANALYSIS` remains the API default. The original Analysis Run selector, records, exports and **Ask the project** continue to use only legacy runs. Reference QA is exposed in its own browser section and cannot silently redirect those older paths.

## Explicit answer-part coverage

QA V3 derives a small answer checklist from only explicit comma lists and parallel question clauses in the submitted question. The provider receives the original question plus `P1..Pn` text fragments. An `ANSWER` must map every part to at least one returned claim or calculation, and every returned claim/calculation must be mapped; missing, duplicate, empty or out-of-range mappings fail locally. Questions that cannot be split conservatively remain one whole `P1`.

This is a structural omission guard, not semantic grading. It does not use an answer key, infer how many source requirements may be nested inside one part, or prove that a mapped claim correctly and completely states the cited engineering requirement. The fixed 15-question zero-call fixture yields 41 parts, including D2/D3/D4 at 3/3/2. Live Flash quality remains a separate frozen benchmark gate.

## Transparent page selection

`POST /api/projects/{project_id}/page-selections/preview` accepts the same run ID and question fields as project questions. It reads only immutable, parser-produced source evidence from that run and returns the exact selection audit without calling a model.

The current selector for new work is `literal-page-selector-7`. It retains v6's four-page/48 KB limits, literal page ranking, explicit Building scope, literal row focus and exact-token layout context. Explicit Sheet, Drawing, Section, Paragraph, RFI and Submittal labels retain both their complete labelled phrase and bare identifier; an exact bare identifier in a parser locator outranks ordinary text mentions. When—and only when—the original question explicitly names `Building` or `BLDG` plus a compact identifier, v5 through v7 rank pages naming that same Building first and exclude pages or evidence rows that explicitly name only another Building. Supplemental model searches inherit that original target even if their shorter query omits it. Unlabelled general evidence remains eligible. The public preview reports the target and excluded page/row counts. Nonnumeric phrases such as `sheet counts` are not promoted to identifiers, ASCII matches respect token boundaries and each ordinary term contributes at most twice per page. The narrow drawing-cover rule is unchanged. The selector uses no embedding, vector database, Canonical node, extracted requirement, model response or reviewer decision.

V6 adds one deliberately smaller rule after pages have already been selected: a bounded allowlist of literal word forms may affect evidence-row order. It currently covers the demonstrated `thickness`/`thick` mismatch, components of a hyphenated question token such as `door-handle`, and bounded singular forms for selected plurals. V7 retains this rule. These forms never change page scores, page ranks or the audit's matched terms. They are not stemming, synonym expansion or semantic search.

The audit includes:

- a stable selection ID and saved snapshot ID;
- normalized literal query terms;
- candidate and exclusion counts;
- selected file/page identities and scores;
- matched terms and fixed reason codes;
- immutable source-evidence IDs;
- explicit issue-date and revision labels found in source evidence;
- original and selected UTF-8 byte counts;
- a short exact-source preview.

Model-vision narration is excluded because it is not source text. Conflicting explicit version metadata is retained as a visible conflict rather than silently resolved through upload order or inferred authority.

The v4 default selection is limited to four pages and 48,000 UTF-8 source bytes. Oversized fragments are clipped only at an exact source prefix, with no synthetic ellipsis that could be mistaken for a quotation.

Every Reference evaluation freezes its selector version. Migration 016 backfilled historical tasks to six-page v3; migrations 018 through 020 expand the allowlist without rewriting rows. Saved v3 through v6 evaluations retain their original behavior, while new tasks use v7. Preview, initial and supplemental evidence selection, readiness, managed jobs and scorecards all follow the saved version. Cloning preserves it, and A/B comparison rejects different versions. This prevents an upgrade from silently changing a frozen benchmark's input. Single-question Reference QA outside an evaluation uses the current v7 default.

## QA V3 integration

QA V3 now uses the page selector for both its initial evidence and every bounded `FIND_IDENTIFIER` or `SEARCH_TEXT` supplement. Its existing three-decision/two-round limits, exact-quote validation, durable call identity and no-automatic-retry rules remain.

The selection trace is returned with the answer so a reviewer can see why each page entered the model context.

When the active model profile has image input explicitly enabled, QA V3 locally renders only pages already admitted by that selector. One decision attaches at most three page-overview PNGs, no more than 32 MiB each and 48 MiB total. Supplemental evidence pages take priority in the next round. The complete PDF, project folder and unselected pages are never attached by this route.

Each attached page has a stable region ID, source document and page, full-page source bbox, coordinate system, contributing evidence IDs and SHA-256 of the exact rendered bytes. Those values and hashes enter the durable call fingerprint. A model may cite a visible page fact only through an exact supplied `IMAGE_REGION`; the citation remains `needs_review=true`, cannot support arithmetic and is enriched after validation with the audited coordinate system and image hash. Render failures are returned as bounded safe codes and never converted into an assertion that pixels were read.

The in-app model profile exposes image capability for DeepSeek, the official OpenAI Responses provider and a custom OpenAI-compatible endpoint as an explicit choice. DeepSeek keeps its supported vision preset when the capability is enabled, while disabling it freezes Reference QA to `deepseek-v4-flash` text input; OpenAI and custom routes use their selected model for both text and image input. Gemini and Mock do not gain an image route. This choice is saved as non-secret profile metadata, enters the frozen evaluation profile and never changes automatically after a failed call.

## Optional native structured output

The official OpenAI Responses provider and a custom OpenAI-compatible profile may explicitly select either `json_object` or native strict `json_schema` mode for Reference QA. Mock, DeepSeek and Gemini keep their established JSON-object payloads. CIRP does not probe a custom provider, infer support from a model name or automatically fall back after rejection.

When enabled, QA V2 answers and QA V3 evidence decisions attach their exact expanded task contract with a stable schema name and `strict=true`: custom Chat Completions uses `response_format`, while official OpenAI Responses uses `text.format`. Before any reservation or HTTP request, CIRP converts the Reference schemas' `oneOf` branches to supported `anyOf` branches and literal `const` values to one-value enums. Unsupported conditional keywords, an object that permits extra properties, an object that does not require every declared property or an invalid schema name fail locally. Extraction, visual extraction, legacy questions and semantic verification retain their existing output contracts.

The output mode and API protocol participate in the durable QA V2/V3 call identity and frozen evaluation profile. Older evaluations without these fields normalize to their historical `json_object` and `chat_completions` behavior; changing either active choice blocks execution as a profile mismatch. Returned content is still parsed and revalidated locally, and refusal, truncation or invalid content remains a safe terminal failure with no automatic retry. This implementation follows the official OpenAI Structured Outputs request shape but does not claim that an arbitrary compatible endpoint supports it until that endpoint is tested.

## Managed workspace and local result history

The browser now has a separate **Reference QA** workflow:

1. **Prepare reference files** creates a `REFERENCE_QA` run and performs local parsing/OCR only.
2. **Preview selected pages** shows the exact deterministic page-selection audit and makes no model call.
3. **Ask reference model** runs QA V3 and saves every validated terminal outcome locally.

Each saved result is an immutable version containing its project, run, source snapshot, submitted question, provider, model, exact result hash and bounded public decision/page-selection audit. Text citations are normalized and checked again against the saved immutable evidence. Image citations are checked against the supplied visual-region identity, page, bbox and exact sent-image SHA-256. Image bytes and hidden model reasoning are not stored in the result tables.

Answered results begin as `PENDING`. An engineer can mark them `ACCEPTED` or `REJECTED`; the request carries the expected review version so a stale browser cannot overwrite a newer review. Each transition records the local actor, optional note and immutable before/after event. The review changes only review metadata, never the model result or source evidence. Non-answer outcomes are saved as `NOT_APPLICABLE` so missing evidence and disabled-model outcomes remain visible without being presented as accepted answers.

The project result list, result detail, history, page preview and review operations make no model request. Migration 011 is additive and retains existing projects, uploads, runs, records and reviews.

## Per-decision execution receipts

Every settled QA V3 decision now produces a schema-validated `reference-model-input-receipt-1`. The receipt records the durable model-call ID and request hash, prompt-contract and normalized-question hashes, provider/model/protocol/output mode, round, exact evidence IDs with SHA-256 of the sent text, optional SHA-256 and byte count for the exact deterministic layout view sent, exact visual-region IDs with SHA-256 and byte counts, aggregate text/image byte counts, and the output-token bound.

The receipt deliberately contains no source text, layout text, prompt content, rejected provider output or private chain of thought. A fresh call has `cached=false`; recovery of the same settled call has `cached=true` while preserving the original call ID, request hash and input hashes. A publishable terminal result retains all of its decision receipts inside the immutable result and existing JSON export. A safely settled contract rejection retains its single receipt on the frozen evaluation failure record through additive migration 017. Historical results remain valid with an empty receipt list, and historical failures expose `execution_receipt=null`; neither is rewritten.

When a frozen failure has a validated receipt, the evaluation API may follow its exact model-call ID to the existing settled error and expose only the bounded validator category, such as `citation_scope`, `entity_scope`, `answer_contract` or `calculation`. It never returns the rejected provider output, source text, prompt body or exception message. Historical failures without a receipt show no category and require no migration.

Before a result or failed evaluation item is exposed, CIRP validates the receipt against the same project/run model-call ledger, saved request hash, frozen provider/question identity and admitted source-evidence IDs. The Reference workspace shows the metadata in a collapsed, text-safe audit panel. These receipts prove the locally recorded identity and size of an input; they do not prove that the selected material was relevant, that a provider executed it as claimed, or that the answer was correct.

## Frozen evaluation tasks

The Reference workspace can freeze an ordered set of 1 through 50 unique normalized questions as one resumable local evaluation task. Each task is permanently tied to one terminal `REFERENCE_QA` run and therefore to its exact source snapshot. It also records the active non-secret provider, text model, image capability, vision model, inference mode, structured-output mode and API protocol at creation. The API key and source text are not copied into the task record.

Creating, listing and reading a task makes no model request. Each item exposes its fixed order, exact normalized question, pending, completed or failed state, a compact link to its immutable result when one exists, a bounded safe terminal-failure record when no publishable result exists, and the current review state. Complete answers and citations remain in the existing result detail instead of being duplicated into every task listing. Failed items retain no rejected provider text or unvalidated answer. Task progress is derived from those local records, so accepting or rejecting a result is immediately reflected without rewriting the evaluation or model output.

For new QA V3 calls, the provider-facing ANSWER shape contains only atomic claims with compact ordered evidence or image-region aliases and optional cited arithmetic operands. Non-answer decisions use the same object with empty arrays. CIRP maps `E1..En` and `V1..Vn` back to real local identities, resolves text to the exact prompt-visible source atom, completes image document/page/bounds from the supplied region, joins claims, computes and displays Decimal results including one base with multiple percentages, and then applies the existing exact-source, numeric, entity and calculation validators. This removes redundant model copying and opaque random-ID transcription without changing the immutable result, receipt or review contracts; an out-of-range alias still fails closed.

**Preview pages** runs the existing transparent local page selector for one stored question and makes no model call. **Run question** executes exactly that one item through the existing bounded QA V3 evidence loop, saves the validated terminal Reference result and links it atomically. Repeating the action for a completed item returns the saved result without another model call. If the active provider, model, image capability, inference mode, structured-output mode or API protocol differs from the frozen profile, execution stops before dispatch.

**Run pending sequentially** remains available as the original browser-controlled option. Before dispatch, a dialog shows the frozen task, run/snapshot, model profile, remaining-question count, a maximum of three model decisions per question, the selected-source transfer boundary and possible provider charges. The sequence cannot start until the user checks the confirmation. The open browser then calls the existing one-item endpoint strictly one question at a time and waits for the result link to be saved before continuing.

Before every next question, the controller refreshes unresolved provider-call state. A local preflight or safely settled contract rejection becomes a terminal failed item and the sequence can continue to the next question without retrying it; rejected provider output is not saved. An unresolved call, profile mismatch, provider transport/policy failure or unclassified request error still stops the sequence with later items pending. **Stop after current question** allows an active request to finish and save but prevents the next dispatch. Closing, refreshing or switching projects likewise prevents further client dispatch. Reopening the task derives progress from durable result and failure records and requires another explicit confirmation before resuming only pending items. Page load never starts or resumes a sequence.

The task layer does not automatically score correctness. It supplies the reproducible snapshot, question order, execution state, immutable output and human-review progress needed for a controlled 15-question model evaluation. Migrations 012 and 013 are additive and leave legacy analysis and the original single-question Reference workflow unchanged.

## Whole-task input plan and readiness

**Inspect input plan** is a read-only local step between freezing a task and deciding whether to run it. CIRP applies the selector version saved by that task to every stored question against the frozen run and snapshot. The resulting manifest shows the frozen version, each question's selected file/page identities, match reasons, selected byte count, candidate and limit counts, bounded version-conflict notices, and aggregate unique/repeated pages, documents, pending text bytes and maximum remaining model decisions.

The manifest deliberately excludes source text, previews and source evidence IDs. It does not use the Canonical graph, embeddings, model output or review state, and it creates no provider request or model-call ledger row. Its stable `manifest_id` depends on the frozen task and deterministic selections. Operational readiness has a separate `status_id`, so changing the active model profile or starting another job can mark the task blocked without making it appear that the source input plan changed.

Readiness checks the exact frozen-profile match, whether the configured provider is locally valid, unresolved provider calls, active analysis, semantic verification or managed evaluation work, and whether questions remain pending. `READY` means only that the existing explicit confirmation step may be opened. It does not authorize, queue, resume or retry any model work. `BLOCKED` and `NO_PENDING` expose bounded reason codes; selection warnings such as no literal source match, version conflict or page/byte limit remain separate from operational blockers.

The browser renders only the metadata manifest with text-safe DOM operations and repeats the no-source-text, no-model-call, no-charge and no-authorization boundary. This feature reduces accidental irrelevant input and makes the next paid action inspectable, but it does not prove relevance, completeness, engineering precedence or answer quality.

## Human benchmark adjudication and release scorecard

The **Review benchmark** action closes a different gap from ordinary result review. An `ACCEPTED` Reference result records that an engineer accepted one saved answer, but the fixed model gate requires three explicit benchmark facts for every terminal question: whether the answer is fully usable, partial or unusable; whether it contains any factual claim unsupported by the independent source answer; and the engineer's optional explanation. CIRP does not derive these judgments from answer text.

Only terminal items can be adjudicated. `FULLY_USABLE` and `PARTIAL` require an `ANSWERED` result; safe contract failures, `CANNOT_ANSWER`, `NEED_USER_INPUT` and `MODEL_DISABLED` outcomes can only be `UNUSABLE`. Only answered results can carry the unsupported-claim flag, and `FULLY_USABLE` cannot be combined with it. These constraints prevent a missing or disabled response from being counted as a usable answer.

Each save includes the expected adjudication version. CIRP stores an append-only event containing the local engineer, exact before/after state and timestamp, while the current row provides fast display. A stale browser, cross-task item or invalid verdict combination fails locally. Adjudication never rewrites the immutable result, citations, evidence or the separate accepted/rejected result-review history. Migration 015 is additive.

The scorecard contains no answer or citation content. It reports only frozen task/profile identity, result and failure identities, result hashes, contract outcome status, human verdicts and counts. `MODEL_DISABLED` is reported separately and cannot count as a contract-valid model response. The scorecard and paginated history are local, deterministic, schema-validated, text-safe and make no model request.

The mechanical gate applies only to a non-Mock task containing exactly 15 questions. It remains incomplete until all questions have terminal outcomes and all 15 have human verdicts. Passing then requires all 15 to have contract-valid non-disabled responses—the integer consequence of the approved ≥98% rule—at least 12 `FULLY_USABLE` verdicts, and zero answers flagged with an unsupported factual claim. The page calls this a mechanical threshold rather than a correctness decision: its truth depends on the engineer having compared every result against the independent source answer. Other task sizes, Mock profiles and incomplete evaluations cannot pass.

## Managed evaluation execution

The same confirmation dialog also offers **Start managed job**. This creates one durable local execution session for the currently pending items. Creating or listing a job is a management operation and makes no model request. The saved job freezes the evaluation identity already held by the task, records confirmation time, pending count, a conservative three-decisions-per-question ceiling, bounded progress counters and only a safe reason code when it stops.

CIRP's existing single worker processes at most one managed evaluation job globally. It executes exactly one pending item through the existing one-item QA V3 path, waits until the immutable result or safe terminal failure is linked, then rechecks the task before choosing the next item. It never dispatches questions in parallel, retries a failed request, changes models, falls back to another provider or turns rejected output into a result.

While a managed job is queued, running or stopping, CIRP rejects new analysis runs, semantic verification, model-profile changes and other model-answer requests. Before each question it rechecks the frozen profile and unresolved-call ledger. A profile mismatch, unresolved call, provider/safety failure or unexpected competing task halts before another question is sent. Job rows contain no provider response, source text, API key or arbitrary exception string; complete answers and citations remain only in immutable Reference results.

**Stop after current question** lets an already dispatched item settle and save, then prevents the next dispatch. Closing the browser does not stop a managed job because it belongs to the local CIRP service. Service shutdown is different: queued or active jobs become `INTERRUPTED`, and startup never treats the earlier confirmation as permission to resume. The remaining pending items require a new explicitly confirmed job. Prior job history and every already saved item remain visible.

The browser lists the latest 20 managed jobs, their frozen model profile, this-session completed/failed counts, current question, remaining task count and safe terminal reason. It polls only while a job already known to the page is active. The existing one-question action and browser-controlled sequential option remain available when no managed job is active. Migration 014 is additive.

## Same-snapshot model A/B

After the user switches to a different active model profile, **Clone for active model** creates a new frozen evaluation from an existing one without calling a model. The clone has a new task ID and the current non-secret profile, but preserves the exact project, Reference run, snapshot, ordered normalized questions and question-set hash. Cloning under the same profile is rejected, and the original task and results are never rewritten.

Two evaluations can be compared only when their project, run, snapshot, question-set hash, ordinal, question key and normalized question text all match. The browser filters the candidate list to this exact identity. A mismatch fails locally; neither cloning nor comparison reserves or sends a model call.

The evaluation comparison displays each frozen profile and the saved public outcome on both sides: answer status, answer or missing facts, calculations, source-readable citations, provider/model, result and portable outcome hashes, review status, bounded safe failure or pending state. Each question is labelled only `PENDING`, `CHANGED` or `UNCHANGED`. Ordering and the comparison ID are deterministic, and the result is bounded to 50 questions and 8 MiB.

This creates the management path needed for a strong-model A/B on one immutable evidence snapshot, but it does not run that benchmark by itself. It does not score correctness, choose a preferred model, infer document precedence or treat an unchanged string as engineering approval.

Before a QA V2/V3 answer is accepted, the local contract also checks compact explicit Building/Bldg identities. If the question names one building and the answer names another, or an otherwise unnamed answer is supported only by exact citations explicitly naming another building, validation fails closed. The guard compares literal labels only: it does not infer building identity from a sheet number, file name or semantic database.

## Export and cross-run comparison

The Reference workspace can export a complete, schema-versioned JSON audit package for the project. An export contains the immutable result payload, normalized citations, current review state and every review event. It can be filtered to one `REFERENCE_QA` run or one review state. To keep this local management operation bounded, one export is limited to 500 results and 64 MiB; larger projects must export one run or review state at a time. Export does not call a model.

Two distinct terminal `REFERENCE_QA` runs in the same project can also be compared locally. Results are grouped by normalized question and classified as `ADDED`, `REMOVED`, `CHANGED` or `UNCHANGED`. The portable outcome comparison deliberately ignores run, snapshot, evidence, document, region and request-telemetry identities. It retains the answer, named missing facts, calculations, claim text and source-readable citation content. This means an unchanged answer can remain unchanged even though its immutable per-run result hash is necessarily different.

Exact result hashes, portable outcome hashes, provider, model, answer basis and review state remain visible. Review changes and processing-path changes are reported separately from answer changes. The comparison has deterministic ordering, a stable identity and explicit run/snapshot identities, and is limited to 2,000 result versions, 1,000 normalized questions and 32 MiB.

This comparison is a record-management tool. It does not choose a controlling result, decide which answer is correct, infer document precedence or call an LLM.

## Selector v5-v7 exact layout context

For selectors v5 and v6, text-layer evidence with valid saved word offsets and native bounding boxes can carry an optional per-block `layout_lines` view. CIRP groups words with the parser's existing line tolerance, sorts each row by its native horizontal position and inserts `||` only across a large horizontal gap. V7 retains that source data but groups exact tokens from the selected blocks on one page into deterministic native columns using a fixed 80-point gap, then attaches the bounded page view to the first selected evidence row for that page. The views contain exact source tokens plus delimiters; they do not infer headers, cells, leader lines, scope or engineering meaning and never replace the original source text or citation identity. Historical v3 through v6 inputs remain on their frozen paths.

The existing 18,000-byte serialized source-evidence budget is applied first. Layout context then receives at most 6,000 additional serialized bytes, so adding it cannot evict a source row that previously fit. Layout text enters the request fingerprint; the safe receipt stores only its SHA-256 and byte count. The fixed D1/C2 local checks confirm that the needed row/column relationships are present, and the full frozen 15-question gate retains all 15 expected pages and all 44 previously detected source atoms with zero provider calls. This is an input-quality result, not a model-answer or release claim. See `reports/kapolei_reference_layout_context_v5_2026-10-03.json`.

For D2, v5 selected the correct page but its exact thickness row ranked 71st and therefore fell outside the final 40-row bounded input. V6 moved that same immutable evidence row to final position 17 without changing page rank. The local fixed-15 expected-page gate remained 15/15, then one explicitly authorized Flash call returned a contract-valid D2 answer independently adjudicated fully usable with no unsupported claim or retry. This proves the demonstrated D2 repair only. See `reports/kapolei_reference_row_focus_v6_2026-10-03.json`.

For D4, visual inspection and saved word coordinates confirmed that one source page contains two callout columns whose continuations were split across parser blocks and interleaved by vertical position. V7's 748-byte page-column view joins the exact attachment, repair and each-side/perimeter sealant token sequences without changing the page rank. One controlled Flash call returned a contract-valid answer that recovered those requirement groups and removed the previous unsupported association, but omitted the leading quantity `(3)` and was independently adjudicated `PARTIAL`. A prompt-only quantity experiment ended in a safely settled `citation_scope` rejection and was rolled back without retry. See `reports/kapolei_reference_page_columns_v7_2026-10-03.json`.

The subsequently completed same-input selector-v7 baseline froze one run, snapshot, ordered question hash and 353,977-byte aggregate selection plan for both profiles. Flash produced 9 contract-valid and 7 fully usable items; V4 Pro produced 12 and 9. Both had zero unsupported flags in their full adjudication but missed the mechanical 15-valid and 12-fully-usable thresholds. The 34 provider decisions all settled, with zero unresolved call and no automatic retry. The local comparison reported 12 changed and 3 unchanged outcomes without inferring correctness or a winner. See `reports/kapolei_reference_v7_flash_pro_2026-10-03.json`.

The arithmetic follow-up compares explicit source numerics by Decimal value, so comma, currency and trailing-zero formatting do not reject an identical value. It still requires every operand in cited source text and recomputes every result locally. The prompt now tells models that `layout_lines` is navigation context rather than citation support and that a descriptive cover-sheet phrase requires `SEARCH_TEXT`, not a labelled identifier lookup. Controlled Pro C1/C3/C5 work produced fully usable, partial and fully usable outcomes; the corresponding Flash smoke produced three contract failures. A later same-page citation-completion prototype was rolled back because its two contract-valid results recorded zero completions and included an incorrect C1 answer. No post-change full comparison was run. See `reports/kapolei_reference_calculation_contract_2026-10-03.json`.

On the fixed 15-question page-identity gate, selector v3 includes the independently recorded source page for 15/15 questions, up from 14/15 for selector v2 and 10/15 for selector v1, with zero provider calls. The v3 cover predicate activates only for S1 in that fixture and selects page 1 of the PCD drawings file; the other 14 questions retain the v2 ranking path. The v4 four-page policy preserves 15/15 because the expected page ranks no lower than fourth, while reducing the maximum page references from 90 to 60. These gates check routing identity only and do not claim answer correctness or completeness. See `reports/kapolei_reference_page_selection_2026-10-01.json` and `reports/kapolei_reference_page_selection_v4_2026-10-01.json`.

The controlled DeepSeek development run used selector v2 and made 20 sequential decisions with zero unresolved calls. Seven questions produced saved contract-valid terminal results, six were answered, and three were fully usable against the independent direct-PDF fixture: S3, S5 and C3. The previous product path also achieved 3/15, on S3, S4 and C5. D3 and D4 improved but remained incomplete; S4 passed the structural contract but answered Building G for a Building I question. The run therefore does not meet the 98% contract-valid / 12-of-15 usable release gate. S1 and S2 were not automatically rerun after later fixes, and selector v3 has not been used for a new model run, so this remains a staged development comparison rather than a homogeneous final-build benchmark. See `reports/kapolei_reference_qa_live_2026-09-30.md`.

Spreadsheet export, reviewer-edited Reference answers, a post-DEV-168 full same-input rerun and a local-small-model comparison remain future gates. Durable explicitly confirmed server-side execution and one fully adjudicated selector-v7 Flash/Pro baseline are implemented, but neither model is approved for unattended release.
