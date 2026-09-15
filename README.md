# CRIP — CIRP v0.2.6 Recovery and Validation Snapshot

This public repository contains the recovered CIRP v0.2.6 application source, its original Git history, and a validated English Excel result. The repository name is **CRIP** as requested; the application identifier remains **CIRP** for compatibility with existing files, APIs, and local data.

CIRP is a local construction-document review prototype. It parses project files, prepares reviewer candidates, keeps supporting evidence with each item, and requires human approval before results are used.

## Current reviewer scope

- Materials and equipment, with the item name, quantity, unit, design properties, specification section, location, status, and evidence separated into readable fields.
- Executable inspections and tests, with the QA activity, specification section, performer, witness, timing, frequency, acceptance criteria, and evidence kept with the item.
- English application and export text. Source quotations remain source evidence and are not rewritten as if they were original English text.
- Optional local OCR, full-page visual analysis, DXF/CAD metadata, and review-only quantity candidates.
- Local human review and a project cost ledger with a CNY 300 dispatch cap.

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

- 494 Python tests passed.
- 23 localization/UI JavaScript tests passed.
- 11 deployment-boundary JavaScript tests passed.
- Requirements/specification synchronization passed.
- The source bundle manifest passed for 248 files.

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
- The public workbook demonstrates the export format and a completed processing result; it does not prove that every extracted item is correct.
- No license file is included. Public visibility does not grant reuse rights beyond applicable law and the repository owner's permissions.
