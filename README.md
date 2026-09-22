# CRIP — CIRP v0.2.6 Recovery and Validation Snapshot

This public repository contains the recovered CIRP v0.2.6 application source, its original Git history, and a validated English Excel result. The repository name is **CRIP** as requested; the application identifier remains **CIRP** for compatibility with existing files, APIs, and local data.

CIRP is a local construction-document review prototype. It parses project files, prepares reviewer candidates, keeps supporting evidence with each item, and requires human approval before results are used.

## Current reviewer scope

- Ask the selected project run a plain-English question. CIRP uses a run-scoped SQLite full-text index to rank bounded evidence—including exact RFI/Submittal identifiers, Email text and source locators—then sends only that context through the configured budgeted model when the user submits. A capped local construction vocabulary bridges audited wording such as accepted/approved, answer/response, Request for Information/RFI and Shop Drawing/Submittal while preserving whole-word and direct-match priority. Pure numeric RFI IDs use the parser's exact zero-padding equivalence, so a question about `RFI 42` can recover `RFI 0042` from Specification prose or Email even behind 160 stronger generic candidates; prefixed RFI and Submittal IDs remain exact. Comparison questions select one passage from each distinct file/workflow source before filling remaining slots, so repeated specification passages cannot hide the referenced RFI, Submittal or Email; an answered comparison lists a separate plain-English finding, file name and exact inline quotation for every named source family. Explicit numeric claims, full dates, exact workflow identifiers and bounded workflow dispositions must occur in their own cited quotations. A date is checked as one value, so components from two cited dates cannot be recombined; ISO and English month-name formatting are equivalent, while ambiguous slash order is never interpreted. Due, issued, submitted, received, sent, reviewed, approved, revision and response labels also stay paired with their own dates. An RFI/Submittal date stays attached to the same exact workflow identity in its statement or one-identity locator; another item in the same quotation cannot donate its date. Distinct workflow items may have independent dates for the same role, while multiple dates for one identity and role remain ambiguous. Lifecycle dates may coexist in a date answer without manufacturing a current status; status questions retain the stricter conflict rule. Unsupported dimensions, quantities, dates, identifiers, opposite RFI/Submittal/Email states and invented approval qualifiers fail closed instead of being inferred. Distinct states in the citations or matching retrieved evidence for one workflow remain ambiguous even if the model omits one; an exact RFI/Submittal status question additionally reuses the complete terminal-run workflow index and returns insufficient evidence before model dispatch when another primary source carries a conflicting status outside the retrieved passages. Each conflicting status, original source file and detected/manual origin is shown directly below that answer with a safe file link. Up to 32 detected sources also receive an exact inline parser-text quote and existing locator when the matching bounded status phrase is in the same statement as the exact workflow identity, or the fragment or locator is scoped to that identity alone; manual corrections, cross-workflow phrases and file-level detections without that phrase remain honestly uncited. Recognized composite phrases, exact identifiers and separate comparison findings stay isolated. Each comparison finding is checked only against its own quotations. Ordinary questions retain pure relevance order and their compact answer format. Equivalent questions that differ only by repeated whitespace reuse one settled task while preserving the submitted wording; letter case, punctuation and identifiers remain significant, and existing exact settled tasks remain recoverable. Existing databases are backfilled locally; systems without FTS5 use a complete compatibility scan. Mock mode retrieves context but never fabricates an answer.
- Within an ordinary answer or one source finding, a dimension, quantity or other number must stay with the same exact RFI/Submittal statement or one-identity locator. Another workflow item in the same quotation and a multi-identity locator cannot donate its value. Numbers inside the identifier itself are not treated as material or quantity values; comparison summaries combine only source findings that pass this check.
- Explicit measurement labels remain paired with their own numbers: pipe size/diameter cannot borrow insulation thickness, width cannot borrow height, and strength cannot borrow pressure. The same relationship stays scoped to one RFI/Submittal when named. Common dimensional pipe and insulation wording is supported without adding unit conversion or a general engineering ontology.
- Recognized units remain paired with the same number, measurement property and RFI/Submittal identity. Common recognized forms may touch the number (`2mm`, `150MPa`, `4L/s`) or use a space. Formatting-only inch and foot spellings/symbols normalize; millimeters, pressure/strength units, flow units, percentages, weights and explicit temperatures remain distinct. CIRP never converts units while validating an answer.
- Explicit six-digit CSI/MasterFormat sections remain whole and stay with the same RFI/Submittal identity or source finding. Spaces, hyphens and dots are formatting equivalents, but groups from different cited sections cannot be recombined; an unlabelled Submittal identifier is not treated as a specification section.
- Explicit Revision/Rev labels remain whole and stay with the same RFI/Submittal identity or source finding. Prefix punctuation and case normalize, while labels remain exact and cannot be borrowed or recombined. Revision dates, unlabelled status words and metadata-only labels are excluded; CIRP does not infer which revision controls.
- Exact unfiltered questions such as `How many RFIs, Submittals, and Emails are in this run?` and `List all RFIs, Submittals, and Emails in this run.` reuse the complete cached workflow index and answer before evidence retrieval, with no model request or budget action. RFI/Submittal inventory is based on identifier groups; Email inventory deduplicates analyzed Email, `.eml` or `.msg` files. Lists state the complete total and show at most 50 ordered values per category; the paginated Workflow Reviewer remains the complete large-inventory view. Identifier, source, date, content and multi-filter questions stay on the ordinary evidence path.
- Strict one-status questions such as `How many open RFIs are in this run?` or `List all approved as noted Submittals.` also use the complete index without retrieval or a model call when the status applies to the requested workflow type. Only identifiers with one matching explicit status are confirmed; matching identifiers with another explicit status are excluded and reported separately as ambiguous. Missing documents and relationship state do not create a status. Email-derived RFI/Submittal contexts participate, and confirmed/ambiguous lists each retain complete totals while displaying at most 50 values.
- Strict single-item questions such as `What is the status of RFI 42?` now use that complete index before retrieval. One distinct explicit status from the bounded vocabulary applicable to that RFI/Submittal type is answered locally and every detected or manually corrected source file is shown directly below it; detected parser text receives an exact inline quotation only when it ties that status to the same identifier. Multiple statuses and malformed values such as `OPEN CLOSED` remain insufficient evidence, while missing, unknown or inapplicable status stays on the ordinary evidence path. Email-derived workflow contexts participate, with no provider request or budget action.
- Model-generated answers now ground every explicit RFI/Submittal identifier in that answer or source finding's own exact quotations or citation locators. `RFI 42` may match `Request for Information No. 0042`, but `RFI ARC-42`, `RFI ARC-0042`, compound IDs and distinct Submittal IDs never merge. A locator may support only numeric occurrences inside the exact matched workflow identity; even the same value used elsewhere as a dimension, quantity or date remains quote-bound. Multiple exact markers on one line remain separately visible.
- Workflow routing uses an explicit email Subject first only when it contains a valid digit-bearing RFI or Submittal ID; otherwise the earliest supported body heading becomes primary and later identifiers remain references.
- Email addresses in headers, current body, or quoted history cannot create RFI or Submittal workflow groups; routing headers remain visible evidence.
- RFI roles require a standalone `Question`/`Response`/`Official Response` heading or an explicit separator. Exact known `Status`/`RFI Status` values remain reviewer metadata; status-like prose stays neutral and never creates a role.
- `Request for Information No. 0042`/`RFI 0042` and `Submission 23-01`/`Submittal 23-01` create the same exact relationships. Every distinct exact marker on one line is retained; canonical duplicates collapse. Compact digit-bearing prefixes such as `ARC-0042`, `MEP-023` and `SUB-001` are preserved without fuzzy matching or cross-prefix merging.
- The same full-name aliases work in bounded filename fallback for scanned or otherwise textless files.
- One TXT, Email or multi-page Submittal retains every distinct explicit status and becomes ambiguous when those statuses conflict. Exact `Status`, `Review Response`, `Final Response` and `Submittal Response` labels accept only the existing known disposition phrases, reuse canonical groups and never infer authority or winning precedence from prose.
- Materials and equipment, with the item name, quantity, unit, design properties, specification section, location, status, and evidence separated into readable fields.
- Executable inspections and tests, with the QA activity, specification section, performer, witness, timing, frequency, acceptance criteria, and evidence kept with the item.
- English application and export text. Source quotations remain source evidence and are not rewritten as if they were original English text.
- Optional local OCR, selective full-page or high-resolution schedule-table visual analysis, DXF/CAD metadata, and review-only quantity candidates.
- Local human review and a project cost ledger with a user-selected CNY dispatch cap (CNY 300 for new projects by default).
- User-selectable 1, 2, or 4 local workers across documents or bounded page chunks inside one PDF, selective vision routing, adjacent-evidence extraction batching inside one ordinary or exact RFI/Submittal scope, evidence-scope verification batching, and visible stage/model timing.
- The workflow reviewer reads only nine relationship fields from each parser summary through SQLite's bundled JSON projection; large page, vision, CAD and geometry arrays are not transferred to Python for RFI/Submittal/Email indexing. The browser shows loaded/total workflow groups and appends reviewer-requested 500-group pages without duplicate group IDs. A terminal run reuses one bounded process-local index across pages; active runs remain uncached.
- A reviewer may compare detected and effective workflow metadata, then apply or reset an exact current-run RFI/Submittal/Other correction. The versioned overlay immediately invalidates the terminal index, never rewrites parser data or hides explicit Email attachment provenance, and starts no model call.
- Deterministic pre-publication checks for clear material-name, QA-activity, duplicate-property, evidence-scope, and mixed workflow-source errors. Invalid RFI-question, rejected-Submittal, email-header, or quoted-history citations are removed individually while valid direct support is preserved; every remaining workflow source contributes to the visible conditional status.
- Native PDF page routing with specification Section/Part/clause locators and bordered table rows kept as coordinate-backed evidence. A generic-name multi-page PDF carries the latest exact RFI/Submittal scope through ordered continuation pages, with equivalent 1/2/4-worker output, until another exact heading replaces it. Borderless and cross-page tables still require review.
- Bounded DOCX body text and tables retain native element and line locators. One table containing consecutive exact RFI/Submittal sections is split at identifier, role and status boundaries so an earlier RFI question cannot inherit a later Submittal disposition. Complete Word heading/list/merged-table/image/revision/comment structure remains planned.
- Local `.eml` parsing for safe headers and visible body text, with RFI question/response and Submittal status evidence boundaries. Each MIME alternative group selects one non-empty body: plain text first, then safe HTML; conflicting same-type alternatives warn instead of merging. For `multipart/related`, the declared `start`/`Content-ID` root—or the first child fallback—is the only body; every non-root resource stays inert and requires explicit import. Unnamed resources receive a deterministic standard MIME extension so supported selected imports reach the existing parser; supplied filenames are preserved. Paired blockquotes, exact Gmail/Yahoo/Proton/Outlook reply wrappers, strict signature boundaries, and explicit forwarded headers keep current body, history and signatures separate. Spaced CSI-style Submittal identifiers and same-line metadata normalize across body, Subject and filename; status keywords require a complete allowlisted value, so longer descriptions stay neutral. Every nested `message/*` part is also inert even without filename or disposition metadata. HTML active content and remote resources are not executed or fetched.
- Local Outlook `.msg` parsing reuses the same Email/RFI/Submittal evidence, MIME-derived attachment names, and selected-attachment paths. A reviewer may import one attachment or all supported attachments in a bounded batch; the parent is parsed once and every selected identity is prevalidated before upload creation. The pinned MIT-licensed `python-oxmsg` package decodes the bounded binary container; CIRP does not implement a second semantic parser or connect to a mailbox.
- Email threads accept only bounded RFC-style `<local@domain>` tokens from `Message-ID`, `In-Reply-To`, and `References`; malformed values and arbitrary header words stay standalone instead of creating false links, and accepted identifiers remain hash-only.
- Current email text and explicit quoted history are separated; exact RFI, Submittal, hash-only email-thread, and selected-attachment relationships appear in one bounded reviewer workflow view without a model call. Primary workflow routing uses an email Subject with a valid digit-bearing workflow ID before an explicit body heading before filename; a workflow-like Subject without an accepted ID stays visible evidence but cannot suppress a valid body primary. For a body-routed primary, different exact same- or cross-type identifiers remain references and cannot donate RFI role or Submittal status; valid Subject priority remains unchanged. Each explicit body section keeps its own identifier, role or status in the evidence locator. A bounded mix of reply/forward prefixes and bracketed enterprise labels such as `[EXTERNAL]` or `[External Email]` may precede an otherwise explicit workflow Subject. UNKNOWN RFI page contexts stay neutral instead of manufacturing a missing Question or Response. Exact Submittal references link to their primary source; conflicting explicit statuses remain ambiguous. Every identifier in a multi-value `In-Reply-To` header is hashed separately. Exact local ancestors appear before replies; self-references, parent/reference cycles and multiple distinct Message-ID values in one email are ambiguous and retain all messages. Raw message identifiers are not persisted, while a missing Message-ID remains a valid standalone item. Attachment provenance is shown only for a parsed email parent.
- Email headers, quoted history and recognized signatures remain visible evidence, but close locally before extraction batching. Only eligible current email evidence reaches the model, and Coverage reports the locally skipped fragment count.
- Customer-configured read-only Autodesk Construction Cloud or Procore browsing and selected-file import, reusing project capacity checks, 4 MiB chunks, SHA-256, deduplication, and optional Windows current-user DPAPI storage.
- Reproducible offline PDF performance measurement in `scripts/benchmark_local_parse.py`; see `docs/PERFORMANCE_BASELINE.md` for the measured fixture and limits.

