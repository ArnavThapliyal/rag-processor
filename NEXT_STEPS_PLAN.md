# NEXT_STEPS_PLAN.md

Updated 2026-11-16, branch `refactor/processor-only`. Steps 4 to 7 of the refactor are implemented. This file records what was built, the choices made on the open decisions, and what is left before a production load.

## 1. Where things stand

`main.py ROOT` ingests every supported file under ROOT into the `documents` table (normalised 1024-dim bge-m3 vectors, cosine, `ChunkRecord` schema), using CPU worker processes for parsing and chunking and one process for embedding.

| Step | Content | Commit subject |
|---|---|---|
| 4 | Cleaner | Rewrite cleaner as a pure function over parser blocks |
| 4 | Chunker | Rewrite chunker: token-sized, structure-aware chunks from blocks |
| 5 | Embedder | Rewrite embedder: normalised vectors, cuda->mps->cpu, batched with OOM backoff |
| 5 | Vector store | Rewrite vector store on the ChunkRecord schema; append-only with crash recovery |
| 6 | Tabular | Tabular: per-record chunks for price/fundamentals files, table ordinals, tolerant rows |
| 6 | Indexer | Multiprocess indexer: CPU parse/chunk workers and a single embedding process |
| 7 | Cleanup, CI | Remove orphaned modules and unused dependencies; add CI workflow |

Each row is one commit, so any step can be reverted on its own with `git revert <hash>` (find hashes with `git log --oneline`).

## 2. How the three requirements were met

### Missing metadata never crashes a run

- `metadata.py` returns null for anything missing or malformed and keeps unknown vendor keys in `metadata_json`.
- `worker.py` wraps each file: an unreadable file, a corrupt archive or a parser error becomes a `failed` result with a reason; the worker carries on.
- Each run logs how many indexed files had no `symbol`, `doc_type` or `allowed_roles`, so gaps are visible.
- Tests: `test_metadata.py`, and the end-to-end tests ingest files with no metadata at all.

### Tables are preserved for a later SQL store

- Tables are never merged into prose. Every table chunk has `content_kind="table"`.
- `metadata_json` on every table chunk carries `table_id` (`<doc_id>:<ordinal>`), `table_ordinal`, `sheet`, `row_start`, `row_end`, and `columns` or `caption` where they apply.
- Rows are never split and the header is repeated in every row-group chunk.
- Price and fundamentals files are emitted one chunk per row with typed columns filled (record mode).

### MPS/CPU process mapping

| Process | Count | Device | Work |
|---|---|---|---|
| Main | 1 | CPU | Accounting, `build_record`, LanceDB writes, index build. Never imports torch |
| Parse/chunk worker | `NUM_WORKERS` | CPU | Route, metadata, parse, clean, chunk, tabular streaming |
| Embedder | 1 | `cuda → mps → cpu` | Holds bge-m3; batches texts across files |

- Start method is `spawn`. The embedding model is loaded in the embedding process only.
- Both queues are bounded, which gives backpressure.
- The embedder makes one encode call per accumulated batch, never one per chunk or per file.

## 3. Decisions taken (change any of them if you disagree)

| # | Decision | What was implemented | How to change it |
|---|---|---|---|
| 1 | Per-record chunks for price/fundamentals files | **On** (`auto`): a sheet whose header names at least 3 financial columns becomes one chunk per row | `TABULAR_RECORD_MODE=off`, or raise `TABULAR_RECORD_MIN_MATCHES` |
| 2 | Types of the 50 columns | Unchanged: floats, four ints, two bools, dates as ISO strings | Edit `ChunkRecord` and `EXTENDED_FIELDS`, bump `SCHEMA_VERSION`, load into a new table |
| 3 | Null `allowed_roles` | Stored as null ("no restriction declared"); no default is applied | Needs a decision from the LLM side, see section 4 |
| 4 | Regex `doc_type` classifier | Deleted. `doc_type` comes from vendor metadata or is null | Restore from git history if a fallback is wanted |
| 5 | `.txt` / `.md` input | Not supported; reported as `skipped_unsupported` | Add the types to `type_router.py` and a plain-text path in `worker.py` |
| 6 | Complete documents after a hard kill | One atomic write per document; documents above `WRITE_MAX_ROWS_PER_ADD` rows use a ledger that the next run cleans up | `WRITE_MAX_ROWS_PER_ADD` |

