# NEXT_STEPS_PLAN.md

Written 2026-11-12, branch `refactor/processor-only`. This is the file-by-file checklist for finishing the Processor. It supersedes the "what to do next" section of `HANDOFF.md`; `refactoring_flags.md` remains the record of the original audit.

## 1. Gap between now and the goal

**Goal:** `main.py ROOT` ingests every supported file under ROOT into the `documents` table (1024-dim normalised bge-m3 vectors, cosine, `ChunkRecord` schema), using CPU worker processes for parsing and chunking and one process for embedding, with nothing crashing on missing metadata and tables kept separable.

| Capability | Now | Gap |
|---|---|---|
| Discovery, routing, metadata | Done, tested | Small hardening items only (sections 3.1, 3.2) |
| Document parsing | Done, tested on fixtures | Untested on real purchased filings; must be made worker-safe |
| Tabular reading | Done, tested | Not called by the indexer; no per-record mode |
| Cleaning | Legacy | Deletes legitimate text; loses page numbers; flattens tables |
| Chunking | Legacy | Character-based, splits tables, non-scoped ids, dict output |
| Embedding | Legacy | Wrong device order, not normalised, global text dedup, hard-coded paths |
| Storage | Legacy | Old table and schema, variable-length vectors, per-file upsert |
| Orchestration | Single process, interim glue | No workers, no embedding process, no document-level dedup |
| Tests / CI | 48 tests on the new modules | Nothing for cleaner, chunker, embedder, store, or a full run; CI workflow empty |

Note on scope: `type_router.py` and `parser.py` were already rewritten in Step 3 and are not broken. They appear below only for the remaining hardening work.

## 2. Order of work

| Step | Content | Sections |
|---|---|---|
| 4 | Cleaner and chunker | 3.4, 3.5 |
| 5 | Embedder and vector store | 3.6, 3.7 |
| 6 | Multiprocess indexer; wire tabular, metadata and `build_record` | 3.8, 3.3, 3.1, 3.2 |
| 7 | Tests, CI, repo cleanup | 3.9 – 3.12 |

Each step ends with the suite green and a review stop. Until Step 6 lands, each step must leave `main.py` runnable.

## 3. File-by-file checklist

### 3.1 `pipeline/type_router.py` (hardening only)

- [ ] Make routing callable from a worker: confirm `open_routed` and `RoutedFile` have no process-global state and that `RoutedFile` pickles (it is a frozen dataclass of paths and strings).
- [ ] Give each worker its own extraction directory. `tempfile.mkdtemp` under `SCRATCH_DIR` is already unique per call; add a test that two processes extracting the same zip do not collide.
- [ ] Remove leftover scratch directories from a crashed run at startup (main process, before workers start).
- [ ] Fail fast with a clear message at startup if libmagic cannot be loaded, instead of an `ImportError` traceback from the first worker.
- [ ] Decide whether plain text (`.txt`, `.md`) should be supported. Today it is reported as unsupported.

### 3.2 `pipeline/metadata.py` (missing-metadata guarantees)

Already never raises for metadata content. Remaining:

- [ ] `compute_doc_id` reads the file and **can** raise `OSError` (unreadable file, permissions). Keep that as a per-file failure, but make sure the worker returns it as a `failed` status with the reason and does not die.
- [ ] `MetadataResolver` caches batch files per directory. Build one resolver per worker process (in the pool initialiser) and confirm the cache is not shared or pickled.
- [ ] Log one summary line per run of how many documents had no `symbol`, no `doc_type` and no `allowed_roles`, so silent metadata gaps are visible.
- [ ] Decide the policy for a document with no `allowed_roles`. It is stored as null today ("no restriction declared"). Confirm with the LLM side how `rag_authz.rego` treats null, and whether a `DEFAULT_ALLOWED_ROLES` setting is needed.
- [ ] Optional: a `doc_type` fallback from `document_template/classifier.py` when the vendor gives none. Otherwise delete the classifier (3.11).
- [ ] Tests: unreadable sidecar (permission error), metadata file that is a directory, non-UTF-8 metadata file.

### 3.3 `pipeline/tabular.py` (tables preserved for SQL later)