Conflict and Missing Information are not shown as current reviewer categories or export sheets. Legacy stored records are preserved for compatibility. Coverage and partial-processing status remain visible at run level.

## Safety defaults

The ordinary local launcher runs in mock mode and does not call a paid model API. Live provider use requires an explicit live launcher and local configuration. Saved API keys use Windows DPAPI current-user encryption and are excluded from Git.

Never commit `.env`, `.local`, databases, uploaded source documents, logs, or provider credentials. The repository ignore rules exclude those paths by default.

## Start locally

Requirements:

- Windows with Python 3.11 or newer for the standard local launcher.
- macOS or Linux can use the shell launcher.

Windows mock mode:

```text
start-local.cmd
```

macOS or Linux mock mode:

```bash
./start-local.sh
```

The default local address is `http://127.0.0.1:8000`.

Live-provider launchers are available for local, explicitly authorized use:

```powershell
.\start-deepseek-live.ps1
.\start-gemini-live.ps1
.\start-custom-model.ps1
```

Review the on-screen data-transfer and cost notice before starting a live analysis. A browser tab or successful startup message does not prove that a document analysis completed; check run status, coverage, records, and the cost ledger.

The custom launcher opens a local setup page. Enter an OpenAI-compatible base URL ending before `/chat/completions` (for example, `http://127.0.0.1:11434/v1` for Ollama), the model name, and token rates. Loopback local models may use HTTP with no key and zero rates. Remote APIs require HTTPS, a key, and positive rates. This generic route requires JSON-mode chat completions with usage data and sends parsed text only; provider-specific SDKs, page-image vision, non-loopback LAN models, and automatic model discovery are not included.