Decision 1 has a cost: record mode stores one vector per row. A price file with a million rows becomes a million rows in LanceDB and a correspondingly long embedding run. If these files are large, consider whether they belong in the vector store at all, or only in the later relational store.

## 4. What is left

### Needs you or the LLM side

- [ ] **`allowed_roles` semantics.** Confirm how `tests/rag_authz.rego` treats a document whose `allowed_roles` is null. If null must mean "deny", add a `DEFAULT_ALLOWED_ROLES` setting applied in `metadata.py`.
- [ ] **Column types.** Confirm the types of the 50 columns, especially dates as strings, before the first production load. Changing them later means reloading.
- [ ] **Table name.** The table is `documents` (was `company_documents`). The LLM side must read the new name and search with `metric("cosine")`.
- [ ] **Record mode.** Confirm decision 1 above against the size of the real price and fundamentals files.

### Validation on real data

- [ ] Run `main.py <real delivery> --dry-run` first and review the unsupported list.
- [ ] Run a sample of real purchased files and inspect the chunks. Check specifically:
  - running header/footer removal on long filings (the cleaner log lists what it removed);
  - scanned pages and the OCR budget;
  - Hindi text: RapidOCR covers English and Chinese only;
  - very wide tables: a single row longer than `EMBED_MAX_SEQ_LENGTH` tokens is truncated at embedding;
  - heading levels from docling, which decide `section_path` and chunk boundaries.
- [ ] Measure memory per PDF worker on the M4 Pro and set `NUM_WORKERS` accordingly. Each PDF worker loads docling's layout and table models, and unified memory is shared with the MPS embedder.
- [ ] Measure embedding throughput on MPS and tune `EMBED_BATCH_SIZE`.
- [ ] A small retrieval check (a handful of questions with known answers) to tune `CHUNK_SIZE_TOKENS` and `CHUNK_OVERLAP_TOKENS`.

### Engineering follow-ups (not blocking)

- [ ] `is_known` in workers reads the table as it was when the worker started. Duplicates within a run are caught by the main process, so this is only wasted work, not a correctness issue.
- [ ] Two runs at the same time against the same database and scratch directory are not supported: each clears the scratch directory and the ledger at start.
- [ ] The worker stall timeout is activity-based. A single file that legitimately parses for longer than `WORKER_STALL_TIMEOUT_SECONDS` (30 minutes) would be killed; raise the setting for very large scanned documents.
- [ ] No position information reaches the cleaner, so running headers are detected by repetition only, not by their place on the page.
- [ ] `.github/workflows/pipeline-test.yml` has not run on GitHub yet; check the first run.
- [ ] Merge `refactor/processor-only` into `main` once the real-data run is satisfactory.

## 5. Definition of done

| Criterion | State |
|---|---|
| `main.py ROOT` on a mixed tree writes `documents` rows that validate against `ChunkRecord`, with `content_kind` set and unit-length vectors | Met (tests) |
| A second run over the same tree writes nothing and exits 0 | Met (tests) |
| A file with no metadata, a corrupt file and an unsupported file each get the right status without affecting others | Met (tests) |
| Table chunks carry `table_id`, sheet, row range and columns | Met (tests) |
| The embedding model is loaded in exactly one process; no per-chunk or per-file encode calls | Met |
| The same command runs unchanged on MPS, CUDA and CPU | Device selection is tested; only MPS and CPU machines were available to run on |
| No legacy module, legacy setting or `print` remains under `pipeline/` | Met |
| Fast tests and `ruff` pass | Met locally; CI not yet observed |
| Verified on real purchased data | **Not done** |