- [ ] Wire `read_table_chunks` into the indexer for `HANDLER_TABULAR` files (3.8). Pass a token-based `size_fn` from the chunker instead of the 3-characters-per-token estimate.
- [ ] Keep every table chunk traceable to its source rows. Already present in chunk metadata: `sheet`, `row_start`, `row_end`, `columns`. Add `table_id` = stable id per table (`doc_id` + sheet, or `doc_id` + ordinal for tables inside documents) so all chunks of one table can be selected together.
- [ ] **Decision needed: per-record mode.** When a file's header contains financial column names (`trade_date`, `close`, `roe_pct`, ...), emit one chunk per row and put that row's values in `Chunk.fields`, so the typed columns hold real values. Otherwise per-row columns stay null for row-group chunks. Proposed rule: per-record when at least 3 header names match `EXTENDED_FIELDS` and the header also has an instrument key (`isin`, `nse_symbol` or `symbol`); else row groups. Guard with a setting (`TABULAR_RECORD_MODE = auto | off`).
- [ ] If per-record mode is adopted: map header names with the same case-insensitive matching as `metadata.py`, run values through `coerce_extended`, and write the row's text as `key: value` lines rather than a one-row Markdown table.
- [ ] A row that fails to parse must not abort the file: count it, add one warning with the count, continue.
- [ ] Surface `warnings` (truncation, encoding fallback, repaired headers) to the indexer's per-file result.
- [ ] Note for the later relational route, no code now: with `content_kind="table"`, `table_id`, sheet, row range and column names stored, a future job can re-read the original file for exactly those rows. Do not store raw cell values in LanceDB.
- [ ] Tests: per-record mode fills `close`/`trade_date`; bad row is counted; `table_id` is stable across runs.

### 3.4 `pipeline/cleaner.py` (rewrite)

- [ ] New entry point: `clean_blocks(blocks: list[Block], page_count: int | None) -> tuple[list[Block], list[str]]` returning kept blocks and a log of what was removed. Pure function, no file I/O, no settings paths.
- [ ] Delete: `run`, `_clean_numeric`, `_clean_document`, `_clean_json_file`, `_is_valid_date_format`, `render_blocks_to_text`, the `TRANS_*`/`CLEANED_*` imports, and the dict-based `clean_ir_blocks`.
- [ ] Repeated-boilerplate removal:
  - only for documents with at least 8 pages;
  - only `paragraph` blocks shorter than 80 characters;
  - only when the text appears on at least 40% of pages;
  - never `header`, `table`, `list_item`, `caption`, `footnote` or `code` blocks;
  - never text that is purely a value ("Nil", "N.A.", "-", numbers), since those repeat legitimately.
- [ ] Skip the repetition pass entirely when `page_number` is `None` (HTML, DOCX).
- [ ] Denylist: keep the regex entries (`Page \d+ of \d+`, rule lines), add bare page numbers, drop the literal entries that never match ("Confidential", "Copyright"). Move the list to settings.
- [ ] Whitespace normalisation for `paragraph`, `header`, `caption`, `footnote` only. Leave `table`, `list_item` and `code` untouched.
- [ ] Do not emit `[[PAGE:n]]` markers. `page_number` stays on the block.
- [ ] Table blocks pass through with their `TableData` intact.
- [ ] Tests: a 3-page document keeps a line repeated on every page; "Nil" repeated on 10 pages is kept; a real running footer on a 10-page document is removed and appears in the removal log; table and code whitespace unchanged.

### 3.5 `pipeline/chunker.py` (rewrite)

- [ ] New entry point: `chunk_document(blocks: list[Block], *, size_fn, max_tokens, overlap_tokens) -> list[Chunk]`. Pure, no class-level state, no torch import.
- [ ] Token counting: load the bge-m3 tokenizer with the `tokenizers` package (`Tokenizer.from_pretrained(EMBEDDING_MODEL)`), which does not import torch. Add `tokenizers` as an explicit dependency. Create it once per worker in the pool initialiser. Verify it works offline from the Hugging Face cache.
- [ ] Prose: walk blocks in order, maintaining the heading stack. Start a new chunk at a level-1 or level-2 heading and when adding a block would exceed `CHUNK_SIZE_TOKENS`. A single block longer than the limit is split on sentence boundaries, then on whitespace.
- [ ] Overlap: carry the last `CHUNK_OVERLAP_TOKENS` of prose into the next chunk within the same section only. No overlap across sections or into tables.
- [ ] Prefix each prose chunk's text with its heading breadcrumb only if a retrieval check shows it helps; default off. `section_path` is always set as metadata.
- [ ] Tables: each `BLOCK_TABLE` goes to `tabular.table_chunks(table, size_fn=..., section_path=..., page_range=...)` and yields `content_kind="table"` chunks. A table is never merged into a prose chunk. A caption directly before or after a table is attached to the first table chunk.
- [ ] `page_range`: from the minimum and maximum `page_number` of the blocks in the chunk; `None` when no block has a page. Never the string `"unknown"`.
- [ ] `Chunk.index`: sequential across prose and table chunks of the document, starting at 0.
- [ ] Do not compute `chunk_id` here; `build_record` derives it. Delete `_generate_chunk_id`, `_global_chunk_id`, `_extract_page_range`, `PAGE_MARKER_RE`, `run`, `_chunk_file`.
- [ ] Remove `langchain-text-splitters` from `pyproject.toml` once nothing imports it.
- [ ] Empty document (no blocks after cleaning) returns `[]`; the indexer reports it as `empty`, not `failed`.
- [ ] Tests: no chunk exceeds the token limit; table header repeated and no row split; `page_range` correct for a chunk spanning two pages and `None` for HTML; same input gives identical chunks twice; `section_path` follows nested headings.

