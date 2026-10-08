# Kapolei HS CIRP Product-Path Validation

Status: COMPLETED

## Purpose

This validation measures the complete CIRP product path—local PDF parsing, OCR/vision enrichment, saved evidence retrieval, and a DeepSeek-backed project answer—against an independent Codex direct reading of the original PDFs. It is not a standalone DeepSeek model benchmark.

The 15 questions and the direct-PDF answer key were fixed before any CIRP project answer was requested. The deterministic sample contains five project-scope questions, five drawing/specification detail questions, and five calculations. See `kapolei_cirp_vs_direct_pdf_questions.json` and `kapolei_direct_pdf_answers.json`.

## Inputs

- Kapolei HS Q82227-21 Drawings Vol 1 FINAL - PCD 01-03 Included.pdf: 117 pages.
- Kapolei HS Q82227-21 Drawings Vol 1 FINAL.pdf: 115 pages.
- Kapolei HS Q82227-21 SPEC COMPILED - PCD 03 included.pdf: 401 pages.
- Total selected input: 3 files and 633 pages.
- CIRP project: `P-d3e2e33a21ff49859c633553008dd233`.
- CIRP live analysis: `RUN-ae67a407392f40bbaa61593f08e0d28e`, DeepSeek provider, four local workers.

## Independent direct-PDF reference

| ID | Category | Locked direct-PDF answer |
| --- | --- | --- |
| S1 | Project scope | Kapolei High School Classrooms Renovation; DOE Q82227-21; 91-5007 Kapolei High School Parkway, Kapolei, Hawaii 96707; modernization build-out with new finishes, fixtures, and equipment. |
| S2 | Project scope | Building I first floor: 4 months; Building G first floor: 4 months; Building G second floor: 4 months; sequential work with only one floor vacated at a time. |
| S3 | Project scope | Building G: Type V-A, fully sprinklered; E, B, and S-1; two stories; sprinkler system throughout. |
| S4 | Project scope | Building I: Type V-A, fully sprinklered; E, B, and S-1; two stories; sprinkler system throughout. |
| S5 | Project scope | General 7; Architectural 48; Structural 13; Mechanical 20; Fire Protection 8; Plumbing 19. |
| D1 | Detail | Code 1: 3-5/8-inch metal stud at 16 inches on center; code 2: 6-inch metal stud at 16 inches on center; code 21: 8-inch solid-grouted CMU; code 22: existing 8-inch CMU; default 16 inches on center. |
| D2 | Detail | Doors 1-3/4 inches thick unless noted otherwise; ADA-compliant lever handles; relite glazing and stops on corridor side unless noted otherwise. |
| D3 | Detail | Flooring transitions centered under doors; door/relite frames PT2 unless noted otherwise with architect-approved color; gypsum-board ceilings PT1 unless noted otherwise. |
| D4 | Detail | Matching stud-size top track, one #10 screw at each leg, three #10 screws through no more than two GWB layers at each zee; repair to UL P516; acoustic sealant both sides and perimeter per Section 07900. |
| D5 | Detail | Rectangular duct at 4-foot intervals; round duct with 3/8-inch rods and 1-inch by 1/8-inch two-piece hanger; straps may replace rods when concealed. |
| C1 | Calculation | 115 sheets: 7 + 48 + 13 + 20 + 8 + 19. |
| C2 | Calculation | 511 + 593 = 1,104; the printed 1,103 total is lower by 1. |
| C3 | Calculation | 12 months: 4 + 4 + 4; sequential work, one floor vacated at a time. |
| C4 | Calculation | 222 calendar days: 589 - 367. |
| C5 | Calculation | Punch-list delay $200/day; closing-document delay $100/day. |

## Scoring contract

Each response will be assessed without changing the locked answer key:

- Answer correctness: 0–2.
- Numeric and unit accuracy where applicable: 0–2.
- Source traceability: 0–2.
- Unsupported statements: counted separately and deducted in the summary.
- Failure attribution: parsing/OCR, retrieval, model synthesis, or source/revision ambiguity.

The final report will preserve each CIRP response and its citations, score each question, summarize category-level results, and distinguish an honest insufficient-evidence response from an incorrect supported claim.

## Live run observations before scoring

- All 3 files and all 633 pages reached local parsing.
- CIRP selected 298 pages for visual analysis; 297 completed and one visual result failed its output contract without an automatic paid retry.
- The parser produced 9,881 fragments: 4,962 extracted and 4,919 marked for review.
- The terminal run status was `PARTIAL`; all three documents were represented, but none was classified as a wholly successful file.
- Analysis used 5,678 settled model calls, 8,771,970 input tokens, and 2,291,087 output tokens. The retired cost ledger recorded ¥68.841295. No call was left unresolved.
- End-to-end analysis took about 4 hours 6 minutes: parse 4.1 minutes, vision 23.3 minutes, extraction 124.2 minutes, verification 94.0 minutes, and assembly 0.3 minutes.
- During verification the UI remained at 92% and its ETA increased from about 19.5 to 21.3 minutes while the actual remaining field count fell to zero. The stage-weighted ETA is therefore not a reliable finish estimate.
- The 15 project questions added 15 settled calls: 13 valid responses and two contract errors, with 120,469 input tokens and 3,540 output tokens. No question call was left unresolved and no failed response was automatically retried.
- This call volume and latency are product-level findings. They are not evidence about standalone DeepSeek accuracy.

