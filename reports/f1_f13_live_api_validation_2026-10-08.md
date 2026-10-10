# F1-F13 post-repair live API validation

Date: 2026-10-08 Hawaii time

## Scope and controls

- Source data was copied into an isolated disposable data directory. The
  original `.local/cirp.sqlite3` was not opened by the test service and retained
  its original 2026-10-04 modification time and 610,615,296-byte length.
- The fixed Kapolei 15-question set, one run, one snapshot, one ordered question
  hash and `literal-page-selector-9` were used for both current model profiles.
- Provider: official DeepSeek endpoint. Profiles: `deepseek-flash` and
  `deepseek-v4-pro`, both text-only with thinking disabled.
- No automatic retry, model fallback, customer-database migration, deployment,
  human-review write or unresolved-call reconciliation occurred.
- Quality verdicts below are an assistant comparison against the existing
  independently locked direct-PDF answer key. They are not an engineer's
  approval or a write to CIRP's human-adjudication tables.

## QA V2 live-path finding

All 15 QA V2 previews reached `RETRIEVAL_READY`, with 29-96 evidence blocks and
31,650-42,000 source-text bytes per question. The first eight actual ask
attempts were rejected locally before HTTP because the complete serialized
evidence plus prompt exceeded the configured 64 KB input envelope. Every
response explicitly said no API call was made; the unresolved-call list stayed
empty. This is a real product-path blocker, not a provider/model failure.

Consequently, the new F2 object/unit/condition relation guard is regression-
tested but the fixed Kapolei corpus cannot yet measure its live quality effect
through QA V2. The preview should expose the actual serialized request upper
bound, and the evidence bundle must be made relation-complete but smaller before
another paid QA V2 benchmark.

## Same-input Reference v9 live result

| Measure | V4.1 Flash | V4 Pro | Pro minus Flash |
|---|---:|---:|---:|
| Contract-valid terminal outcomes | 10/15 | 14/15 | +4 |
| Fully usable against locked answer key | 6/15 | 11/15 | +5 |
| Partial | 2/15 | 1/15 | -1 |
| Unusable semantic answer | 1/15 | 2/15 | +1 |
| Failed or safe cannot-answer | 6/15 | 1/15 | -5 |
| Questions with inaccurate/unsupported content | 2 | 2 | 0 |
| Mean item elapsed time | 10.400 s | 15.305 s | +4.905 s |
| Median item elapsed time | 10.406 s | 13.541 s | +3.135 s |
| Maximum / nearest-rank P95 | 17.430 s | 38.913 s | +21.483 s |
| New provider calls in this session | 11 | 11 | 0 |
| Reused settled outcomes | 4 | 4 | 0 |
| Unresolved calls at completion | 0 | 0 | 0 |

The release rule requires 15/15 contract-valid, at least 12/15 fully usable,
and zero unsupported factual claims. Neither profile passes. Pro is the clear
same-input winner for completeness, but it remains one fully usable result short
and produced two semantic failures that the structural contract accepted.

## Question-level adjudication

| ID | Flash | Pro | Main observation |
|---|---|---|---|
| S1 | FULLY_USABLE | FULLY_USABLE | Identity, address and modernization scope complete. |
| S2 | PARTIAL | PARTIAL | Both propagate the parser/OCR typo `1nd floor`; durations are otherwise complete. |
| S3 | FULLY_USABLE | FULLY_USABLE | Building G facts remain correctly scoped. |
| S4 | FULLY_USABLE | FULLY_USABLE | Building I facts remain correctly scoped. |
| S5 | FULLY_USABLE | FULLY_USABLE | All six discipline counts are correct. |
| D1 | UNUSABLE | FULLY_USABLE | Flash assigns code 1 to paint on CMU instead of the 3-5/8-inch stud. |
| D2 | FAILED | FULLY_USABLE | Pro recovers thickness, ADA lever and corridor-side condition. |
| D3 | FAILED | UNUSABLE | Pro says PT3 and an architect-approved ceiling color; locked requirements are PT2 and PT1. |
| D4 | PARTIAL | UNUSABLE | Both omit or mis-bind fastening details; Pro also links furring/LVF wording incorrectly. |
| D5 | FULLY_USABLE | FULLY_USABLE | Spacing, rod, hanger and strap substitute are complete. |
| C1 | FULLY_USABLE | FULLY_USABLE | Both compute 115; each used two model decisions. |
| C2 | CANNOT_ANSWER | FAILED | Neither publishes the required 511 + 593 = 1,104 comparison. |
| C3 | FAILED | FULLY_USABLE | Pro computes 12 months and preserves the one-floor-at-a-time constraint. |
| C4 | FAILED | FULLY_USABLE | Pro computes 589 - 367 = 222. |
| C5 | FAILED | FULLY_USABLE | Pro computes $200/day and $100/day correctly. |