### 3.6 `pipeline/embedder.py` (rewrite)

- [ ] `resolve_device() -> str`: `EMBED_DEVICE` if set, else `cuda` if available, else `mps` if available, else `cpu`. Log the choice once. No module-level cache.
- [ ] `class Embedder`: loads `SentenceTransformer(EMBEDDING_MODEL, device=...)` in `__init__`, sets `max_seq_length` to `CHUNK_SIZE_TOKENS` plus a small margin, asserts the reported dimension equals `EMBEDDING_DIM`.
- [ ] `encode(texts: list[str]) -> np.ndarray` (float32, shape `n × 1024`):
  - `normalize_embeddings=True`;
  - sort by length, encode in batches of `EMBED_BATCH_SIZE`, restore the original order;
  - on an out-of-memory error, halve the batch size and retry down to 1, then raise;
  - `show_progress_bar=False`;
  - assert no NaN in the output.
- [ ] Half precision only when `EMBED_FP16` is true and the device is `cuda`. Leave MPS in float32.
- [ ] On MPS, call `torch.mps.empty_cache()` every N batches to limit memory growth; make N a setting.
- [ ] Delete: `get_existing_hashes` and all chunk-level dedup, `LANCEDB_PATH`/`LANCEDB_TABLE` constants, the old `Embedder.run`/`_embed_file`/`_generate_dummy_embedding`, the `__main__` block, every `print`.
- [ ] The module must be importable without loading the model. Only the embedding process constructs `Embedder`.
- [ ] Tests (with a tiny stub model injected, no download): order preserved after length sorting; vectors unit-length; OOM backoff path; device resolution order with availability mocked.
- [ ] One slow test (`RUN_SLOW=1`) that loads the real model and checks dimension 1024 and unit norm.

### 3.7 `pipeline/vector_store.py` (rewrite)

- [ ] `class VectorStore(path=LANCEDB_PATH, table=LANCEDB_TABLE)` with one connection for the run.
- [ ] `open_or_create()`: create from `ChunkRecord` when the table is missing. Treat only "table not found" as create. If the table exists with a different schema, stop with a clear error naming the differing columns; do not write.
- [ ] `existing_doc_ids(doc_ids) -> set[str]`: query in bounded batches with validated hex ids (ids are `[0-9a-f]{32}`; reject anything else before building the filter).
- [ ] `add(records: list[dict]) -> int`: `table.add` on an Arrow table built from the `ChunkRecord` schema, so a mismatch fails before the write. Return the number of rows written.
- [ ] `delete_doc(doc_id)`: used to remove a partially written document after a failure.
- [ ] `finalize()` once per run: create scalar indexes on `doc_id` and `symbol`; create the vector index with `VECTOR_INDEX_KWARGS` (cosine) when the row count is above the minimum the index type needs, otherwise skip and log. Optionally `optimize()` to compact fragments.
- [ ] Keep only `count()` and `get_by_doc_id()` for verification. Delete `search`, `get_by_id`, `get_by_symbol`, `get_metadata`, `CompanyDocument`, the pandas import, `merge_insert`.
- [ ] No upsert by default. If a replace mode is ever needed, implement it as `delete_doc` + `add`.
- [ ] Tests: create and reopen; schema mismatch is refused; missing optional fields insert as null; `existing_doc_ids`; `delete_doc`; cosine nearest neighbour after `finalize()` on a small table.

### 3.8 `pipeline/indexer.py` (multiprocess orchestration)

Process map:

| Process | Count | Device | Does | Must not |
|---|---|---|---|---|
| Main | 1 | CPU | Discover; dedup by `doc_id`; start processes; build records; write to LanceDB; account per file; finalise indexes | Import torch or load any model |
| Parse/chunk worker | `NUM_WORKERS` | CPU | `open_routed`, `build_source_document`, parse, clean, chunk, or `read_table_chunks` | Touch MPS/CUDA; load bge-m3 |
| Embedder | 1 | `cuda → mps → cpu` | Own bge-m3; batch texts; return vectors | Parse, or write to LanceDB |

