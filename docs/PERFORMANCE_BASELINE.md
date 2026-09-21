# Local PDF performance baseline

Measured on 2026-09-15 with Windows 11, Python 3.12.2, the repository virtual environment, and no model API calls.

The two earlier customer PDFs were no longer present on disk, and the handoff archive contains no PDF sample. This report therefore uses the benchmark tool's deterministic 48-page fixture. It contains 44 text/vector pages and 4 image-only pages that exercise local OCR. It is a real wall-clock measurement of the current parser, but it is not a customer-document benchmark or a construction-accuracy evaluation.

| Local workers | Time | Speedup vs 1 | Pages | Fragments | OCR fragments | Output match |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 10.213 s | 1.00x | 48/48 | 136 | 4 | yes |
| 2 | 8.929 s | 1.14x | 48/48 | 136 | 4 | yes |
| 4 | 7.496 s | 1.36x | 48/48 | 136 | 4 | yes |

All three runs produced the same content hash. The fixture is `PARTIAL` by design because image-only pages retain OCR/vision review warnings.

The same 136 fragments form 34 adjacent extraction batches under the four-fragment/8.8 KB ceilings, a 75% reduction in request count for this fixture. This is a deterministic request-count estimate; no paid extraction call was sent, so it is not a model latency, cost, or output-quality claim.

A four-page control run measured 0.595 s with one worker, 0.837 s with two, and 0.843 s with four. Process startup dominates small files, so one worker remains the sensible small-file choice even though large-file users may select two or four.

Reproduce locally:

```powershell
.venv\Scripts\python.exe scripts\benchmark_local_parse.py --fixture-pages 48 --workers 1 2 4
```

To measure an actual project PDF without a model call, append its path. Raw measurements are written only to ignored `reports/local/` files.

## CR-0021 parser-structure recheck

After adding deterministic page routing, specification locators and bordered table-row evidence, the same generated 48-page fixture was rerun on 2026-09-15. It retained 136 fragments, 34 estimated adjacent extraction batches and identical content hashes for all worker settings.

| Local workers | Time | Speedup vs 1 | Output match |
|---:|---:|---:|---|
| 1 | 10.351 s | 1.00x | yes |
| 2 | 9.033 s | 1.15x | yes |
| 4 | 8.499 s | 1.22x | yes |

The earlier and later wall-clock values are close enough to include ordinary local variance and process-startup effects. This recheck establishes output/request-count stability, not a speed improvement or a customer-document accuracy result.

## CR-0033 workflow-summary read baseline

The workflow reviewer needs nine relationship fields from each parser summary. It previously transferred and decoded the complete JSON summary, including unrelated page, vision, CAD, takeoff and geometry arrays. The endpoint now uses the SQLite JSON functions bundled with Python to project those nine fields before Python decoding, while preserving legacy top-level workflow fields.

On the same Windows/Python environment, `scripts/benchmark_workflow_read.py` generated a 2,000-page synthetic summary and measured the best of three 300-iteration query-and-decode rounds:

| Read path | Returned JSON | 300 query/decode operations |
|---|---:|---:|
| Complete parser summary | 1,112,749 bytes | 599.8 ms |
| Nine workflow fields | 254 bytes | 255.1 ms |

This is a 99.98% payload reduction and 2.35x synthetic speedup. It is not a browser, network, customer-project or semantic-accuracy benchmark; actual gains depend on document-summary size and local SQLite behavior. No model API or customer file is used.

Reproduce locally:

```powershell
.venv\Scripts\python.exe scripts\benchmark_workflow_read.py --pages 2000 --iterations 300
```

## CR-0052 routing-only email extraction baseline

A synthetic RFI reply retains headers, current body and quoted history but now extracts only the current body; the other scopes close locally as `NO_REQUIREMENTS`. Estimated batches fall 3→1 without changing the mock candidate. This is structural, not a universal latency, cost or accuracy claim.

## CR-0053 HTML reply-wrapper accuracy fixture

Synthetic Gmail and Outlook quote wrappers previously leaked historical `Approved` text beside current `Pending`. They now create reviewable history, resume current text after closure and stay outside extraction. This is an offline structural check, not mailbox or live-model validation.

## CR-0054 signature-boundary accuracy fixture

A synthetic RFI reply previously extracted both a valid material and a company name inside a Gmail signature. Gmail and strict `-- ` signatures now remain reviewable but local; closed HTML signatures resume current text. This is a deterministic boundary, not universal signature or live-model validation.

## CR-0068 workflow extraction-scope fixture

Adjacent `RFI 42 > QUESTION` and `SUBMITTAL 23-01 > STATUS: APPROVED` fragments previously shared one prompt. Exact workflow differences now close the batch; matching scopes and ordinary evidence retain four-fragment/8.8 KB limits. This is an offline boundary check.

## CR-0070 multi-page workflow-scope fixture

A six-page generic-name PDF previously lost `RFI 42 > RESPONSE` on continuation pages 2–5. Ordered merge now carries the scope, adds `STATUS: CLOSED` on page 5 and replaces it with exact `Submittal 23-01` on page 6; 1/2/4-worker locators match without rereading the PDF. This is a synthetic structural check.

## CR-0071 same-line workflow metadata fixture

Synthetic text, Email and compact filenames previously lost same-line role/status or absorbed it into the identifier. Existing allowlists now separate exact metadata while descriptive titles stay neutral. This is an offline parser check, not semantic or live-provider validation.

## CR-0072 exact Submittal status fixture

Three synthetic Email status lines beginning with Approved, Pending or Rejected previously became formal dispositions despite trailing prose. Complete allowlisted values still pass with terminal punctuation; longer prose stays neutral. This is an offline boundary check.

## CR-0073 selected Email attachment batch fixture

A synthetic EML with two supported attachments now needs one parse and one explicit import request instead of two parses and two requests. Both attachment identities are prevalidated before upload creation; an invalid hash or duplicate index leaves the manifest unchanged. This is a structural offline fixture, not a mailbox, customer-file, storage-throughput or live-provider benchmark.

## CR-0074 DOCX workflow-table accuracy fixture

One synthetic Word table containing `RFI 42 > QUESTION` followed by `SUBMITTAL 23-01 > STATUS: APPROVED AS NOTED` previously produced one fragment labeled entirely as the Submittal. It now produces separate evidence scopes with the original native table element and non-overlapping line ranges. This is an offline boundary check, not a complete Word-structure or construction-accuracy benchmark.

## CR-0075 workflow-classification correction fixture

A synthetic run changes `RFI 42` to `Submittal 23-01 / Pending`, rejects a stale version and resets it. Parser bytes and model-call count stay unchanged; two events retain before/after values. The browser repeats this with zero model or external calls. This is structural, not an accuracy benchmark.
