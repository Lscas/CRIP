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