The most important remaining safety gap is that exact citation/numeric checks
can still accept the wrong requirement relationship. D3 selected the wrong
paint values, while D4 combined nearby drawing notes into an unsupported
attachment statement. This is a retrieval-and-relation problem, not merely a
larger-model problem.

## Historical comparison boundary

The historical selector-v7 same-input report recorded Flash at 9/15 contract-
valid and 7/15 fully usable, and Pro at 12/15 contract-valid and 9/15 fully
usable, with zero unsupported flags. The current v9 Pro result is +2 fully
usable and +2 contract-valid versus that history, but current inputs and code
are different, so this is observational improvement rather than an isolated
F1-F13 causal measurement. The current Flash result is one fully usable answer
lower than the historical run.

## Usage and estimated provider charge

The current session made 22 new provider calls; eight other evaluation outcomes
were authenticated replays. Provider-reported new-call usage:

| Profile | Prompt tokens | Cache hit | Cache miss | Completion tokens |
|---|---:|---:|---:|---:|
| V4.1 Flash | 966,688 | 789,244 | 177,444 | 3,391 |
| V4 Pro | 938,271 | 25,344 | 912,927 | 3,840 |

All new calls occurred before 06:00 UTC, in DeepSeek's published off-peak
window. Applying the official 2026-10-08 CNY rates gives an estimated charge of
¥0.2068 for Flash and ¥4.1638 for Pro, ¥4.3706 total. CIRP's local
`actual_units` field is zero and is not the provider bill, so this remains a
token-based estimate. The cost comparison is strongly order/cache biased:
Flash ran second and received far more cache hits; it is not an intrinsic
twenty-times price ratio.

Pricing reference:
`https://api-docs.deepseek.com/zh-cn/quick_start/pricing/`

## Recommended next change

1. Do not switch the general product Q&A to QA V2 yet. First expose serialized
   request size in preview and reduce the final evidence envelope below the
   existing limit without lowering source coverage.
2. Reuse the selector's page routing, but send relation-complete note/table
   blocks rather than hundreds of flat fragments. Preserve file/page/sheet,
   row/column or note-group identity and the complete conditional clause.
3. Extend F2-style source-local property, object, unit and condition binding to
   the Reference V3 validator. D3 and D4 prove that exact quotes and numeric
   support alone are not enough.
4. Use Pro for detail and calculation questions when the user explicitly
   accepts cost. Flash remains suitable for low-risk project identity and
   inventory questions, but should not be treated as the quality default for
   the current 15-question gate.
5. Repeat the same frozen 15 only after the relation guard and compact context
   pass offline atom-coverage checks; do not spend another model run merely to
   tune wording.

## Local evidence

- `reports/local/f1-f13-live-api-2026-10-08/repair/reference-v9-pro-live.json`
- `reports/local/f1-f13-live-api-2026-10-08/repair/reference-v9-flash-live.json`
- `reports/local/f1-f13-live-api-2026-10-08/repair/qa-v2-pro-live.json`
- `reports/kapolei_direct_pdf_answers.json`
- `reports/kapolei_reference_v7_flash_pro_2026-10-03.json` in the source checkout