## Validate the source

Install the pinned application and development dependencies in an isolated environment, then run:

```bash
python scripts/run_checks.py --area all
```

This single entry point runs the Python suite, specification and bundle-manifest checks, and the web and deployment JavaScript checks. It exits with an error instead of silently skipping JavaScript validation when Node.js is unavailable.

The publication recovery was validated from a clean local environment with:

- 642 Python tests passed.
- 24 localization/UI JavaScript tests passed.
- 11 deployment-boundary JavaScript tests passed.
- 12 offline Chromium browser regressions passed with zero model calls and zero external requests.
- Requirements/specification synchronization passed.
- The source bundle manifest passed for 370 files.

No DeepSeek, Gemini, or other paid model call was made during recovery, validation, or publication preparation.

## Published test result

The validated workbook is available at [test-results/CIRP_Focused_English_Result.xlsx](test-results/CIRP_Focused_English_Result.xlsx). Its verification record and SHA-256 checksum are in [test-results/README.md](test-results/README.md).

The workbook contains 2,804 material/equipment candidates and 380 inspection/test candidates on three visible sheets. It contains no formulas, hyperlinks, comments, external workbook links, CJK characters, private-use glyphs, or CIRP internal record/evidence/call identifiers.

The source PDFs are intentionally not included. The workbook is a reviewer candidate set, not a construction-accuracy certification, procurement list, approved takeoff, or substitute for professional review.

## Repository structure

- `app/` — local API, parsing, assembly, export, verification, OCR, visual, CAD, security, and credential handling.
- `web/` — browser interface and English localization.
- `spec/` and `contracts/` — requirements and machine-readable contracts.
- `prompts/` — provider-facing extraction and verification prompts.
- `tests/` — offline application, contract, governance, UI, and deployment tests.
- `docs/` — architecture, operational boundaries, decisions, and historical validation notes.
- `test-results/` — the public validated workbook and its verification record.

## Important limitations

- All extracted items remain subject to human review.
- A partial run is not evidence of construction accuracy or completeness.
- PDF vector geometry is not treated as material quantity.
- CAD-derived counts and measurements remain review candidates until scope, units, and duplicate representations are verified.
- Mailbox synchronization, automatic attachment recursion, and semantic email-thread inference are not supported yet.
- Autodesk/Procore interactive OAuth, automatic token refresh or provider-side revocation, recursive bulk import, change polling, continuous synchronization, and live-tenant acceptance are not included yet. Forgetting a connection removes only the local encrypted copy.
- The public workbook demonstrates the export format and a completed processing result; it does not prove that every extracted item is correct.
- No license file is included. Public visibility does not grant reuse rights beyond applicable law and the repository owner's permissions.