Queues (all from `multiprocessing.get_context("spawn")`):

| Queue | From → to | Bounded | Message |
|---|---|---|---|
| tasks | main → workers | No (paths only) | file path |
| chunks | workers → embedder | Yes, `EMBED_QUEUE_MAX_BATCHES` | `(file_key, SourceDocument, list[Chunk])`, at most `EMBED_BATCH_SIZE` chunks per message |
| results | embedder → main | Yes | `(file_key, SourceDocument, list[Chunk], float32 matrix)` |
| status | workers → main | No | `(file_key, status, reason, chunks_emitted, warnings)` |

Checklist:

- [ ] Call `multiprocessing.get_context(MP_START_METHOD)` and use that context for every process and queue. Do not call the global `set_start_method` from library code; `main.py` may set it under `if __name__ == "__main__"`.
- [ ] Start workers and the embedder **before** anything imports torch in the main process. Move the `Parser`, `Cleaner`, chunker and embedder imports out of the indexer's module top level and into the worker functions.
- [ ] Worker initialiser: set `OMP_NUM_THREADS=1` and `torch.set_num_threads(PARSER_THREADS)`; create the tokenizer and a `MetadataResolver`; create the `Parser` lazily on the first document file, so a worker that only sees tabular files never loads docling.
- [ ] Worker task, per file: route → for each routed item build the `SourceDocument` → skip if its `doc_id` is in the set of known ids passed at start → produce chunks → put them on the chunks queue in messages of at most `EMBED_BATCH_SIZE` → send one status message. Wrap everything in `try/except` so a worker reports `failed` and moves on.
- [ ] Tabular files stream: a worker puts chunk messages as rows are read, never holding a whole large file's chunks in memory. The bounded queue provides backpressure.
- [ ] Embedder loop: collect messages, concatenate texts across documents up to `EMBED_BATCH_SIZE`, encode once, split the matrix back per message, put on results. This is the batching requirement: no per-chunk or per-file encode calls.
- [ ] Main loop: for each result build rows with `build_record` (one `ingest_run_id` and `ingested_at` per run), buffer per `doc_id`, and write a document's rows when its status says all chunks were emitted and all have arrived.
- [ ] Atomicity rule: "`doc_id` present in the table" must mean "document complete". Write a document in a single `table.add` when it fits a row cap (setting, for example 50 000 rows). For larger documents write in parts, and on any failure call `delete_doc`. Decide how to detect a hard kill between parts (proposal: store `chunk_count` in the last row's `metadata_json`, or keep a small run ledger file).
- [ ] Document-level dedup: before starting, load existing `doc_id`s (or check per batch) and pass them to workers. Also dedup within the run: two paths with the same bytes produce one document; the second is reported as `duplicate`.
- [ ] Per-file statuses: `indexed`, `skipped_duplicate`, `skipped_unsupported`, `empty`, `failed`. Only `failed` sets exit code 1. Remove the `n == 0` means failure rule.
- [ ] Shutdown: one sentinel per worker on the tasks queue; workers send a final "done"; after all workers are done, a sentinel to the embedder; main drains results, then calls `VectorStore.finalize()`.
- [ ] Failure handling: if the embedder process dies, stop the run with a clear error (nothing can be embedded). If a worker dies (segfault in a native parser), mark its in-flight file `failed`, start a replacement worker, continue. Use a per-file timeout for parsing.
- [ ] `KeyboardInterrupt`: terminate children, remove scratch directories, exit 1 without leaving partial documents (apply the atomicity rule).
- [ ] Single-process mode for tests and debugging (`NUM_WORKERS=0`): same code path functions called inline with a stub or real embedder.
- [ ] Memory check on the M4 Pro (48 GB): each worker that parses PDFs holds docling's layout and table models. Measure resident memory per worker and cap `NUM_WORKERS` accordingly; unified memory is shared with the MPS embedder.
- [ ] Delete `_index_file` and the imports of the legacy cleaner, chunker, `embed_chunks` and old `VectorStore`.
- [ ] Update `pipeline/pipeline.py`: summary keys for the new statuses; `_validate_result` adjusted.
- [ ] Tests: end-to-end run on a small mixed tree with a stub embedder (documents and tabular files land in `documents` with the right `content_kind`); second run writes 0 rows and exits 0; a failing file does not block others; a file with no metadata ingests with nulls; duplicate bytes at two paths ingest once; spawn mode works under pytest.

### 3.9 `config/settings.py`

- [ ] Delete the `LEGACY` block and its import-time `mkdir` loop once cleaner and chunker no longer import it. Remove the stale `data/raw`, `data/transient`, `data/cleaned`, `data/chunked`, `data/done` directories.
- [ ] Add: `TABULAR_RECORD_MODE`, `WRITE_MAX_ROWS_PER_ADD`, `PARSE_TIMEOUT_SECONDS`, `MPS_EMPTY_CACHE_EVERY`, cleaner thresholds and denylist, optional `DEFAULT_ALLOWED_ROLES`.
- [ ] Allow `NUM_WORKERS=0` (inline mode).

### 3.10 `main.py` and `pipeline/pipeline.py`

- [ ] Put the entry point under `if __name__ == "__main__"` semantics required by `spawn` (already the case) and make sure importing `main` has no side effects.
- [ ] Generate `ingest_run_id` in `main.py`, include it in the log format, and print it in the summary.
- [ ] Optional flags: `--workers N`, `--dry-run` (discover and route only, print counts by type and unsupported reasons). The dry run is useful for a first look at a new vendor delivery.
- [ ] Summary: counts per status and per `source_type`.

### 3.11 Repo cleanup

- [ ] Delete `pipeline/normalizer.py`. Delete `pipeline/document_template/` unless the fallback classifier is adopted (3.2).
- [ ] Untrack `data/pipeline.log`, `data/16 log 2nd run`, `data/log run 14- 20 junly`, and `.DS_Store` files.
- [ ] `.gitignore`: add `data/`, keep `.DS_Store`; remove entries for directories that no longer exist.
- [ ] Delete or regenerate `project-structure.txt` (it lists the removed crawler tree).
- [ ] `.vscode/launch.json` passes `--all-symbols`, which the CLI no longer accepts. Change the args to a sample ROOT.
- [ ] `pyproject.toml`: add `tokenizers`; remove `langchain-text-splitters`; check whether `pandas` and `tabulate` are still needed after `vector_store.py` stops using pandas (the parser's table export may still need them).

### 3.12 Tests, CI, docs

- [ ] New test files: `test_cleaner.py`, `test_chunker.py`, `test_embedder.py`, `test_vector_store.py`, `test_indexer_e2e.py`, `test_idempotency.py`.
- [ ] `.github/workflows/pipeline-test.yml`: install libmagic, `uv sync`, `ruff check`, `pytest` (fast tests only, stub embedder, no model downloads).
- [ ] Run the pipeline on a sample of real purchased files before declaring done. Check specifically: header/footer handling on long filings, scanned pages, Hindi text (RapidOCR does not cover it), very wide tables, and large CSVs.
- [ ] Small retrieval check (a handful of questions with known answers) to tune `CHUNK_SIZE_TOKENS` and `CHUNK_OVERLAP_TOKENS`.
- [ ] Update `README.md` status table and `Doc/current/04_processing_stages.md` as each legacy module is replaced.

## 4. Decisions needed from you

| # | Decision | Needed by | Recommendation |
|---|---|---|---|
| 1 | Per-record chunks for price/fundamentals files (3.3) | Step 6 | Yes, in `auto` mode. Otherwise most of the 50 columns stay null for tabular data |
| 2 | Types of the 50 columns, especially dates as strings | Before first production load | Confirm with the LLM side |
| 3 | Meaning of null `allowed_roles` in the OPA policy (3.2) | Step 6 | Confirm; add a default only if null means "deny" |
| 4 | Keep the regex `doc_type` classifier as a fallback, or delete it | Step 7 | Delete; store null |
| 5 | Support `.txt`/`.md` input | Step 6 | Only if a vendor delivers them |
| 6 | How to guarantee complete documents after a hard kill on very large files (3.8) | Step 6 | Single `add` per document with a row cap, plus `chunk_count` check for oversized ones |

## 5. Definition of done

- `main.py ROOT` on a mixed tree writes `documents` rows that validate against `ChunkRecord`, with `content_kind` set and vectors of unit length.
- A second run over the same tree writes nothing and exits 0.
- A file with no metadata, a corrupt file, and an unsupported file each produce the right status and do not affect other files.
- Table chunks carry `table_id`, sheet, row range and columns.
- The embedding model is loaded in exactly one process, and no encode call is made per chunk or per file.
- The same command runs unchanged on MPS, CUDA and CPU.
- No legacy module, legacy setting, or `print` remains under `pipeline/`.
- Fast tests and `ruff` pass in CI.
