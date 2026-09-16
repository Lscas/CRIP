# CRIP — CIRP v0.2.6 Recovery and Validation Snapshot

This public repository contains the recovered CIRP v0.2.6 application source, its original Git history, and a validated English Excel result. The repository name is **CRIP** as requested; the application identifier remains **CIRP** for compatibility with existing files, APIs, and local data.

CIRP is a local construction-document review prototype. It parses project files, prepares reviewer candidates, keeps supporting evidence with each item, and requires human approval before results are used.

## Current reviewer scope

- Workflow routing uses an explicit email Subject first only when it contains a valid digit-bearing RFI or Submittal ID; otherwise the earliest supported body heading becomes primary and later identifiers remain references.
- Email addresses in headers, current body, or quoted history cannot create RFI or Submittal workflow groups; routing headers remain visible evidence.
- RFI roles require a standalone `Question`/`Response`/`Official Response` heading or an explicit separator. Exact known `Status`/`RFI Status` values remain reviewer metadata; status-like prose stays neutral and never creates a role.
- `Request for Information No. 0042`/`RFI 0042` and `Submission 23-01`/`Submittal 23-01` create the same exact relationships. Compact digit-bearing prefixes such as `ARC-0042`, `MEP-023` and `SUB-001` are preserved without fuzzy matching or cross-prefix merging.
- The same full-name aliases work in bounded filename fallback for scanned or otherwise textless files.
- One TXT, Email or multi-page Submittal retains every distinct explicit status and becomes ambiguous when those statuses conflict. Exact `Status`, `Review Response`, `Final Response` and `Submittal Response` labels accept only the existing known disposition phrases, reuse canonical groups and never infer authority or winning precedence from prose.
- Materials and equipment, with the item name, quantity, unit, design properties, specification section, location, status, and evidence separated into readable fields.
- Executable inspections and tests, with the QA activity, specification section, performer, witness, timing, frequency, acceptance criteria, and evidence kept with the item.
- English application and export text. Source quotations remain source evidence and are not rewritten as if they were original English text.
- Optional local OCR, selective full-page or high-resolution schedule-table visual analysis, DXF/CAD metadata, and review-only quantity candidates.
- Local human review and a project cost ledger with a user-selected CNY dispatch cap (CNY 300 for new projects by default).
- User-selectable 1, 2, or 4 local workers across documents or bounded page chunks inside one PDF, selective vision routing, adjacent-evidence extraction batching, evidence-scope verification batching, and visible stage/model timing.
- The workflow reviewer reads only nine relationship fields from each parser summary through SQLite's bundled JSON projection; large page, vision, CAD and geometry arrays are not transferred to Python for RFI/Submittal/Email indexing. The browser shows loaded/total workflow groups and appends reviewer-requested 500-group pages without duplicate group IDs instead of silently treating the first 500 as complete.
- Deterministic pre-publication checks for clear material-name, QA-activity, duplicate-property, evidence-scope, and mixed workflow-source errors. Invalid RFI-question, rejected-Submittal, email-header, or quoted-history citations are removed individually while valid direct support is preserved; every remaining workflow source contributes to the visible conditional status.
- Native PDF page routing with specification Section/Part/clause locators and bordered table rows kept as coordinate-backed evidence. Borderless and cross-page tables still require review.
- Local `.eml` parsing for safe headers and visible body text, with RFI question/response and Submittal status evidence boundaries. Each MIME alternative group selects one non-empty body: plain text first, then safe HTML; conflicting same-type alternatives warn instead of merging. Paired blockquotes, exact Gmail/Outlook reply wrappers, strict signature boundaries, and explicit forwarded headers keep current body, history and signatures separate. Spaced CSI-style Submittal identifiers normalize across body, subject and filename. MIME parsing stops at attachment boundaries, so attached messages and files stay inert until explicit import through normal capacity, hash and deduplication controls. HTML active content and remote resources are not executed or fetched.
- Local Outlook `.msg` parsing reuses the same Email/RFI/Submittal evidence and selected-attachment paths. The pinned MIT-licensed `python-oxmsg` package decodes the bounded binary container; CIRP does not implement a second semantic parser or connect to a mailbox.
- Current email text and explicit quoted history are separated; exact RFI, Submittal, hash-only email-thread, and selected-attachment relationships appear in one bounded reviewer workflow view without a model call. Primary workflow routing uses an email Subject with a valid digit-bearing workflow ID before an explicit body heading before filename; a workflow-like Subject without an accepted ID stays visible evidence but cannot suppress a valid body primary. Different exact identifiers remain references and cannot donate their RFI role or Submittal status to the primary identifier. Each explicit body section keeps its own identifier, role or status in the evidence locator. A bounded mix of reply/forward prefixes and bracketed enterprise labels such as `[EXTERNAL]` or `[External Email]` may precede an otherwise explicit workflow Subject. UNKNOWN RFI page contexts stay neutral instead of manufacturing a missing Question or Response. Exact Submittal references link to their primary source; conflicting explicit statuses remain ambiguous. Every identifier in a multi-value `In-Reply-To` header is hashed separately. Exact local ancestors appear before replies; self-references, parent/reference cycles and multiple distinct Message-ID values in one email are ambiguous and retain all messages. Raw message identifiers are not persisted, while a missing Message-ID remains a valid standalone item. Attachment provenance is shown only for a parsed email parent.
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

- 631 Python tests passed.
- 24 localization/UI JavaScript tests passed.
- 11 deployment-boundary JavaScript tests passed.
- 12 offline Chromium browser regressions passed with zero model calls and zero external requests.
- Requirements/specification synchronization passed.
- The source bundle manifest passed for 335 files.

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
