# Offline field benchmark metrics v2

`scripts/field_benchmark_metrics.py` summarizes independently reviewed offline
records. Its output version is `field-benchmark-metrics-2`. It does not call a
model, query the customer database, decide whether an answer is correct, or write
human approval. Input measurements and judgments are supplied by the caller, not
authenticated by this summary script. It now includes an offline completed-report
adapter, but has no live telemetry collector.

## Required observations and quality denominators

Each record still requires `answerability`, `judgment` and a finite non-negative
`elapsed_seconds`. Existing judgment/scope validation is unchanged. Answerable
and unanswerable questions have separate reviewed denominators; unreviewed
records contribute to review coverage and elapsed time but never count as
correct. Incorrect answers and inappropriate refusals stay in the answerable
reviewed denominator. A successful response format is not a correct-answer
judgment.

End-to-end latency includes every supplied record, including failed/incorrect,
refused and unreviewed cases. Stage durations are independent measurements, not
a replacement for the end-to-end clock. Distributions use the existing mean,
nearest-rank P50/P95 and extrema. Empty distributions have null statistics.
Aggregation that overflows to a non-finite value raises an error rather than
returning infinity, clamping or filling zero. The CLI serializes strict JSON
before writing its output, so an aggregation failure creates no result file.

## Optional numbers: unknown is not zero

The following measurements may be absent or explicitly null:

- `human_minutes`: finite non-negative number, excluding booleans.
- `model_decisions`: non-negative integer, excluding booleans.
- Legacy `supplement_requests` and `supplement_rounds`: non-negative integers.
- `requested_supplement_requests` and `requested_supplement_rounds`.
- `accepted_supplement_requests` and `accepted_supplement_rounds`.

Missing/null is unknown; explicit zero is a known observation. Each number is
summarized separately with these fields:

| Field | Meaning |
|---|---|
| `observed_question_count` | Records with a numeric observation |
| `unknown_question_count` | Records missing the observation, including null |
| `known_zero_question_count` | Observed values equal to zero |
| `known_nonzero_question_count` | Observed values greater than zero |
| `observed_total` | Sum over observed values only; an empty observed set sums to zero |
| `total` | Exact whole-input sum, null if any record is unknown |
| `mean_per_observed_question` | Observed total divided by observed count, null if none |

An `observed_total` of zero with zero observations does **not** establish zero
work. For a nonempty entirely unknown input, `total` and the mean are null. An
empty record list has an exact total of zero and a null mean. The old top-level
`supplement_requests_total` and `human_minutes_total` fields now project the
exact/null total, never the observed subtotal. Consumers must check the report
version before interpreting them.

Legacy supplement counters have caller-defined/version-dependent semantics.
They are marked non-comparable across versions and remain separate from the
four explicit requested/accepted counters. V8 and v9 live-runner legacy aliases
must not be pooled as the same measurement. No field fills another field. When
the corresponding requested and accepted values are both known, accepted must
not exceed requested. The script does not impose a particular loop's per-round
request limit.

Accepted retrieval does not mean new or useful evidence was obtained. This
script does not infer new-evidence counts, current calls, cost or receipt history
from supplement counters. A future data adapter must preserve producer version,
scope and exact/unknown/lower-bound semantics before mapping observations here.

## Cache observation and current execution mode are independent

`cached` is an optional legacy boolean observation. Only false enters the
`fresh` cache-observation group; only true enters `cached`. Missing/null enters
`unknown`. Counts, elapsed sums and latency distributions expose all three
groups and the observed/unknown denominator. These groups alone do not prove
the current request's provider-call count.

`execution_mode` is a separate optional explicit observation, one of `FRESH`,
`CACHED`, `REPLAY`, `MIXED` or `UNKNOWN`. Missing/null becomes `UNKNOWN`.
`execution_mode_counts` and `latency_seconds.by_execution_mode` report all five
modes. Modes are never inferred from `cached`, saved model receipts or results;
the mode also does not backfill a missing `cached` value.

