# Canonical Storage V2

## Outcome

CIRP no longer treats fixed-size `evidence` rows as the only representation of project knowledge. A local canonical document graph now preserves hierarchy, exact source spans, spatial bounds, typed identifiers and deterministic content hashes. SQLite remains the durable local container, while full-text and spatial indexes are rebuildable projections rather than the source of truth.

This is an offline storage migration foundation. It does not make a provider call, modify the source database, delete source objects, overwrite human review decisions or automatically switch the running application to a rebuilt database.

## Data authority

The authority order is:

1. Original uploaded object bytes and their recorded SHA-256 identity.
2. Immutable parser evidence and exact source offsets/bounds.
3. Canonical hierarchy, identifiers and edges derived deterministically from that evidence.
4. Rebuildable FTS5 and R*Tree projections used for retrieval.
5. Model vision narration, which may be retained only as non-source context and cannot become an exact quotation or engineering fact.

Human review events, provider-call records and the existing evidence ledger remain separate authoritative records. The canonical graph does not rewrite them.

## Graph model

`content_nodes` stores deterministic nodes for `DOCUMENT`, `PAGE`, `SHEET`, `SECTION`, `PARAGRAPH`, `TEXT_BLOCK` and `VISUAL_CONTEXT`. Parent IDs preserve the parsed document hierarchy. Every node has a stable content hash and belongs to one project, run and document.

`content_spans` links source-text nodes back to one immutable evidence row and keeps exact character offsets plus optional page coordinates. `content_edges` provides explicit typed graph relationships. `content_identifiers` normalizes supported Sheet, Paragraph, specification Section, RFI, Submittal, Detail, Article and Clause values without replacing their printed form. `content_assertions` is reserved for bounded derived or reviewed statements and requires provenance and status rather than silently promoting model output to fact.

Detected table structure is retained without inventing a new semantic fact type. A table is a `SECTION` container, each row is a `PARAGRAPH` container, and its source `TEXT_BLOCK` keeps `structure_kind`, table/row numbers, cell count and the parser's exact cell text-map offsets and boxes. An explicit `Table N row M` locator remains structural metadata and is not misfiled as a Paragraph identifier. Explicit five-digit legacy specification Sections such as `SECTION 01100` are supported only when labelled; ordinary five-digit values are not promoted to Sections.

`canonical_builds` records each run's build state, source fingerprint, counts and safe failure category. A run is eligible for canonical retrieval only after it reaches `READY` and passes validation.

## Retrieval

Exact typed identifiers are resolved before ordinary term ranking. Different requested identifier types must intersect along the same source-node ancestor path, while alternative values of the same type remain alternatives; for example, `Section 01100` plus `Paragraph 1.04` cannot retrieve Paragraph 1.04 under Section 01200. The identifier's structural scope is expanded only to descendant source-text nodes from the same project, run and document. A matched table-row anchor may add at most 24 sibling rows from the same run, document, page and table so one discipline or count cell does not hide the rest of a bounded schedule. Ordinary terms use `content_search` when FTS5 is available and fall back to the existing complete deterministic evidence scan when it is not.

`content_search` indexes only source text and locator paths. `VISUAL_CONTEXT` is deliberately excluded. `content_bounds` indexes source boxes when R*Tree is available; bounds remain stored in `content_spans` even without the extension.

The existing evidence table remains the exact citation authority during this migration stage. Returned quotations still use its raw text and offsets, so the stronger graph cannot manufacture a citation.

## Offline staging rebuild

Run the rebuild from the repository root with two different database paths:

```powershell
.\.venv\Scripts\python.exe scripts\rebuild_canonical_v2.py `
  --source C:\path\to\cirp.sqlite3 `
  --output C:\path\to\cirp-v2-staging.sqlite3
```

The source is opened read-only. SQLite creates a consistent snapshot in a uniquely named staging file, applies the schema migrations, rebuilds every run that has document results, validates the graph, checks foreign keys and integrity, checkpoints the staging WAL and then atomically publishes only the requested output file. The command refuses identical source/output paths. Replacing an existing staging output additionally requires `--replace`.

There is intentionally no automatic cutover and no delete operation. Before a future cutover, keep the source database, compare project/run/document/evidence/review/call counts, exercise fixed question sets against both databases and retain a rollback path.

## Validation and failure behavior

A canonical build fails closed when it finds cross-project references, orphan parents or edges, invalid source spans, source-text nodes without spans, or a mismatch between the durable nodes and an available FTS5/R*Tree projection. The failed run is marked `FAILED` with only a safe exception class and is not used for canonical retrieval. Existing evidence retrieval remains available.

Rebuilding the same unchanged run is idempotent: deterministic node IDs, hashes and counts remain stable. Newly completed parser output is synchronized locally before extraction begins, so no provider request is needed to construct the graph.

## Current limits

- The first release projects the current immutable parser evidence into the graph. It does not yet reconstruct every native CAD/BIM object relationship, cross-sheet callout or specification dependency.
- Sheet identity currently requires one explicit native-text label in a bounded drawing title-block region. Detectable vector tables retain rows and cells, but borderless tables, cross-page tables and complete multi-column order remain future work.
- Local semantic embeddings and vector search are not part of this change. Exact identifiers, hierarchy and SQLite full-text search are the initial retrieval routes.
- The rebuild command produces a validated staging database only. Production cutover, rollback automation and removal of legacy projections require a separate approved task.
- Vision narration stays contextual and cannot satisfy an exact-evidence contract.