## CIRP answers and comparison

The direct-PDF result is the locked reference, not another model score. A CIRP answer is considered fully usable only when it answers every requested field or calculation and supplies matching source evidence.

| ID | CIRP result | Correctness | Numeric / units | Traceability | Primary attribution |
| --- | --- | ---: | ---: | ---: | --- |
| S1 | Partial: correct name, job, and shortened address; scope reduced to the title instead of the stated modernization build-out | 1/2 | N/A | 1/2 | Retrieval / synthesis |
| S2 | Insufficient evidence | 0/2 | 0/2 | 0/2 | Retrieval and missing sibling-fragment expansion |
| S3 | Full answer | 2/2 | 2/2 | 2/2 | Pass |
| S4 | Full answer | 2/2 | 2/2 | 2/2 | Pass |
| S5 | Partial: only General 7 and Plumbing 19 | 1/2 | 1/2 | 2/2 | Retrieval / prompt-window truncation |
| D1 | Insufficient evidence | 0/2 | 0/2 | 0/2 | Retrieval / separated schedule fragments |
| D2 | Insufficient evidence | 0/2 | 0/2 | 0/2 | Retrieval / sheet identifier not bound to note fragments |
| D3 | Partial: flooring transition only; both paint requirements missing | 1/2 | N/A | 2/2 | Retrieval / separated note fragments |
| D4 | Response rejected by CIRP evidence contract | 0/2 | 0/2 | 0/2 | Model synthesis / evidence contract, with some source-text loss |
| D5 | Response rejected by CIRP evidence contract | 0/2 | 0/2 | 0/2 | Model synthesis / evidence contract |
| C1 | Insufficient evidence; no arithmetic | 0/2 | 0/2 | 2/2 | Retrieval / prompt-window truncation |
| C2 | Partial: found 593 and printed 1,103, but missed stored L1 value 511 and did not calculate | 1/2 | 1/2 | 2/2 | Retrieval / separated fragments |
| C3 | Insufficient evidence; no arithmetic | 0/2 | 0/2 | 0/2 | Retrieval and missing sibling-fragment expansion |
| C4 | Insufficient evidence; no arithmetic | 0/2 | 0/2 | 0/2 | Retrieval / numeric and section ranking |
| C5 | Partial: retrieved $2,000, 10%, and 5%, but refused deterministic arithmetic | 1/2 | 1/2 | 2/2 | Answer-policy / synthesis |

### Score summary

- Fully usable: 2 of 15 (13.3%).
- Partially correct but incomplete: 5 of 15 (33.3%).
- Failed, insufficient, or rejected: 8 of 15 (53.3%).
- Answer correctness: 9/30 (30.0%).
- Numeric and unit accuracy on the 13 applicable questions: 7/26 (26.9%).
- Source traceability: 15/30 (50.0%).
- Unsupported factual claims: 0. The system generally failed closed instead of inventing missing values.

By category, the only full answers were two project-scope code-analysis questions. Detail questions produced 0/5 full answers. Calculation questions produced 0/5 full answers.

### Root-cause check against the stored CIRP evidence

The benchmark does not support local OCR or text extraction as the dominant cause of these 15 failures. CIRP's database already contained exact or substantially exact source facts that the question retriever did not select:

- All three four-month work-area fragments and the one-floor constraint were stored for S2/C3.
- All six discipline totals were stored for S5/C1.
- Wall framing, door thickness, lever-handle, corridor-side, duct support, and both occupant-load subtotals were stored.
- Section 01100 stored `589 Calendar Days` and `367 Calendar Days` as separate neighboring fragments, but C4 retrieved an unrelated 180-day paragraph instead.

There are real parsing defects—examples include `1nd floor`, noisy drawing-order text, the missing exact `UL P516` phrase, and broken duct-note formatting—but most sampled answer failures occurred after usable text had already been stored. The dominant failure is retrieval: generic terms outrank exact section/sheet/numeric identifiers, related page fragments are not expanded as siblings, and atomic schedules/notes are split into independent evidence rows. A secondary failure is answer policy: C5 had every required number but was instructed too conservatively to perform cited arithmetic. D4 and D5 exposed two evidence-contract failures.

### Product conclusion

Codex direct PDF reading answered all 15 fixed questions from the original files. CIRP plus DeepSeek fully answered 2/15. This is a product-path gap, not a fair basis for concluding that DeepSeek itself is inaccurate. The largest gains should come from fixing evidence construction and retrieval before changing the model:

1. Treat exact sheet numbers, section numbers, paragraph numbers, and numeric literals as hard retrieval signals.
2. Expand a hit to adjacent fragments on the same page and under the same section so lists, schedules, and multi-line notes remain atomic.
3. Preserve cover-sheet indices, wall legends, finish notes, and calculation inputs as structured blocks instead of isolated snippets.
4. Allow deterministic arithmetic over explicitly cited numbers while continuing to prohibit unsupported domain inferences.
5. Record the exact failed answer and validation rule for evidence-contract errors so prompt and contract defects can be corrected without paid retries.

The complete product responses are preserved in `kapolei_cirp_answers.json`; the fixed questions and independent reference remain in `kapolei_cirp_vs_direct_pdf_questions.json` and `kapolei_direct_pdf_answers.json`.