Contradictory input is rejected: `FRESH` with `cached=true`, `CACHED` with
`cached=false`, and `REPLAY` or `MIXED` with either boolean cache value. Replay
and mixed-mode records must omit the binary cache observation or use null. An
explicit replay label is not independently authenticated or converted into a
zero provider-call measurement.

## Minimal synthetic example

```json
{
  "records": [
    {
      "answerability": "ANSWERABLE",
      "judgment": "UNREVIEWED",
      "elapsed_seconds": 2
    },
    {
      "answerability": "ANSWERABLE",
      "judgment": "CORRECT_COMPLETE",
      "elapsed_seconds": 4,
      "cached": false,
      "execution_mode": "FRESH",
      "human_minutes": 1.5,
      "supplement_requests": 2
    }
  ]
}
```

There is one explicit-fresh and one unknown cache observation, not two fresh
executions. There is independently one FRESH and one UNKNOWN execution mode.
The observed human subtotal is 1.5 minutes, but the exact total is null. The
legacy supplement observed subtotal is 2, but its exact total is null. All four
explicit requested/accepted counters stay unknown; they are not copied from 2.
Overall mean elapsed time is still 3 seconds across both records.

The CLI accepts this records envelope and an optional output path. Existing
historical files are not migrated or rewritten; use a new report destination.

## Explicit reference-live-smoke-3 adapter

The same offline CLI also has two explicit modes for a completed named-v9
`reference-live-smoke-3` JSON report:

```text
python scripts/field_benchmark_metrics.py smoke.json --smoke3-review-template --output review-template.json
python scripts/field_benchmark_metrics.py smoke.json --smoke3-review review.json --output adapted.json
```

The template binds the SHA-256 of the **original report bytes** and its fixed
identity: report version, evaluation/project/run/snapshot IDs, selector,
canonical profile ID, and the ordered full `(question_id, item_id)` set. It
contains only `answerability: null`, `judgment: "UNREVIEWED"`, and
`human_minutes: null` review placeholders. There is intentionally no
`review_complete` flag: conversion instead requires every sidecar entry to have
an explicit answerability; `UNREVIEWED` remains a valid judgment.

The conversion rejects a changed report byte hash, noncanonical named profile,
unsafe report policy, unresolved or nonterminal item, missing/extra/reordered
review item, unknown sidecar field, overwrite target, or non-finite JSON. A
review sidecar is caller-supplied and unauthenticated; the output declares
`review_authentication: "CALLER_SUPPLIED_NOT_AUTHENTICATED"` and
`source_authentication: "CALLER_SUPPLIED_FILE_NOT_AUTHENTICATED"`. It does not
read settings, credentials, a database, a service, source text, model answers,
or human identity.

Its `records` still use the unchanged metrics-v2 summary contract. The wrapper
separately labels `elapsed_seconds` as
`RUNNER_PREVIEW_EXECUTE_TO_TERMINAL_CHECK`: a runner wall-clock observation, not
model-only time, field end-to-end time, or complete post-processing. Complete
receipt chains alone contribute saved `model_decisions` and explicit
requested/accepted supplement counters; each four counter scopes is labelled
`SAVED_COMPLETE_CHAIN_ONLY`, and legacy supplement aliases are omitted.
Terminal-only failures leave those counts unknown.

Current execution is kept outside the metrics-v2 counter totals. Exact current
calls are summarized only in `current_execution.current_model_calls` and retained
per item; saved terminal-receipt lower bounds remain per failed item in
`source_observations`, under
`SAVED_TERMINAL_RECEIPT_LOWER_BOUND_NOT_EXACT`, never in an exact sum.
`REPLAY` requires exactly zero current calls. Non-replay exact current calls map
to `CACHED`, `FRESH`, or `MIXED` only relative to complete-chain decisions;
otherwise mode is `UNKNOWN`. This never derives current mode from saved receipt
cache flags. The adapter establishes file consistency only, not server receipt
authenticity, a provider call, human approval, answer quality, cost, actual new
evidence, or release readiness.

This contract correction does not establish any real question's response time,
accuracy, supplement count, human saving, model quality or release readiness.
